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
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Protocol

import anyio
import boto3
from boto3.dynamodb.conditions import Key

_SK_PREFIX = "LOADOUT#"


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
        self._table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    async def list(self, steam_id: str) -> list[SavedLoadout]:
        def query() -> list[dict]:
            resp = self._table.query(
                KeyConditionExpression=(
                    Key("PK").eq(_pk(steam_id)) & Key("SK").begins_with(_SK_PREFIX)
                )
            )
            return resp.get("Items", [])

        items = await anyio.to_thread.run_sync(query)
        return [_from_item(i) for i in items]

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

        def put() -> None:
            self._table.put_item(Item=item)

        await anyio.to_thread.run_sync(put)
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
        names = {"#u": "updatedAt"}
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

        def do_update() -> dict:
            resp = self._table.update_item(
                Key={"PK": _pk(steam_id), "SK": sk},
                UpdateExpression="SET " + ", ".join(sets),
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
                ReturnValues="ALL_NEW",
            )
            return resp["Attributes"]

        attrs = await anyio.to_thread.run_sync(do_update)
        return _from_item(attrs)

    async def delete(self, steam_id: str, loadout_id: str) -> bool:
        sk = await self._find_sk(steam_id, loadout_id)
        if sk is None:
            return False

        def do_delete() -> None:
            self._table.delete_item(Key={"PK": _pk(steam_id), "SK": sk})

        await anyio.to_thread.run_sync(do_delete)
        return True
