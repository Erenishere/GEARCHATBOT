"""Inspect a small Gear MongoDB schema sample with a verified read-only user.

Run with ``python -m scripts.inspect_gear_db`` after setting
GEAR_READONLY_DB_URL and GEAR_DB_NAME.
No document, ObjectId, credential, or unrestricted field value is printed.
"""

import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from dotenv import load_dotenv


READ_ACTIONS = frozenset({
    "changeStream", "checkMetadataConsistency", "collStats", "dbHash", "dbStats",
    "find", "indexStats", "killCursors", "listCollections", "listDatabases",
    "listIndexes", "listSearchIndexes", "planCacheRead",
})
COLLECTION_ALIASES = {
    "cars": {"car", "cars", "vehicle", "vehicles"},
    "car_types": {"cartype", "cartypes", "carcategory", "carcategories"},
    "brands": {"brand", "brands"},
    "locations": {"location", "locations", "defaultlocation", "defaultlocations"},
    "deals": {"deal", "deals", "offer", "offers", "promotion", "promotions"},
}
SAFE_EXAMPLE_FIELDS = {
    "fuel", "fueltype", "transmission", "transmissiontype", "seats",
    "seatcount", "withdriver", "driveroption", "currency",
}
SAFE_EXAMPLE_TEXT = re.compile(r"^[A-Za-z0-9 _-]{1,32}$")
SAFE_FIELD_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_ -]{0,47}$")
OBJECT_ID_KEY = re.compile(r"^[0-9a-fA-F]{24}$")
MAX_DOCUMENTS = 20
MAX_FIELDS = 120


class InspectionError(Exception):
    """The inspection cannot safely proceed."""


def require_read_only(auth_info: dict) -> list[dict]:
    """Fail closed unless MongoDB reports only recognized read privileges."""
    if not auth_info.get("authenticatedUsers"):
        raise InspectionError("The MongoDB connection is not authenticated.")
    roles = auth_info.get("authenticatedUserRoles")
    privileges = auth_info.get("authenticatedUserPrivileges")
    if not isinstance(roles, list) or not roles or not isinstance(privileges, list) or not privileges:
        raise InspectionError("Could not verify effective MongoDB privileges.")
    actions = {action for privilege in privileges
               for action in privilege.get("actions", [])}
    if "find" not in actions or actions - READ_ACTIONS:
        role_names = ", ".join(sorted({str(role.get("role", "unknown")) for role in roles}))
        raise InspectionError(
            f"Credential has non-read or unverified privileges (roles: {role_names}). "
            "Use a dedicated read-only MongoDB user."
        )
    return roles


def database_name(uri: str, roles: list[dict], configured: str | None) -> str:
    if configured:
        return configured
    path_name = urlsplit(uri).path.strip("/")
    if path_name:
        return path_name
    role_databases = {role.get("db") for role in roles
                      if role.get("role") == "read"
                      and role.get("db") not in {None, "admin", "local", "config"}}
    if len(role_databases) == 1:
        return role_databases.pop()
    raise InspectionError("Set GEAR_DB_NAME; the MongoDB URL has no database name.")


def collection_group(name: str) -> str | None:
    normalized = re.sub(r"[^a-z0-9]", "", name.casefold())
    return next((group for group, aliases in COLLECTION_ALIASES.items()
                 if normalized in aliases), None)


def summarize_documents(documents: list[dict]) -> dict:
    fields: dict[str, dict] = defaultdict(lambda: {"types": set(), "seen": 0,
                                                    "examples": set()})

    def visit(value: object, path: str, depth: int = 0) -> None:
        if not path or len(fields) >= MAX_FIELDS and path not in fields:
            return
        entry = fields[path]
        kind = type(value).__name__
        entry["types"].add(kind)
        entry["seen"] += 1
        key = re.sub(r"[^a-z0-9]", "", path.rsplit(".", 1)[-1].casefold())
        if key in SAFE_EXAMPLE_FIELDS:
            if isinstance(value, str) and SAFE_EXAMPLE_TEXT.fullmatch(value):
                entry["examples"].add(value)
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                entry["examples"].add(value)
            elif isinstance(value, bool):
                entry["examples"].add(value)
        if depth >= 3:
            return
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                if (isinstance(child_key, str) and SAFE_FIELD_NAME.fullmatch(child_key)
                        and not OBJECT_ID_KEY.fullmatch(child_key)):
                    visit(child_value, f"{path}.{child_key}", depth + 1)
        elif isinstance(value, list):
            for child_value in value[:2]:
                visit(child_value, f"{path}[]", depth + 1)

    for document in documents:
        for key, value in document.items():
            if (isinstance(key, str) and SAFE_FIELD_NAME.fullmatch(key)
                    and not OBJECT_ID_KEY.fullmatch(key)):
                visit(value, key)
    return {
        "sampled_documents": len(documents),
        "fields": {
            path: {"types": sorted(entry["types"]), "seen": entry["seen"],
                   **({"examples": sorted(entry["examples"], key=str)[:8]}
                      if entry["examples"] else {})}
            for path, entry in sorted(fields.items())
        },
    }


def inspect(client, uri: str, configured_database: str | None,
            check_access_only: bool = False) -> dict:
    auth_info = client.admin.command({"connectionStatus": 1,
                                      "showPrivileges": True})["authInfo"]
    roles = require_read_only(auth_info)
    name = database_name(uri, roles, configured_database)
    report = {"access": "read-only verified", "database": name}
    if check_access_only:
        return report

    database = client[name]
    names = sorted(database.list_collection_names())
    report["collections"] = names
    report["inspected"] = {}
    for collection_name in names:
        group = collection_group(collection_name)
        if group is None:
            continue
        cursor = database[collection_name].find({}, max_time_ms=5000).limit(MAX_DOCUMENTS)
        report["inspected"][collection_name] = {
            "group": group,
            **summarize_documents(list(cursor)),
        }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-access", action="store_true",
                        help="verify permissions without reading collection metadata or documents")
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    uri = os.getenv("GEAR_READONLY_DB_URL", "")
    if not uri.startswith(("mongodb://", "mongodb+srv://")):
        raise SystemExit("Set a valid read-only MongoDB URL in GEAR_READONLY_DB_URL.")
    try:
        from pymongo import MongoClient
        from pymongo.errors import PyMongoError
    except ImportError:
        raise SystemExit("Install development dependencies: python -m pip install -r requirements-dev.txt") from None
    try:
        with MongoClient(uri, serverSelectionTimeoutMS=10000, connectTimeoutMS=10000,
                         socketTimeoutMS=10000, retryWrites=False,
                         appname="gear-ai-schema-inspector") as client:
            report = inspect(client, uri, os.getenv("GEAR_DB_NAME"), args.check_access)
    except InspectionError as exc:
        raise SystemExit(str(exc)) from None
    except PyMongoError as exc:
        raise SystemExit(f"MongoDB connection or read failed ({type(exc).__name__}).") from None
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
