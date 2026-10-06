"""Socket-free local conversation tester: python -m app.demo_cli."""

import asyncio

from app.api.v1.chat import _build_chat_service
from app.config import get_settings
from app.schemas.chat import ChatRequest


def main() -> None:
    settings = get_settings()
    if not settings.demo_mode:
        raise SystemExit("The demo CLI requires DEMO_MODE=true.")
    service = _build_chat_service()
    session_id = None
    print("Gear AI demo chat. Vehicles and availability are sample data.")
    print("Type /reset for a new chat or /quit to leave.\n")
    while True:
        try:
            message = input("You> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if message.lower() in {"/quit", "/exit"}:
            break
        if message.lower() == "/reset":
            session_id = None
            print("New chat started.\n")
            continue
        if not message:
            continue
        response = asyncio.run(service.chat(ChatRequest(message=message,
                                                         session_id=session_id), None))
        session_id = response.session_id
        print(f"Gear AI> {response.message}")
        for index, card in enumerate(response.vehicles, start=1):
            price = f" · sample AED {card.price:g}" if card.price is not None else ""
            label = f"{index}." if response.status == "results" else "Selected vehicle:"
            print(f"  {label} {card.title or 'Untitled sample vehicle'}"
                  f" · {card.location or 'unknown area'}{price}")
        if response.suggestions:
            print("Suggestions: " + " | ".join(response.suggestions))
        print()


if __name__ == "__main__":
    main()
