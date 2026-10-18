"""Tests for the DynamoDB-backed saved-loadout store, against moto's in-memory
DynamoDB rather than a real table -- keeps the "no test reaches a real external
API" rule intact (CLAUDE.md), same story as the LLM's TestModel fixture.
"""

from __future__ import annotations

import threading

import boto3
import pytest
from moto import mock_aws

from tf2_loadout.loadouts import DynamoLoadoutStore, LoadoutStoreError, _pk, _sk

TABLE_NAME = "tf2-loadout-test-main"
REGION = "us-west-2"


@pytest.fixture
def store():
    with mock_aws():
        client = boto3.client("dynamodb", region_name=REGION)
        client.create_table(
            TableName=TABLE_NAME,
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield DynamoLoadoutStore(TABLE_NAME, region=REGION)


def test_each_thread_gets_its_own_table(store):
    assert store._table() is store._table()

    other: list[object] = []
    thread = threading.Thread(target=lambda: other.append(store._table()))
    thread.start()
    thread.join()
    assert other[0] is not store._table()


async def test_store_ops_build_the_table_on_the_worker_thread(store, monkeypatch):
    seen: list[int] = []
    real = store._table

    def spy():
        seen.append(threading.get_ident())
        return real()

    monkeypatch.setattr(store, "_table", spy)
    await store.list("s1")
    assert seen
    assert threading.get_ident() not in seen


async def test_dynamo_failure_raises_loadout_store_error():
    with mock_aws():
        s = DynamoLoadoutStore("missing", region=REGION)
        with pytest.raises(LoadoutStoreError):
            await s.list("s1")
        with pytest.raises(LoadoutStoreError):
            await s.create("s1", "A", "Spy", [1])


async def test_create_returns_the_saved_loadout(store):
    created = await store.create("s1", "Cop look", "Spy", [1, 2, 3])
    assert created.name == "Cop look"
    assert created.cls == "Spy"
    assert created.defindexes == (1, 2, 3)
    assert created.created_at == created.updated_at


async def test_defindexes_survive_as_plain_ints_not_decimal(store):
    created = await store.create("s1", "A", "Spy", [1, 2])
    listed = (await store.list("s1"))[0]
    assert all(type(d) is int for d in listed.defindexes)


async def test_list_is_creation_ordered(store):
    a = await store.create("s1", "A", "Spy", [1])
    b = await store.create("s1", "B", "Spy", [2])
    assert [l.id for l in await store.list("s1")] == [a.id, b.id]


async def test_list_scoped_to_steam_id(store):
    await store.create("s1", "A", "Spy", [1])
    assert await store.list("s2") == []


async def test_update_name_and_items(store):
    created = await store.create("s1", "A", "Spy", [1])
    updated = await store.update("s1", created.id, name="B", defindexes=[2, 3])
    assert updated.name == "B"
    assert updated.cls == "Spy"  # untouched field survives
    assert updated.defindexes == (2, 3)
    assert updated.updated_at >= created.updated_at

    listed = await store.list("s1")
    assert len(listed) == 1
    assert listed[0].name == "B"


async def test_update_missing_loadout_returns_none(store):
    assert await store.update("s1", "does-not-exist", name="x") is None


async def test_update_scoped_to_steam_id(store):
    created = await store.create("s1", "A", "Spy", [1])
    assert await store.update("s2", created.id, name="stolen") is None


async def test_update_racing_delete_returns_none_without_creating_a_row(store, monkeypatch):
    created = await store.create("s1", "A", "Spy", [1])
    sk = await store._find_sk("s1", created.id)
    await store.delete("s1", created.id)

    async def stale_find_sk(steam_id, loadout_id):
        return sk

    with monkeypatch.context() as m:
        m.setattr(store, "_find_sk", stale_find_sk)
        assert await store.update("s1", created.id, name="x") is None
    assert await store.list("s1") == []


async def test_list_skips_malformed_rows(store):
    valid = await store.create("s1", "A", "Spy", [1])
    table = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)
    table.put_item(Item={"PK": _pk("s1"), "SK": _sk(1, "ghost"), "updatedAt": 1, "name": "x"})
    assert [l.id for l in await store.list("s1")] == [valid.id]


def _seed_oversize(n: int) -> None:
    # Full rows, so Commit 2's malformed-row skip doesn't drop them. 4 x 300 KB is
    # past the 1 MB page moto (like DynamoDB) cuts a Query at.
    table = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)
    for i in range(n):
        table.put_item(
            Item={
                "PK": _pk("s1"),
                "SK": _sk(1000 + i, f"id{i}"),
                "id": f"id{i}",
                "name": "x" * 300_000,
                "cls": "Spy",
                "defindexes": [1],
                "createdAt": 1000 + i,
                "updatedAt": 1000 + i,
            }
        )


async def test_list_follows_pagination_past_one_mb(store):
    _seed_oversize(4)
    assert [l.id for l in await store.list("s1")] == ["id0", "id1", "id2", "id3"]


async def test_update_finds_a_loadout_on_a_later_page(store):
    _seed_oversize(4)
    updated = await store.update("s1", "id3", name="y")
    assert updated is not None
    assert updated.name == "y"


async def test_delete(store):
    created = await store.create("s1", "A", "Spy", [1])
    assert await store.delete("s1", created.id) is True
    assert await store.list("s1") == []


async def test_delete_missing_returns_false(store):
    assert await store.delete("s1", "does-not-exist") is False


async def test_delete_scoped_to_steam_id(store):
    created = await store.create("s1", "A", "Spy", [1])
    assert await store.delete("s2", created.id) is False
    assert len(await store.list("s1")) == 1
