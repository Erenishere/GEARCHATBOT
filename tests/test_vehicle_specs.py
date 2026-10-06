import asyncio

from app.schemas.chat import ChatRequest, VehicleCard
from app.security.sessions import SessionStore
from app.services.conversation import ConversationService
from tests.test_chat_flow import FakeExtractor, FakeGear, extraction


def test_seat_question_uses_selected_car_not_generic_guidance():
    urus = VehicleCard(
        id="507f1f77bcf86cd799439011", title="Lamborghini Urus", location="Dubai", seats=5,
    )
    gear = FakeGear([urus])
    service = ConversationService(
        FakeExtractor([
            extraction(location="Dubai"),
            extraction(intent="details", selected_index=1),
            extraction(intent="vehicle_specs", selected_index=1),
        ]),
        gear,
        SessionStore(1800),
    )

    async def scenario():
        first = await service.chat(ChatRequest(message="Cars in Dubai"), None)
        await service.chat(ChatRequest(message="View option 1", session_id=first.session_id), None)
        answer = await service.chat(
            ChatRequest(message="how many seat this car has?", session_id=first.session_id),
            None,
        )
        assert answer.status == "details"
        assert "5 seats" in answer.message
        assert "find a rental" not in answer.message.lower()

    asyncio.run(scenario())


def test_greeting_gets_opening_not_generic_guidance():
    service = ConversationService(FakeExtractor([extraction()]), FakeGear(), SessionStore(1800))
    response = asyncio.run(service.chat(ChatRequest(message="hello"), None))
    assert response.status == "guidance"
    assert "find the right rental" in response.message
    assert response.suggestions
