# Gear AI Agent Architecture and Delivery Plan

## Scope and current state

One FastAPI service is designed to serve the existing Gear Web and Mobile assistant entry points through `POST /api/v1/agent/chat`. It helps customers search, inspect vehicle details, check availability, and understand the documented booking journey. Gear remains the source of truth for listings and commercial facts. Search, details, and availability flows now have working code and mocked tests; Web/Mobile wiring and live Gear behavior still await staging access and response samples. A development playground at `/playground` and `python -m app.demo_cli` use explicitly labeled sample inventory. Checkout quote mapping remains planned.

The intended experience follows the described Honeymoon Events chatbot pattern: accept natural language, ask one short question when an essential detail is missing, search real inventory, show ranked vehicle cards, offer relevant follow-up chips, and carry validated filters into the next turn. Both Web and Mobile use the same API contract.

```text
Gear Web / Mobile
    -> FastAPI: token handling and request schema
    -> conversation: intent, filter merge, clarification
    -> guardrails: allowed intent, parameters, tool and user scope
    -> Gear API client: catalog, search, details, availability, quote
    -> ranking and response builder: grounded cards and chips
    -> output validation -> Gear Web / Mobile
```

The model receives only the minimum conversation text and safe catalog context needed to interpret a request. It cannot choose arbitrary URLs or execute Gear operations directly.

## Conversation contract

The Gear login and AI conversation session are separate. A conversation has a stable internal ID and a high-entropy opaque client session token. Web uses an `HttpOnly`, `Secure`, `SameSite=Lax` `ai_session_id` cookie; Mobile sends the token in `X-AI-Session-ID` or `session_id` in the request body and stores it in platform secure storage. Cookie-mode responses set the cookie and omit the token from JSON. Mobile responses return the token in `session_id`. If more than one transport supplies an ID, the values must match. The Web client marks its first request with `X-AI-Session-Transport: cookie` so the first response is also cookie-only. The same AI session token continues before and after Gear login; adding a Gear bearer does not rotate or replace it.

The chat API does not treat a Bearer header as proof of identity. Guest listing search calls public `getCars` without a customer token. Protected detail, catalog, location, and availability calls require a customer Gear bearer; the AI service forwards it and Gear validates it. A missing or rejected bearer produces `authentication_required` with a sign-in action. The AI session token is a separate random capability used only to resume the conversation. No service-wide Gear credential is configured, and the AI service does not decode or trust JWT claims.

The response carries `status`, `message`, `vehicles`, `suggestions`, and `data_source` (`gear` or `demo`), plus `session_id` for Mobile transport. Vehicle cards use only fields confirmed in Gear responses; unknown price or availability is represented as unknown, never estimated. Booking requests return `SIGN_IN` guidance so Web/Mobile can open the Gear sign-in and booking flow.

Session state stores the internal conversation ID, client token digest, validated search criteria, prior Gear cards, and expiry. A new constraint merges into the last search; an explicit correction replaces the old value. A new-search action deletes the current device conversation and expires its cookie. The current in-memory store is for local development; persistent storage and its retention policy are selected before deployment. No model response ID is treated as the session store.

Logout policy: authentication is removed independently of the AI conversation. A client may keep the conversation token to continue its chat or reset it explicitly. Web calls `POST /api/v1/agent/session/reset` to remove the conversation and cookie; Mobile discards its stored token and can call the same endpoint with `X-AI-Session-ID` to remove server state.

Example: “I need a 5-seater fuel-efficient car for 3 days” retains the duration, asks only for the pickup date, calculates the return date, and filters/ranks only on verified Gear attributes. Type and brand names resolve against Gear's catalog. Driver, deals, and unstructured feature filters remain held until their API mappings are verified.

## Provider configuration

The hosted extractor calls OpenAI with a server-side `OPENAI_API_KEY` and validates strict structured output with Pydantic. `OPENAI_MODEL` defaults to `gpt-6-luna`, which supports Chat Completions and structured output. The development demo defaults to a local parser so it remains testable offline; `DEMO_EXTRACTOR=hosted` uses OpenAI while keeping sample vehicle inventory. A live Luna extraction and end-to-end search succeeded; broader conversation evaluations remain. [OpenAI model documentation](https://developers.openai.com/api/docs/models/gpt-6-luna)

The OpenAI API key and optional customer Gear token are separate credentials. Neither belongs in prompts or logs. The customer token is sent only to Gear's protected read endpoints and never to OpenAI. The model is selected through server configuration, never a user request.

## Gear integration and grounded output

The supplied Postman collection documents `getCars` as public, plus authenticated `getAvailableCars`, `getCarDetails`, `getDropdownData`, `checkIsCarAvailable`, and `getDefaultLocations`. The Gear client allowlists those read routes, calls `getCars` without a customer token, and forwards a logged-in user's Bearer token only to protected routes. Gear validates that token. Missing or rejected credentials produce `authentication_required`; the agent does not create bookings or call `checkout`.

Catalog name-to-ID resolution uses authenticated `getDropdownData`; user-supplied IDs still come only from validated client context. Ranking uses only returned, verified attributes: explicit family flags, seating, luggage capacity, vehicle category, mileage, budget, rating, and location. Discounts, driver options and charges, unstructured features, and vendor comparisons remain conditional on confirmed API fields. A user's booking status requires a separate verified endpoint; the documented lifecycle alone supports general guidance, not a live status claim. Live GPS tracking is outside scope.

If Gear cannot verify live inventory, availability, or price, return an `unavailable` response with a retry suggestion. Never invent cars, IDs, prices, discounts, ratings, or booking status.

## Guardrails and operations

Validate request size, language and field types before model use. Treat all user text and Gear content as untrusted data; prompt-injection checks support the boundary but cannot replace server-side authorization. An allowlisted tool registry and typed parameters restrict calls to documented read/search operations. Gear remains the authority for customer token validation and protected read access. The agent has no booking, cancellation, refund, or payment tools.

Validate model extraction and final cards with schemas. Keep raw tokens, passwords, payment data and unnecessary PII out of model context and logs. Log request ID, intent, tool name, latency, Gear status, guardrail decision and safe error code. Add timeouts, bounded retries for safe reads, rate limits, and clear errors for provider or Gear outages.

## Implementation stages and acceptance gates

1. **Configuration and contract — implemented:** typed settings, OpenAI model selection, chat request/response schemas, and generated OpenAPI. A missing OpenAI key fails only on use; health remains available.
2. **OpenAI and conversation foundation — implemented and smoke tested:** OpenAI HTTP adapter, strict extraction, filter merge, clarification rules, and short-lived local sessions.
3. **Gear read integration — partly implemented:** search, details, availability, catalog and locations client methods. The public listing endpoint used by Gear's web app was verified at `https://gearapp.ae:5001/Gear/api/getCars`. Quote mapping, authenticated response samples, and staging tests remain.
4. **Experience and guardrails — partly implemented:** grounded cards, follow-up chips, opaque conversation sessions independent of Gear login, allowlisted Gear routes, and controlled authentication/outage responses. Ranking, broader security evaluations and Web/Mobile integration tests remain.
5. **Release readiness — planned:** choose persistent session storage and retention, add operational metrics and rate limits, then run security and conversation evaluations in staging.

Next external inputs: exact staging API base URL, a dedicated customer test account or token for protected read calls, the Postman request definitions, and representative response bodies for fuel, driver, and deal fields. Until then, development can use explicit fixtures without presenting them as live Gear results. See the [Gear API filter audit](gear-api-filter-audit.md) for the sampled public response and follow-up issues.
