# Gear API filter audit

Observed on 2026-10-06 from the public `GET /Gear/api/getCars` endpoint and the Postman request definitions supplied in the conversation. The inspection read field names and types from five returned car objects; it did not retain or print vehicle or vendor values. A sample response is not proof that every documented filter behaves correctly.

| Capability | Observed response or metadata | Agent status |
| --- | --- | --- |
| Location, free text, dates, times, price bounds | The agent sends these as `getCars` query parameters. `filters_applied` includes `min_price` and `max_price`. | Implemented; exact backend semantics still need contract tests. |
| Car type and brand | Cars include `car_type_id`, `car_type_name`, `brand_id`, and `brand_name`; `filters_applied` includes `car_type_id`, `car_type_ids`, and `brand_id`. Postman documents the corresponding query parameters. | Natural-language names are resolved only against authenticated `getDropdownData`; missing or rejected credentials preserve the search and return `SIGN_IN`. |
| Rating and featured | Cars include `avg_rating` and `is_featured`; `filters_applied` includes `min_rating` and `featured`. | Chat extraction and Gear query forwarding are implemented. A live `min_rating=0` rating-sorted query returned 12 cards; `min_rating=4` and `featured=true` returned no matches, so those cases are not validated against nonempty results. |
| Sort | `filters_applied` includes `sort_by`. | Chat supports Postman's `recommended`, `price_low`, `price_high`, `rating`, `distance`, and `newest` values. Distance requires coordinates. |
| Price display | Cars include `day_price` and `day_price_with_fee`; no plain `price` was seen in the sample. | The mapper exposes the first verified numeric daily-price field and labels it `/day`; it never presents this as a checkout total. Fee/currency confirmation is still required. |
| Seat count | Cars include `no_seats` as a string. No seat query parameter was observed in `filters_applied`. | The mapper normalizes `no_seats`; the service applies the capacity requirement to verified returned cars. A server-side filter is still required to guarantee matches beyond the first 50 records. |
| Fuel, transmission, driver, deals, features | No corresponding car fields appeared in the five sampled records. | Fuel/mileage and transmission are mapped and filtered when Gear returns verified fields. Driver, deals, and unstructured features remain blocked. Backend exposure is still required for live fuel/transmission results. |
| Availability | Postman documents `getAvailableCars` with dates, times, coordinates, paired price bounds, `car_type_id`, and `brand_id`; `checkIsCarAvailable` checks one car. Gear's current public web client posts `car_id`, start/end dates, and start/end times, then treats a successful response as available unless the standard API envelope has `status: false`. | Complete-date catalog searches use the protected endpoint and require the user's Gear token. Selected-car checks now mirror the verified Web request/envelope behavior, including `Accept` and `Language` headers, while still honoring explicit availability booleans when Gear returns them. |
| Checkout calculation | `/checkout` is in the supplied API description. | Disabled until Gear confirms that calling it cannot book, charge, or mutate state. |

The response includes pagination metadata. Postman documents `getCars` page limits up to 50. Filtering only the first page in the AI service could hide valid matches. Server-side filters on `getCars` and `getAvailableCars` are preferred. If client-side filtering is temporary, it must account for every relevant page and the backend's result limit.

`getDropdownData` returned HTTP 401 without a customer token, as Postman describes. The optional development inspector requests a dedicated customer test token at a hidden terminal prompt, uses it only in the Gear Authorization header, and prints keys and types. No token is configured or stored by the inspector, so live type/brand catalog resolution is pending. The chat service never uses the test token.

## Backend issues and requested extension

1. **Immediate data exposure:** the public car response includes a nested `company.password` string field. The agent's whitelist mapper does not forward it, but Gear should remove this field and other vendor-only data from the public API response. No password value was printed or saved during the audit.
2. Confirm response field semantics and nonempty filter behavior with a staging test account. The supplied Postman definitions establish parameter names and sort values.
3. If Gear stores seat count and other requested attributes, expose normalized fields in car responses and add matching query parameters to the existing `getCars` and `getAvailableCars` endpoints. Suggested names are `min_seats`, `fuel_type`, `transmission`, `with_driver`, and `deals_only`; the backend team should confirm the final contract.
4. Use the development-only read-only MongoDB inspector to distinguish fields missing from the API from fields missing in the database. The currently supplied credential has `atlasAdmin` permissions, so collection inspection remains stopped until a read-only credential is provided.

Production search remains `AI agent → Gear API → Gear database`. The database inspector is never used by the chat service.
