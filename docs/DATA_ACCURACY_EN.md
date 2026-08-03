# Data Definitions and Accuracy

This document defines the authoritative source, calculation, freshness, and historical boundary for every monitor page. The UI and API must follow these contracts. Different scopes must be explained explicitly instead of leaving users to infer why totals differ.

## Core rules

1. **Prefer the authoritative source.** New API read-only endpoints own channels, accounts, keys, consumption logs, and billed quota. Local sampling owns host resources. The official Status API owns OpenAI status.
2. **Separate totals from attribution.** Analytics request and quota totals come from live consumption logs. Model, token, key, and group attribution comes from New API hourly aggregation. Any temporary difference is rendered as a separate reconciliation row.
3. **Separate current values from trends.** Resource current values and extrema use raw samples. Long-range charts use time-bucket averages and never present those averages as instantaneous peaks.
4. **Unknown is not failed.** Missing or stale probe samples are shown as unprobed or stale, never silently converted to healthy or failed.
5. **Lifetime means retained history.** “Lifetime total” means all history still queryable from the authoritative source and remains bounded by New API cleanup and monitor retention policies.
6. **Malformed authoritative data fails closed.** Missing required fields, invalid types, non-finite values, or inconsistent pagination fail the complete query instead of being replaced with zero values.

## Data matrix

| Page or metric | Authoritative source | Definition | Freshness and boundary |
| --- | --- | --- | --- |
| Channel catalog | New API `/api/channel/` | Mirrors the current catalog, probes enabled channels only, and applies separate admin/viewer visibility | Defaults to a 5-second sync; stale sync marks every channel unknown instead of presenting an old snapshot as current |
| Channel health | Live model probe or New API built-in test | History and availability use only the latest probe source for each channel so different protocols are not mixed | Stale after three probe intervals; all thresholds are dynamic; lifetime availability covers only retained probe samples |
| Monitor usage logs | New API consumption logs (`type=2`) | Real user consumption requests, excluding configured monitor-probe and model-test tokens | Defaults to 30-second overlapping incremental sync and 90-day retention; the UI shows collection bounds |
| Overview latency | Monitor usage logs | 24-hour count, average, P95, first-token average, and display-only slow count | Uses the configured slow-display threshold; one slow call does not directly send an external alert |
| Host resources | `psutil` and a read-only Docker Socket Proxy | Current values and extrema use raw samples; trends use bucket averages; sample coverage equals collected samples divided by expected samples for the selected interval | Defaults to 15-second sampling and 90-day retention; sampling and alert thresholds are dynamic |
| Incidents | Monitor state machines and collectors | Trigger reason, recovery reason, state, acknowledgement, and metadata | Resolved incidents default to 365-day retention; time filters use incident start time |
| Alert delivery | Durable SQLite outbox | Pending, sending, delivered, dead-letter, and cancelled states come from actual records; status facets follow time, destination, and search filters but ignore the selected status tab | Delivered and dead-letter records default to 30-day retention; full pagination is supported |
| OpenAI status | `https://status.openai.com/api/v2/summary.json` | Upstream context only; only configured workload-relevant components may affect overall status | Defaults to 60-second polling and never overrides local channel evidence |
| New API account overview | `/api/status`, `/api/user/self`, `/api/token/`, `/api/log/self/stat` | Always the current signed-in account; account requests, balances, and 24-hour consumption never mix with global usage | RPM and TPM are current 60-second rates, not totals for the selected history range |
| Analytics totals | `/api/log/` or `/api/log/self` and matching `stat` | Exact live consumption-log request and quota totals for the selected interval | Matches the currently queryable New API logs |
| Analytics model/token/flow | `/api/data/`, `/api/data/flow`, and self variants | New API hourly attribution; newest or differently retained data appears as pending or reconciliation differences | Regular-user ranges over 30 days are split to respect the upstream limit; any failed chunk fails the whole query |
| API keys | New API Token API | Live key state, quota, model restrictions, and allowed IPs; unlimited keys may have a signed negative remaining quota, which is preserved and explained as unlimited | Read on demand; plaintext is returned only for an explicit one-time reveal and is never persisted |
| Key-group usage | Current keys plus New API hourly flow attribution | Counts current keys only; deleted-key history is disclosed separately; multi-group keys count in every selected group | New usage can wait for hourly attribution, and group totals must not be added together |
| Key lookup | `/api/usage/token/`, `/api/log/token`, and `/api/status` | Quota is authoritative; call statistics summarize only the latest N records returned by New API and are not lifetime totals; display conversion uses the live New API unit | N and rate limits are configurable; the local unit is only a drift-check value and cannot override New API |

## Time ranges

- Today, last 7/30/90 days, and custom dates use inclusive bounds.
- Lifetime queries do not invent a fixed creation timestamp. Local monitor data begins at the earliest retained sample; New API data begins at its currently queryable history.
- The backend uses Unix timestamps and the browser renders the user's local timezone.
- Closed historical ranges do not poll. Live ranges refresh on the configured page schedule.

## Reconciliation

### Analytics

- `request total = New API consumption-log total`
- `quota total = New API consumption-log stat.quota`
- `model difference = live request/quota total - hourly model attribution`
- `flow difference = live request/quota total - hourly flow attribution`

A positive difference only means the live-log total is greater than hourly attribution; causes can include attribution lag, bucket boundaries, or different retention windows. A negative difference means hourly attribution is greater than currently queryable logs and can likewise come from retention or bucket boundaries. The platform does not infer one definitive cause from the sign alone. In every case, the live consumption-log total remains authoritative and the difference is rendered explicitly.

### Monitor logs

Production acceptance compares one complete interval between New API `type=2` logs and monitor SQLite after configured probe tokens are excluded. Request IDs, row counts, channels, models, duration, first-token time, and stream flags must match; duplicate request IDs are not allowed. A failed collector may preserve old data for diagnosis but must report staleness instead of claiming synchronization.

### Resources

The current value must equal the last raw sample in the selected interval. Average, maximum, and minimum must be calculated from raw samples. Chart points may be bucketed but tooltips must identify them as bucket averages. Thresholds must come from dynamic settings rather than frontend constants.

Sample coverage is calculated from raw sample count and the configured sampling interval. Time-span coverage is reported separately and must not be used as a substitute for sample completeness.

“Exceeds threshold” consistently means strictly greater than (`>`). A value equal to the threshold is not presented by the UI, periodic report, or alert state machine as over the limit.

## Release acceptance

Every release runs the complete backend suite, frontend suite and build, Playwright E2E, Docker Compose validation, image build, production health checks, same-window log reconciliation, channel-catalog reconciliation, latest-resource reconciliation, and desktop/mobile public-page checks.
