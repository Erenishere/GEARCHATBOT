"""Customer catalog inspection never returns token or catalog values."""

import asyncio

import pytest

from app.clients.gear import GearAuthorizationRequired
from scripts.inspect_gear_catalog import inspect_catalog


class FakeGear:
    def __init__(self):
        self.calls = []

    async def filter_catalog(self, token):
        self.calls.append(token)
        return {"data": {"car_types": [{"_id": "secret-id", "name": "SUV"}]}}


def test_catalog_inspection_requires_customer_token():
    gear = FakeGear()
    with pytest.raises(GearAuthorizationRequired):
        asyncio.run(inspect_catalog(None, gear))
    assert gear.calls == []


def test_catalog_inspection_returns_shape_without_values():
    gear = FakeGear()
    shape = asyncio.run(inspect_catalog("customer-token", gear))
    assert gear.calls == ["customer-token"]
    assert shape["data"]["car_types"]["type"] == "array"
    assert shape["data"]["car_types"]["first_item_shape"] == {"_id": "str", "name": "str"}
    assert "SUV" not in str(shape)
    assert "secret-id" not in str(shape)
    assert "customer-token" not in str(shape)
