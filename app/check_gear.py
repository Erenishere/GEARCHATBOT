"""Check the configured public Gear listing route without an LLM or login."""

from urllib.parse import urlparse

import httpx

from app.clients.gear import GearUnavailable, vehicle_cards
from app.config import get_settings


def main() -> None:
    settings = get_settings()
    base = settings.gear_api_base_url
    parsed = urlparse(base)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path or parsed.query or parsed.fragment:
        raise SystemExit("Set GEAR_API_BASE_URL to the HTTPS host before checking Gear.")
    url = f"{base}{settings.gear_api_prefix}/getCars"
    print(f"Checking {url}?page=1&limit=1")
    try:
        response = httpx.get(url, params={"page": "1", "limit": "1"},
                             timeout=settings.request_timeout, follow_redirects=False)
    except httpx.RequestError as exc:
        raise SystemExit(f"Connection failed: {type(exc).__name__}") from None

    print(f"HTTP status: {response.status_code}")
    print(f"Content type: {response.headers.get('content-type', 'unknown')}")
    try:
        payload = response.json()
    except ValueError:
        raise SystemExit("Response is not JSON; check GEAR_API_BASE_URL and GEAR_API_PREFIX.") from None
    if not isinstance(payload, dict):
        raise SystemExit("JSON root is not an object; check the Gear API route.")
    print("Top-level keys: " + ", ".join(sorted(payload.keys())))
    data = payload.get("data")
    print("data shape: " + type(data).__name__)
    if isinstance(data, dict):
        print("data keys: " + ", ".join(sorted(data.keys())))
    if not response.is_success:
        raise SystemExit(f"Gear API returned HTTP {response.status_code}.")
    try:
        cars = vehicle_cards(payload)
    except GearUnavailable:
        raise SystemExit("Vehicle mapping: response shape needs inspection with the Gear developer.") from None
    print(f"Vehicle mapping: OK ({len(cars)} returned)")


if __name__ == "__main__":
    main()
