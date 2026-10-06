"""Deterministic chat flow around the model and Gear API boundaries."""

from datetime import date, timedelta
from typing import Callable

from pydantic import ValidationError

from app.clients.gear import (
    GearAuthorizationRequired,
    GearClient,
    GearUnavailable,
    resolve_catalog_id,
)
from app.clients.llm import Extraction, LLMUnavailable, RequirementExtractor
from app.config import ConfigurationError
from app.guardrails.policy import (
    catalog_only_blocks,
    explicit_search_reset,
    grounded_refusal,
    missing_verified_rating,
    unverified_constraints,
    unverified_filter_message,
    verified_preference_updates,
)
from app.schemas.chat import ChatAction, ChatRequest, ChatResponse, SearchFilters, VehicleCard
from app.security.sessions import PendingProtectedOperation, Session, SessionStore
from app.services.dates import gear_today, has_bare_day_range, resolve_month_day_range
from app.services.faqs import faq_answer
from app.services.messaging import (
    OPENING_SUGGESTIONS,
    booking_guidance_message,
    booking_guidance_suggestions,
    budget_change_acknowledgement,
    criteria_summary,
    dates_prompt,
    empty_search_prompt,
    location_prompt,
    near_me_prompt,
    no_results_message,
    no_results_suggestions,
    opening_message,
    results_message,
    results_suggestions,
    unavailable_vehicle_message,
    unavailable_vehicle_suggestions,
    with_match_reasons,
)
from app.services.vehicle_specs import (
    SpecTopic,
    asks_about_focus_vehicle,
    is_greeting,
    vehicle_spec_message,
    vehicle_spec_topic,
)


