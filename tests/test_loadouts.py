"""Tests for the DynamoDB-backed saved-loadout store, against moto's in-memory
DynamoDB rather than a real table -- keeps the "no test reaches a real external
API" rule intact (CLAUDE.md), same story as the LLM's TestModel fixture.
"""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from tf2_loadout.loadouts import DynamoLoadoutStore

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
