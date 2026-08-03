# New API Monitor 1.12.1 Differential Security Review

Review date: 2026-08-03

Baseline: `d954684`

Scope: attribution-difference wording and current customer-console documentation

## Result

No authentication, authorization, storage, outbound-request, or secret-handling code changed. The patch only removes an unsupported causal claim from the UI and documentation.

Production reconciliation confirmed that 24-hour and 7-day model/flow attribution matched live consume logs exactly. The retained all-time flow difference was exactly equal to historical `quota_data` rows with an empty `use_group`, proving that a positive difference is not always a pending export. The UI now reports the difference without promising that it will later disappear.

Automated acceptance still covers administrator and viewer routing, exact total closure, neutral attribution wording, frontend build, backend tests, Playwright, release validation, Docker image health, and secret scanning.
