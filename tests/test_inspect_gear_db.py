"""The production schema inspector must reject broad credentials first."""

import pytest

from scripts.inspect_gear_db import (InspectionError, database_name, inspect,
                                     require_read_only, summarize_documents)


def auth_info(role: str, actions: list[str]) -> dict:
    return {
        "authenticatedUsers": [{"user": "reader", "db": "admin"}],
        "authenticatedUserRoles": [{"role": role, "db": "gear"}],
        "authenticatedUserPrivileges": [{
            "resource": {"db": "gear", "collection": ""},
            "actions": actions,
        }],
    }


def test_admin_credential_is_rejected_before_collection_access():
    class AdminDatabase:
        def command(self, *_args):
            return {"authInfo": auth_info("atlasAdmin", ["find", "insert", "dropDatabase"])}

    class Client:
        admin = AdminDatabase()

        def __getitem__(self, _name):
            raise AssertionError("No collection access is allowed for admin credentials")

    with pytest.raises(InspectionError, match="non-read"):
        inspect(Client(), "mongodb+srv://example.test/gear", "gear")


def test_read_role_is_accepted_and_can_identify_its_database():
    info = auth_info("read", ["find", "listCollections", "collStats"])
    roles = require_read_only(info)
    assert database_name("mongodb+srv://example.test/", roles, None) == "gear"


def test_schema_summary_never_includes_ids_or_private_field_values():
    summary = summarize_documents([{
        "_id": "secret-id", "owner_email": "owner@example.com",
        "fuel_type": "Petrol", "seats": 5,
        "location": {"address": "Private address"},
        "owner@example.com": {"notes": "Private notes"},
    }])
    assert summary["fields"]["fuel_type"]["examples"] == ["Petrol"]
    assert summary["fields"]["seats"]["examples"] == [5]
    output = str(summary)
    assert "secret-id" not in output
    assert "owner@example.com" not in output
    assert "Private address" not in output
    assert "owner@example.com" not in output
