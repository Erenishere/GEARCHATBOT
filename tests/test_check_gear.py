"""Checks for the standalone Gear API diagnostic."""

from types import SimpleNamespace

import httpx
import pytest

from app import check_gear


@pytest.fixture
def configured_probe(monkeypatch):
    settings = SimpleNamespace(
        gear_api_base_url="https://gearapp.ae:5001",
        gear_api_prefix="/Gear/api",
        request_timeout=3,
    )
    monkeypatch.setattr(check_gear, "get_settings", lambda: settings)
    return monkeypatch


def test_probe_rejects_html_page_even_when_http_status_is_200(configured_probe):
    configured_probe.setattr(
        check_gear.httpx, "get",
        lambda *args, **kwargs: httpx.Response(200, text="<html>GearUp</html>"),
    )

    with pytest.raises(SystemExit, match="Response is not JSON"):
        check_gear.main()


def test_probe_accepts_mappable_vehicle_json(configured_probe, capsys):
    configured_probe.setattr(
        check_gear.httpx, "get",
        lambda *args, **kwargs: httpx.Response(200, json={"data": {"cars": [
            {"_id": "507f1f77bcf86cd799439011", "title": "Gear car"},
        ]}}),
    )

    check_gear.main()

    assert "Vehicle mapping: OK (1 returned)" in capsys.readouterr().out
