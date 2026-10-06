"""Regression tests for the AI-session/Gear-authentication boundary."""

import asyncio
import logging

import httpx

from app.api.v1.chat import get_chat_service
from app.clients.llm import Extraction
from app.main import app
from app.schemas.chat import VehicleCard
from app.security.sessions import SessionStore
from app.services.conversation import ConversationService


CARD = VehicleCard(id="507f1f77bcf86cd799439011", title="Verified Gear car")


def extraction(**updates):
    values = {
        "intent": "search", "selected_index": None, "reset_filters": False,
        "needs_dates": False, "needs_location": False,
        "unsupported_constraints": [], "location": None, "search": None,
        "from_date": None, "to_date": None, "from_time": None, "to_time": None,
        "min_price": None, "max_price": None,
    }
    values.update(updates)
    return Extraction.model_validate(values)


class Extractor:
    def __init__(self):
        self.responses = iter([
            extraction(location="Dubai"),
            extraction(intent="details", selected_index=1),
        ])

    async def extract(self, message, current, result_titles):
        return next(self.responses)


class CredentialBoundaryGear:
    """Record only whether the protected call received auth, never its value."""

    def __init__(self):
        self.public_call_had_token = None
        self.protected_call_had_token = False

    async def search_cars(self, filters, token):
        self.public_call_had_token = token is not None
        return [CARD]

    async def car_details(self, car_id, token):
        self.protected_call_had_token = bool(token)
        return CARD


def test_bearer_is_transient_and_does_not_own_or_enter_ai_session(caplog):
    raw_bearer = "temporary-gear-credential-for-regression-test"
    gear = CredentialBoundaryGear()
    sessions = SessionStore(1800)
    service = ConversationService(Extractor(), gear, sessions)

    async def override_service():
        return service

    app.dependency_overrides[get_chat_service] = override_service
    caplog.set_level(logging.INFO, logger="gear_ai.requests")

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            guest = await client.post("/api/v1/agent/chat", json={
                "message": "Cars in Dubai",
            })
            session_id = guest.json()["session_id"]
            authenticated = await client.post("/api/v1/agent/chat", json={
                "message": "Show option 1 details", "session_id": session_id,
            }, headers={"Authorization": f"Bearer {raw_bearer}"})

        assert authenticated.status_code == 200
        assert authenticated.json()["session_id"] == session_id
        assert authenticated.json()["status"] == "details"
        assert raw_bearer not in authenticated.text
        assert gear.public_call_had_token is False
        assert gear.protected_call_had_token is True

        session = sessions.get_or_create(session_id)
        assert session.public_token == session_id
        assert raw_bearer not in repr(session)
        assert raw_bearer not in repr(sessions.__dict__)

    try:
        asyncio.run(scenario())
    finally:
        app.dependency_overrides.clear()

    request_logs = "\n".join(record.getMessage() for record in caplog.records)
    assert raw_bearer not in request_logs
    assert "Authorization" not in request_logs
