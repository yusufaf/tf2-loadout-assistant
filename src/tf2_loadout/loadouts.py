"""DynamoDB-backed store for saved loadouts on a signed-in player's Steam account.

Single-table design mirroring the Quizaroni / team-builder convention exactly: `PK`/`SK`
string keys, `PAY_PER_REQUEST` billing, `PK2`/`SK2` + `PK3`/`SK3` GSIs (see `cdk/`,
unused so far but kept for convention -- empty GSIs cost nothing). A loadout's key is
`USER#<steamid64>` / `LOADOUT#<createdAt>#<uuid>`, which makes a plain `begins_with`
Query the entire access pattern -- no GSI needed for the one thing this store does.

boto3's resource layer is wrapped in ``anyio.to_thread.run_sync`` rather than using
aioboto3/aiobotocore: loadout CRUD is low-frequency, and aiobotocore pins botocore
tightly enough to become a recurring resolver headache for a hobby app. boto3 also
returns every number as ``Decimal`` on read -- ``defindexes`` is coerced back to
plain ``int`` here so a ``Decimal`` never leaks past this module into the JSON API.

boto3 resources aren't thread-safe and anyio runs each call on a pooled worker thread,
so every worker gets its own Table (built from one shared, lock-guarded Session). Any
boto3/botocore failure leaves this module as ``LoadoutStoreError``.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

import anyio
import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)

T = TypeVar("T")

_SK_PREFIX = "LOADOUT#"


class LoadoutStoreError(Exception):
    """DynamoDB failed (throttled, unreachable, misconfigured); the message is for logs, never the response."""


@dataclass(frozen=True)
class SavedLoadout:
    id: str
    name: str
    cls: str
    defindexes: tuple[int, ...]
    created_at: int  # epoch milliseconds
    updated_at: int  # epoch milliseconds


class LoadoutStore(Protocol):
    async def list(self, steam_id: str) -> list[SavedLoadout]: ...

    async def create(
        self, steam_id: str, name: str, cls: str, defindexes: list[int]
    ) -> SavedLoadout: ...

    async def update(
        self,
        steam_id: str,
        loadout_id: str,
        *,
        name: str | None = None,
        cls: str | None = None,
        defindexes: list[int] | None = None,
    ) -> SavedLoadout | None: ...

    async def delete(self, steam_id: str, loadout_id: str) -> bool: ...


def _pk(steam_id: str) -> str:
    return f"USER#{steam_id}"


def _sk(created_at: int, loadout_id: str) -> str:
    # Zero-padded so lexicographic SK order matches creation order -- 13 digits of
    # epoch milliseconds is good through the year 2286.
    return f"{_SK_PREFIX}{created_at:013d}#{loadout_id}"


def _from_item(item: dict) -> SavedLoadout:
    return SavedLoadout(
        id=item["id"],
        name=item["name"],
        cls=item["cls"],
        defindexes=tuple(int(d) for d in item["defindexes"]),
        created_at=int(item["createdAt"]),
        updated_at=int(item["updatedAt"]),
    )


class DynamoLoadoutStore:
    def __init__(self, table_name: str, *, region: str | None = None) -> None:
        self._table_name = table_name
        self._session = boto3.session.Session(region_name=region)
        self._session_lock = threading.Lock()
        self._local = threading.local()

    def _table(self) -> Any:
        # Built from one shared Session (not thread-safe itself, hence the lock) because
        # a fresh Session reloads the service model (~200-300 ms) while a warm one
        # builds a resource in ~2 ms.
        table = getattr(self._local, "table", None)
        if table is None:
            with self._session_lock:
                table = self._session.resource("dynamodb").Table(self._table_name)
            self._local.table = table
        return table

    async def _run(self, op: Callable[[Any], T]) -> T:
        def call() -> T:
            return op(self._table())

        try:
            return await anyio.to_thread.run_sync(call)
        except (ClientError, BotoCoreError) as exc:
            raise LoadoutStoreError(str(exc)) from exc

    async def list(self, steam_id: str) -> list[SavedLoadout]:
        def query(table: Any) -> list[dict]:
            resp = table.query(
                KeyConditionExpression=(
                    Key("PK").eq(_pk(steam_id)) & Key("SK").begins_with(_SK_PREFIX)
                )
            )
            return resp.get("Items", [])

        loadouts: list[SavedLoadout] = []
        for item in await self._run(query):
            try:
                loadouts.append(_from_item(item))
            except (KeyError, TypeError, ValueError):
                # A row left half-written (e.g. by an update that raced a delete) must
                # not take the whole list down with it.
                logger.warning("skipping malformed loadout row %s", item.get("SK"))
        return loadouts

    async def create(
        self, steam_id: str, name: str, cls: str, defindexes: list[int]
    ) -> SavedLoadout:
        now = int(time.time() * 1000)
        loadout_id = str(uuid.uuid4())
        item = {
            "PK": _pk(steam_id),
            "SK": _sk(now, loadout_id),
            "id": loadout_id,
            "name": name,
            "cls": cls,
            "defindexes": list(defindexes),
            "createdAt": now,
            "updatedAt": now,
        }

        def put(table: Any) -> None:
            table.put_item(Item=item)

        await self._run(put)
        return _from_item(item)

    async def _find_sk(self, steam_id: str, loadout_id: str) -> str | None:
        """A loadout id alone isn't a full sort key (it also carries `createdAt`), so
        update/delete look the item up by id first. A player's saved-loadout list is
        small and never paginated, so a full Query here is cheap.
        """
        for loadout in await self.list(steam_id):
            if loadout.id == loadout_id:
                return _sk(loadout.created_at, loadout_id)
        return None

    async def update(
        self,
        steam_id: str,
        loadout_id: str,
        *,
        name: str | None = None,
        cls: str | None = None,
        defindexes: list[int] | None = None,
    ) -> SavedLoadout | None:
        sk = await self._find_sk(steam_id, loadout_id)
        if sk is None:
            return None

        now = int(time.time() * 1000)
        names = {"#pk": "PK", "#u": "updatedAt"}
        values: dict[str, object] = {":u": now}
        sets = ["#u = :u"]
        if name is not None:
            names["#n"] = "name"
            values[":n"] = name
            sets.append("#n = :n")
        if cls is not None:
            names["#c"] = "cls"
            values[":c"] = cls
            sets.append("#c = :c")
        if defindexes is not None:
            names["#d"] = "defindexes"
            values[":d"] = list(defindexes)
            sets.append("#d = :d")

        def do_update(table: Any) -> dict | None:
            # UpdateItem is an upsert: without the condition, a delete landing between
            # _find_sk and here would resurrect the row as a stub with no id.
            try:
                resp = table.update_item(
                    Key={"PK": _pk(steam_id), "SK": sk},
                    UpdateExpression="SET " + ", ".join(sets),
                    ConditionExpression="attribute_exists(#pk)",
                    ExpressionAttributeNames=names,
                    ExpressionAttributeValues=values,
                    ReturnValues="ALL_NEW",
                )
            except ClientError as exc:
                if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                    return None
                raise
            return resp["Attributes"]

        attrs = await self._run(do_update)
        return None if attrs is None else _from_item(attrs)

    async def delete(self, steam_id: str, loadout_id: str) -> bool:
        sk = await self._find_sk(steam_id, loadout_id)
        if sk is None:
            return False

        def do_delete(table: Any) -> None:
            table.delete_item(Key={"PK": _pk(steam_id), "SK": sk})

        await self._run(do_delete)
        return True
