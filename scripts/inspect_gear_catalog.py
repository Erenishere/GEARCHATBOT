"""Inspect the authenticated Gear filter catalog without printing its values.

Use a dedicated customer test token entered at the terminal prompt. The token
is sent solely through GearClient's Authorization header and is never printed.
"""

import asyncio
import getpass
import json
import re
import sys

from app.clients.gear import GearAuthorizationRequired, GearClient, GearUnavailable
from app.config import ConfigurationError, get_settings


SAFE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_ -]{0,47}$")
OBJECT_ID_KEY = re.compile(r"^[0-9a-fA-F]{24}$")


def response_shape(value: object, depth: int = 0) -> object:
    """Return bounded field names and types, never IDs, labels, or records."""
    if isinstance(value, dict):
        if depth >= 5:
            return "object"
        return {key: response_shape(item, depth + 1)
                for key, item in list(value.items())[:60]
                if isinstance(key, str) and SAFE_KEY.fullmatch(key)
                and not OBJECT_ID_KEY.fullmatch(key)}
    if isinstance(value, list):
        return {"type": "array", "first_item_shape":
                response_shape(value[0], depth + 1) if value else "empty"}
    return type(value).__name__


async def inspect_catalog(token: str | None, gear: GearClient) -> object:
    if not token:
        raise GearAuthorizationRequired()
    return response_shape(await gear.filter_catalog(token))


def main() -> None:
    settings = get_settings()
    if not sys.stdin.isatty():
        raise SystemExit("Run catalog inspection in a terminal to enter a Gear test token securely.")
    token = getpass.getpass("Gear test Bearer token: ").strip()
    if not token:
        raise SystemExit("A Gear customer test token is required for catalog inspection.")
    try:
        shape = asyncio.run(inspect_catalog(token, GearClient(settings)))
    except GearAuthorizationRequired:
        raise SystemExit("Gear rejected the customer test token; catalog inspection is unavailable.") from None
    except (GearUnavailable, ConfigurationError):
        raise SystemExit("Gear catalog inspection is unavailable; check the configured API host.") from None
    print(json.dumps(shape, indent=2))


if __name__ == "__main__":
    main()