class ConversationService:
    def __init__(self, extractor: RequirementExtractor, gear: GearClient,
                 sessions: SessionStore, data_source: str = "gear",
                 today: Callable[[], date] = gear_today):
        self.extractor = extractor
        self.gear = gear
        self.sessions = sessions
        self.data_source = data_source
        self.today = today

    async def chat(self, request: ChatRequest, token: str | None) -> ChatResponse:
        session = self.sessions.get_or_create(request.session_id)
        if (session.pending_operation and token
                and (self._automatic_resume_message(request.message)
                     or self._message_matches_pending_operation(
                         request.message, session.pending_operation))
                and not explicit_search_reset(request.message)):
            return await self._resume_pending_operation(session, token)
        if faq := faq_answer(request.message):
            return self._response(session, "guidance", faq[0], suggestions=faq[1])
        filters_before = session.filters.model_copy(deep=True)
        try:
            titles = [card.title or f"Option {index}"
                      for index, card in enumerate(session.results, start=1)]
            extracted = await self.extractor.extract(request.message, session.filters, titles)
        except (LLMUnavailable, ConfigurationError):
            return self._response(session, "unavailable",
                                  "I can't process your request right now. Please try again shortly.")

        if is_greeting(request.message):
            return self._response(
                session, "guidance", opening_message(), suggestions=OPENING_SUGGESTIONS,
            )

        spec_topic = vehicle_spec_topic(request.message)
        if extracted.intent not in {"details", "availability", "pricing"} and (
            extracted.intent == "vehicle_specs"
            or (
                spec_topic
                and asks_about_focus_vehicle(
                    request.message, has_selected=session.selected_vehicle_id is not None,
                )
            )
        ):
            topic: SpecTopic = spec_topic or "overview"
            card = self._resolve_focus_card(session, extracted.selected_index)
            if card is None:
                return self._response(
                    session, "clarification",
                    "Which car should I check? Pick an option from your results.",
                )
            return await self._vehicle_spec_answer(session, topic, card, token)

        if extracted.intent == "unsupported":
            return self._response(session, "guidance",
                                  "I can help you find a rental and explain how booking works.")
        if extracted.intent == "booking_guidance":
            return self._response(
                session, "guidance", booking_guidance_message(),
                suggestions=booking_guidance_suggestions(),
                action=ChatAction(type="SIGN_IN"),
            )
        refusal = grounded_refusal(request.message)
        if refusal:
            return self._response(session, "guidance", refusal)

        merge_error = self._merge_extracted_filters(session, request, extracted)
        if merge_error:
            return merge_error

        if extracted.intent not in {"details", "pricing"}:
            date_error = self._past_pickup_error(session)
            if date_error:
                return date_error

        budget_ack = budget_change_acknowledgement(filters_before, session.filters)

        if session.pending_vehicle_intent == "availability" and extracted.intent == "search":
            return await self._vehicle_follow_up(session, "availability", None, token)

        blocked = unverified_constraints(request.message, extracted.unsupported_constraints)
        if self.data_source == "demo":
            blocked = [name for name in blocked if name not in {"car_type", "brand"}]
        if request.context.car_type_id or request.context.car_type_ids:
            blocked = [name for name in blocked if name != "car_type"]
        if request.context.brand_id:
            blocked = [name for name in blocked if name != "brand"]
        catalog_skipped = False
        if blocked:
            searchable = bool(session.filters.query_params())
            needs_missing_dates = (
                extracted.needs_dates
                and not (session.filters.from_date and session.filters.to_date)
            )
            has_budget = (
                session.filters.max_price is not None or session.filters.min_price is not None
            )
            refined_search = bool(session.results) or has_budget
            if (catalog_only_blocks(blocked) and searchable and not needs_missing_dates
                    and refined_search):
                blocked = []
                catalog_skipped = True
            else:
                if needs_missing_dates:
                    session.pending_requirement = "dates"
                return self._response(
                    session, "clarification",
                    unverified_filter_message(blocked, session.filters, extracted.needs_dates),
                )

        filters = session.filters
        if extracted.intent in {"details", "availability", "pricing"}:
            return await self._vehicle_follow_up(session, extracted.intent,
                                                 extracted.selected_index, token)
        needs_dates = (
            extracted.needs_dates or (filters.rental_days is not None and filters.from_date is None)
        ) and not (filters.from_date and filters.to_date)
        if extracted.needs_location and request.context.lat is None:
            session.pending_requirement = ("location_then_dates" if needs_dates
                                           else "location")
            prompt = near_me_prompt() if extracted.sort_by == "distance" else location_prompt()
            return self._response(session, "clarification", prompt)
        if session.pending_requirement in {"location", "location_then_dates"}:
            if not (filters.location or filters.lat is not None):
                return self._response(session, "clarification", location_prompt())
            session.pending_requirement = ("dates" if session.pending_requirement == "location_then_dates"
                                           or needs_dates else None)
        if needs_dates or session.pending_requirement == "dates":
            if filters.from_date and filters.to_date:
                session.pending_requirement = None
            else:
                session.pending_requirement = "dates"
                prompt = dates_prompt(
                    pickup_only=(filters.from_date is None
                                 and (filters.to_date is not None
                                      or filters.rental_days is not None)),
                    return_only=filters.from_date is not None and filters.to_date is None,
                )
                if budget_ack:
                    prompt = f"{budget_ack} {prompt}"
                return self._response(
                    session, "clarification",
                    prompt,
                )
        if filters.sort_by == "distance" and filters.lat is None:
            return self._response(session, "clarification", near_me_prompt())
        if (filters.from_date is None) != (filters.to_date is None):
            return self._response(
                session, "clarification",
                dates_prompt(
                    pickup_only=filters.from_date is None,
                    return_only=filters.to_date is None,
                ),
            )
        catalog_error = await self._resolve_catalog_filters(
            session, request.message, token,
        )
        if catalog_error:
            return catalog_error
        filters = session.filters
        if not filters.query_params():
            if any((filters.family_suitable, filters.seats, filters.fuel_type,
                    filters.fuel_efficiency_preferred, filters.transmission)):
                session.pending_requirement = "location"
                return self._response(session, "clarification", location_prompt())
            return self._response(session, "clarification", empty_search_prompt())
        return await self._search_vehicles(session, token, request_message=request.message,
                                           budget_ack=budget_ack,
                                           catalog_skipped=catalog_skipped)

    async def _resolve_catalog_filters(
        self, session: Session, request_message: str, token: str | None,
    ) -> ChatResponse | None:
        filters = session.filters
        unresolved_type = filters.car_type_name if not filters.car_type_id else None
        unresolved_brand = filters.brand_name if not filters.brand_id else None
        if self.data_source == "demo" or not (unresolved_type or unresolved_brand):
            return None
        if not token:
            session.pending_operation = PendingProtectedOperation(
                "catalog_search", request_message=request_message,
            )
            return self._authentication_required(
                session,
                "Please sign in so I can match that vehicle type or brand to Gear's verified catalog.",
            )
        try:
            catalog = await self.gear.filter_catalog(token)
        except GearAuthorizationRequired:
            session.pending_operation = PendingProtectedOperation(
                "catalog_search", request_message=request_message,
            )
            return self._authentication_required(
                session, "Please sign in again so I can use Gear's vehicle catalog.",
            )
        except (GearUnavailable, ConfigurationError):
            return self._response(
                session, "unavailable",
                "I can't load Gear's vehicle types right now. Please try again shortly.",
            )

        values = filters.model_dump(exclude_none=True)
        if unresolved_type:
            type_id = resolve_catalog_id(catalog, "car_type", unresolved_type)
            if type_id is None:
                return self._response(
                    session, "clarification",
                    f"I couldn't find {unresolved_type} in Gear's current vehicle-type catalog. "
                    "Which vehicle type should I use?",
                )
            values["car_type_id"] = type_id
        if unresolved_brand:
            brand_id = resolve_catalog_id(catalog, "brand", unresolved_brand)
            if brand_id is None:
                return self._response(
                    session, "clarification",
                    f"I couldn't find {unresolved_brand} in Gear's current brand catalog. "
                    "Which brand should I use?",
                )
            values["brand_id"] = brand_id
        session.filters = SearchFilters.model_validate(values)
        session.pending_operation = None
        return None

    async def _search_vehicles(self, session: Session, token: str | None,
                               *, request_message: str = "", budget_ack: str | None = None,
                               catalog_skipped: bool = False) -> ChatResponse:
        filters = session.filters
        searches_availability = (filters.from_date is not None
                                 and filters.to_date is not None
                                 and self.data_source == "gear")
        if searches_availability and not token:
            session.pending_operation = PendingProtectedOperation(
                "search_availability", request_message=request_message,
                catalog_skipped=catalog_skipped)
            return self._authentication_required(
                session, "Please sign in to search vehicles available for those dates.")
        had_prior_results = bool(session.results)
        try:
            if searches_availability:
                cars = await self.gear.search_available_cars(filters, token)
            else:
                cars = await self.gear.search_cars(filters, None)
        except GearAuthorizationRequired:
            session.pending_operation = PendingProtectedOperation(
                "search_availability", request_message=request_message,
                catalog_skipped=catalog_skipped)
            return self._authentication_required(
                session, "Please sign in again to search available vehicles.")
        except (GearUnavailable, ConfigurationError):
            session.pending_operation = None
            return self._response(session, "unavailable",
                                  "I can't search right now. Please try again shortly.")
        cars, missing_attribute = self._apply_verified_preferences(
            cars, filters, apply_unforwarded=searches_availability,
        )
        if missing_attribute:
            session.pending_operation = None
            return self._response(
                session, "unavailable",
                f"Gear's current listings don't provide verified {missing_attribute} data, "
                "so I can't apply that requirement safely yet.",
                suggestions=["Change requirements", "Keep browsing"],
            )
        session.results = cars
        session.pending_operation = None
        session.selected_vehicle_id = None
        session.pending_vehicle_intent = None
        if missing_verified_rating(request_message, cars):
            return self._response(session, "guidance",
                                  "I don't have verified rating data for these results.")
        if not cars:
            message = (f"{budget_ack} I couldn't find a verified match for those filters right now. "
                       "I can broaden the search if you'd like.") if budget_ack else no_results_message(filters)
            return self._response(session, "results", message,
                                  suggestions=no_results_suggestions())
        cars = with_match_reasons(cars, filters)
        refinement = None
        if budget_ack:
            refinement = budget_ack.rstrip(".")
        elif had_prior_results:
            refinement = criteria_summary(filters, lead="Sure").rstrip(".")
        message = results_message(len(cars), filters, catalog_skipped=catalog_skipped,
                                  refinement_note=refinement)
        return self._response(
            session, "results", message,
            vehicles=cars,
            suggestions=results_suggestions(filters),
        )

    @staticmethod
    def _apply_verified_preferences(
        cars: list[VehicleCard], filters: SearchFilters, *, apply_unforwarded: bool = False,
    ) -> tuple[list[VehicleCard], str | None]:
        filtered = list(cars)
        required_fields = (
            (filters.seats is not None, "seating capacity", lambda car: car.seats is not None),
            (filters.fuel_type is not None, "fuel type", lambda car: car.fuel_type is not None),
            (filters.transmission is not None, "transmission", lambda car: car.transmission is not None),
            (
                filters.fuel_efficiency_preferred is True,
                "fuel efficiency",
                lambda car: car.fuel_efficiency is not None or car.is_fuel_efficient is not None,
            ),
        )
        for requested, label, has_value in required_fields:
            if requested and filtered and not any(has_value(car) for car in filtered):
                return [], label
        if apply_unforwarded and filters.location:
            if filtered and not any(car.location for car in filtered):
                return [], "location"
            wanted_location = filters.location.casefold()
            filtered = [car for car in filtered
                        if car.location and wanted_location in car.location.casefold()]
        if apply_unforwarded and filters.search:
            wanted_search = filters.search.casefold()
            filtered = [car for car in filtered
                        if car.title and wanted_search in car.title.casefold()]
        if apply_unforwarded and filters.car_type_ids:
            allowed_types = {value.casefold() for value in filters.car_type_ids}
            if filtered and not any(car.car_type_id for car in filtered):
                return [], "vehicle type"
            filtered = [car for car in filtered
                        if car.car_type_id and car.car_type_id.casefold() in allowed_types]
        if apply_unforwarded and filters.min_rating is not None:
            if filtered and not any(car.rating is not None for car in filtered):
                return [], "rating"
            filtered = [car for car in filtered
                        if car.rating is not None and car.rating >= filters.min_rating]
        if apply_unforwarded and filters.featured is not None:
            if filtered and not any(car.is_featured is not None for car in filtered):
                return [], "featured-listing"
            filtered = [car for car in filtered if car.is_featured is filters.featured]
        if filters.seats is not None:
            filtered = [car for car in filtered
                        if car.seats is not None and car.seats >= filters.seats]
        if filters.fuel_type:
            wanted = filters.fuel_type.casefold()
            filtered = [car for car in filtered
                        if car.fuel_type and car.fuel_type.casefold() == wanted]
        if filters.transmission:
            wanted = filters.transmission.casefold()
            filtered = [car for car in filtered
                        if car.transmission and car.transmission.casefold() == wanted]
        if filters.budget_period == "trip" and filters.max_price is not None:
            if not filters.rental_days:
                return [], "rental duration for a total-trip budget"
            if filtered and not any(car.price is not None for car in filtered):
                return [], "daily price"
            filtered = [
                car for car in filtered
                if car.price is not None and car.price * filters.rental_days <= filters.max_price
            ]
        if filters.fuel_efficiency_preferred:
            filtered.sort(
                key=lambda car: (
                    car.is_fuel_efficient is True,
                    car.fuel_efficiency if car.fuel_efficiency is not None else -1,
                ),
                reverse=True,
            )
        if filters.family_suitable:
            def family_score(car: VehicleCard) -> int:
                return (
                    (100 if car.is_family_suitable is True else 0)
                    + (30 if car.seats is not None and car.seats >= 5 else 0)
                    + (20 if car.luggage_capacity is not None and car.luggage_capacity >= 2 else 0)
                    + (10 if (car.car_type_name or "").casefold()
                       in {"suv", "van", "minivan", "mpv"} else 0)
                )

            if any(family_score(car) for car in filtered):
                filtered.sort(key=family_score, reverse=True)
        if filters.sort_by in {"price_low", "price_high"}:
            filtered.sort(
                key=lambda car: car.price if car.price is not None else float("inf"),
                reverse=filters.sort_by == "price_high",
            )
        elif filters.sort_by == "rating":
            filtered.sort(key=lambda car: car.rating if car.rating is not None else -1,
                          reverse=True)
        return filtered, None

    async def _vehicle_spec_answer(self, session: Session, topic: SpecTopic,
                                 card: VehicleCard, token: str | None) -> ChatResponse:
        session.selected_vehicle_id = card.id
        enriched = card
        if self.data_source == "gear":
            if token:
                try:
                    enriched = await self.gear.car_details(card.id, token)
                except GearAuthorizationRequired:
                    session.pending_operation = PendingProtectedOperation("details", card.id)
                    return self._authentication_required(
                        session,
                        "Sign in and I can pull this car's verified specifications.",
                        vehicles=[card],
                    )
                except (GearUnavailable, ConfigurationError):
                    enriched = card
            elif self._spec_data_missing(card, topic):
                session.pending_operation = PendingProtectedOperation("details", card.id)
                return self._authentication_required(
                    session,
                    "Sign in and I can pull this car's verified specifications.",
                    vehicles=[card],
                )
        message = vehicle_spec_message(enriched, topic)
        suggestions = ["Check availability", "Show similar cars", "How booking works"]
        return self._response(session, "details", message, vehicles=[enriched],
                              suggestions=suggestions)

    @staticmethod
    def _spec_data_missing(card: VehicleCard, topic: SpecTopic) -> bool:
        if topic == "seats":
            return card.seats is None
        if topic == "mileage":
            return card.fuel_efficiency is None and card.is_fuel_efficient is None
        if topic == "fuel":
            return card.fuel_type is None
        if topic == "transmission":
            return card.transmission is None
        if topic == "luggage":
            return card.luggage_capacity is None
        return not any((
            card.seats is not None, card.fuel_type, card.transmission,
            card.fuel_efficiency is not None, card.is_fuel_efficient is not None,
            card.luggage_capacity is not None,
        ))

    def _resolve_focus_card(self, session: Session,
                            selected_index: int | None) -> VehicleCard | None:
        card = self._selected_card(session, selected_index)
        if card is not None:
            return card
        return self._card_by_id(session, session.selected_vehicle_id)

    async def _vehicle_follow_up(self, session: Session, intent: str,
                                 selected_index: int | None,
                                 token: str | None) -> ChatResponse:
        card = self._selected_card(session, selected_index)
        if card is None and selected_index is None:
            selected_id = session.selected_vehicle_id
            if selected_id is None and session.pending_operation is not None:
                selected_id = session.pending_operation.vehicle_id
            card = self._card_by_id(session, selected_id)
        if card is None:
            session.pending_operation = None
            session.pending_vehicle_intent = None
            return self._response(session, "clarification",
                                  "Which car do you mean? Search first if you don't see options yet.")
        session.selected_vehicle_id = card.id
        if intent == "pricing":
            message = ("Checkout totals aren't available in this demo."
                       if self.data_source == "demo" else
                       "I can't verify the checkout total yet — please review the price in the app.")
            return self._response(session, "unavailable",
                                  message, vehicles=[card])
        if intent == "availability":
            session.pending_operation = None
            session.pending_vehicle_intent = "availability"
            filters = session.filters
            if not (filters.from_date and filters.to_date):
                session.pending_requirement = "availability_dates"
                return self._response(
                    session, "clarification",
                    dates_prompt(
                        pickup_only=filters.from_date is None and filters.to_date is not None,
                        return_only=filters.from_date is not None and filters.to_date is None,
                    ),
                )
            if not (filters.from_time and filters.to_time):
                session.pending_requirement = "availability_times"
                return self._response(session, "clarification",
                                      "What pickup and return times should I check for those dates?")
            session.pending_requirement = None
            session.pending_vehicle_intent = None
        pending = PendingProtectedOperation(
            "details" if intent == "details" else "vehicle_availability", card.id)
        if self.data_source == "gear" and not token:
            session.pending_operation = pending
            return self._authentication_required(
                session,
                ("Please sign in to view this car's full details."
                 if intent == "details" else
                 "Please sign in to check this car's live availability."),
                vehicles=[card],
            )
        try:
            if intent == "details":
                details = await self.gear.car_details(card.id, token)
                session.pending_operation = None
                session.pending_vehicle_intent = None
                details = with_match_reasons([details], session.filters)[0]
                message = "Here's what I found for this car."
                index = session.results.index(card) + 1
                return self._response(session, "details", message,
                                    vehicles=[details],
                                    suggestions=[f"Check availability for option {index}"])
            available = await self.gear.check_availability(card.id, session.filters, token)
            session.pending_operation = None
            if self.data_source == "demo":
                message = ("In this demo, the sample vehicle is available for those dates and times."
                           if available else
                           "In this demo, the sample vehicle is unavailable for those dates and times.")
            elif available:
                message = (
                    "This car is available for your selected dates. To book it, open the "
                    "Gear app, select this car, and complete the booking there."
                )
            else:
                message = unavailable_vehicle_message()
            suggestions = (["How booking works", "Show similar cars"] if available
                           else unavailable_vehicle_suggestions())
            return self._response(session, "availability", message, vehicles=[card],
                                  available=available, suggestions=suggestions)
        except GearAuthorizationRequired:
            session.pending_operation = pending
            return self._authentication_required(
                session, "Please sign in again to view protected Gear information.",
                vehicles=[card],
            )
        except (GearUnavailable, ConfigurationError):
            session.pending_operation = None
            return self._response(session, "unavailable",
                                  "I can't verify this car right now. Please try again shortly.")

    async def _resume_pending_operation(self, session: Session,
                                        token: str) -> ChatResponse:
        pending = session.pending_operation
        if pending is None:
            raise RuntimeError("No pending operation to resume")
        if pending.kind == "catalog_search":
            session.pending_operation = None
            return await self.chat(
                ChatRequest(message=pending.request_message, session_id=session.public_token),
                token,
            )
        if pending.kind == "search_availability":
            date_error = self._past_pickup_error(session)
            if date_error:
                session.pending_operation = None
                return date_error
            return await self._search_vehicles(session, token,
                                               request_message=pending.request_message,
                                               catalog_skipped=pending.catalog_skipped)
        if pending.kind == "vehicle_availability":
            date_error = self._past_pickup_error(session)
            if date_error:
                session.pending_operation = None
                return date_error
        index = next((position for position, card in enumerate(session.results, start=1)
                      if card.id == pending.vehicle_id), None)
        return await self._vehicle_follow_up(
            session, "details" if pending.kind == "details" else "availability", index, token)

    def _merge_extracted_filters(self, session: Session, request: ChatRequest,
                                 extracted: Extraction) -> ChatResponse | None:
        updates = {key: value for key, value in extracted.filter_updates().items()
                   if value != ""}
        if "car_type" in extracted.unsupported_constraints and extracted.search:
            updates["car_type_name"] = extracted.search
            updates.pop("search", None)
        if "brand" in extracted.unsupported_constraints and extracted.search:
            updates["brand_name"] = extracted.search
            updates.pop("search", None)
        updates.update(verified_preference_updates(request.message))
        reset = extracted.reset_filters and explicit_search_reset(request.message)
        if reset:
            session.pending_requirement = None
            session.pending_operation = None
            session.selected_vehicle_id = None
            session.pending_vehicle_intent = None
            session.results = []
        try:
            relative_dates = resolve_month_day_range(request.message, self.today())
        except ValueError:
            return self._response(session, "clarification",
                                  "Those days aren't a valid pickup and return range for that month. "
                                  "Which days should I use?")
        extracted_dates_are_iso = False
        if extracted.from_date and extracted.to_date:
            try:
                date.fromisoformat(extracted.from_date)
                date.fromisoformat(extracted.to_date)
                extracted_dates_are_iso = True
            except ValueError:
                pass
        ambiguous_day_range = (
            relative_dates is None
            and has_bare_day_range(request.message)
            and not extracted_dates_are_iso
            and bool(extracted.from_date or extracted.to_date
                     or session.pending_vehicle_intent == "availability"
                     or session.pending_requirement in {"dates", "availability_dates"})
        )
        if ambiguous_day_range:
            updates.pop("from_date", None)
            updates.pop("to_date", None)
        existing = {} if reset else session.filters.model_dump(exclude_none=True)
        if "car_type_name" in updates:
            existing.pop("car_type_id", None)
            existing.pop("car_type_ids", None)
        if "brand_name" in updates:
            existing.pop("brand_id", None)
        existing.update(updates)
        if relative_dates:
            existing["from_date"] = relative_dates[0]
            if relative_dates[1] is not None:
                existing["to_date"] = relative_dates[1]
        duration_changed = "rental_days" in updates
        pickup_changed = "from_date" in updates or bool(relative_dates)
        return_changed = "to_date" in updates or bool(relative_dates and relative_dates[1])
        if (existing.get("rental_days") and existing.get("from_date")
                and (duration_changed or pickup_changed) and not return_changed):
            pickup = existing["from_date"]
            if isinstance(pickup, str):
                try:
                    pickup = date.fromisoformat(pickup)
                except ValueError:
                    pickup = None
            if isinstance(pickup, date):
                existing["to_date"] = pickup + timedelta(days=int(existing["rental_days"]))
        if existing.get("from_date") and existing.get("to_date"):
            try:
                pickup = (date.fromisoformat(existing["from_date"])
                          if isinstance(existing["from_date"], str) else existing["from_date"])
                dropoff = (date.fromisoformat(existing["to_date"])
                           if isinstance(existing["to_date"], str) else existing["to_date"])
                existing["rental_days"] = max(1, (dropoff - pickup).days)
            except (TypeError, ValueError):
                pass
        if request.context.lat is not None:
            existing["lat"] = request.context.lat
            existing["lng"] = request.context.lng
        if request.context.car_type_id is not None:
            existing["car_type_id"] = request.context.car_type_id
            existing.pop("car_type_ids", None)
        elif request.context.car_type_ids is not None:
            existing["car_type_ids"] = request.context.car_type_ids
            existing.pop("car_type_id", None)
        if request.context.brand_id is not None:
            existing["brand_id"] = request.context.brand_id
        try:
            session.filters = SearchFilters.model_validate(existing)
        except ValidationError:
            return self._response(session, "clarification",
                                  "Please give valid dates and a valid price range.")
        if ambiguous_day_range:
            session.pending_requirement = ("availability_dates"
                                           if session.pending_vehicle_intent == "availability"
                                           else "dates")
            return self._response(
                session, "clarification",
                "Which month and year should I use for those pickup and return days?",
            )
        return None

    def _past_pickup_error(self, session: Session) -> ChatResponse | None:
        pickup = session.filters.from_date
        if pickup is not None and pickup < self.today():
            return self._response(
                session, "clarification",
                "That pickup date is in the past. What future pickup date should I use?",
            )
        return None

    @staticmethod
    def _selected_card(session: Session, index: int | None) -> VehicleCard | None:
        if index is None and len(session.results) == 1:
            index = 1
        if isinstance(index, bool) or not isinstance(index, int):
            return None
        if 1 <= index <= len(session.results):
            return session.results[index - 1]
        return None

    @staticmethod
    def _card_by_id(session: Session, car_id: str | None) -> VehicleCard | None:
        if car_id is None:
            return None
        return next((card for card in session.results if card.id == car_id), None)

    @staticmethod
    def _automatic_resume_message(message: str) -> bool:
        normalized = " ".join(message.lower().strip(" .!?").split())
        return normalized in {
            "continue", "continue please", "retry", "try again", "proceed",
            "i signed in", "signed in", "i have signed in", "done",
        }

    @staticmethod
    def _message_matches_pending_operation(
        message: str, pending: PendingProtectedOperation,
    ) -> bool:
        lower = message.lower()
        mentions_availability = "availab" in lower or "check option" in lower
        if pending.kind == "catalog_search":
            return True
        if pending.kind == "details":
            return not mentions_availability and ("detail" in lower or "view option" in lower)
        return mentions_availability

    def _response(self, session: Session, status: str, message: str,
                  vehicles: list[VehicleCard] | None = None,
                  suggestions: list[str] | None = None,
                  available: bool | None = None,
                  action: ChatAction | None = None) -> ChatResponse:
        return ChatResponse(session_id=session.public_token, status=status, message=message,
                            vehicles=vehicles or [], suggestions=suggestions or [],
                            applied_filters=session.filters, available=available,
                            data_source=self.data_source, action=action)

    def _authentication_required(self, session: Session, message: str,
                                 vehicles: list[VehicleCard] | None = None) -> ChatResponse:
        return self._response(
            session, "authentication_required", message, vehicles=vehicles,
            suggestions=["Sign in", "Keep browsing"],
            action=ChatAction(type="SIGN_IN"),
        )
