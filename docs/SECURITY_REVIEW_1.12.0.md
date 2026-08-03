# New API Monitor 1.12.0 Differential Security Review

Review date: 2026-08-03

Baseline: `720a9d9`

Scope: data reconciliation, dynamic metric definitions, long-range self analytics, and viewer overall-status isolation

## Executive summary

| Severity | Findings |
| --- | ---: |
| Critical | 0 |
| High | 0 |
| Medium | 0 |
| Low | 0 |

Overall risk: Low after automated and production verification.

## Security invariants

1. Exact totals must not widen access: regular users remain restricted to New API self endpoints and their current session identity.
2. Long-range queries must split only fixed allowlisted self endpoints and must fail the complete request if any chunk is invalid or fails.
3. Monitor-local emergency administration must never grant New API customer-console access.
4. Viewer overall status must include only viewer-visible channel incidents, not hidden channels or administrator-only operational incidents.
5. No reconciliation response may expose credentials, upstream URLs, prompts, response bodies, or administrator-only diagnostic fields.

## Differential review

| Area | Risk | Result |
| --- | --- | --- |
| Analytics totals | Misleading billing data | Request count and quota now use consume-log APIs as the authoritative total. Hourly model and flow differences are signed and displayed separately rather than silently changing the total. |
| Upstream payload validation | Silent data corruption | Required numeric, boolean, pagination, account-identity, token, and log fields fail closed. Legitimate signed remaining quota for unlimited keys is preserved. |
| Long-range self analytics | Upstream amplification and partial data | Requests are split at New API's 30-day limit, begin at the oldest retained consume log for lifetime queries, use fixed `/self` routes, and fail closed on malformed responses. |
| Account overview | Cross-scope confusion | The page now uses self statistics for administrators as well as regular users, avoiding a mixed page with personal balances and global consumption. |
| Log statistics | Scope confusion | Quota/RPM/TPM are exposed only for all/consume log views and are labeled as consume-only; RPM/TPM explicitly use the current 60-second window. |
| Dynamic thresholds | UI drift | Slow-request and resource thresholds are returned by the authenticated backend and used by the UI. No new configuration write path was added. |
| Viewer status | Hidden-data inference | Hidden channel and operator-only incident state no longer changes the regular viewer's overall status. |
| React rendering | Untrusted labels | All New API labels continue through React text rendering; no raw HTML sink was introduced. |

## Adversarial checks

- A regular user requesting `scope=global` or another username is rejected before any upstream call.
- An administrator selecting self scope still uses `/api/data/self`, `/api/data/flow/self`, `/api/log/self`, and `/api/log/self/stat`.
- A malformed chunk response raises a 502 and discards the entire aggregation; partial totals are never returned as complete.
- Pagination metadata must match the requested page and page size; oversized pages, impossible offsets, missing expected rows, duplicate token IDs, and totals smaller than returned rows are rejected.
- The oldest-log lookup carries only the verified New API session cookie to the configured same-origin service and does not follow redirects.
- Resource and slow thresholds are numbers from validated dynamic settings; the browser cannot override backend alerting behavior.
- Reconciliation metadata contains only counts, quota differences, source labels, and timestamps.

## Residual risk

New API hourly attribution and consume-log retention can intentionally differ. The release does not invent missing history or rewrite New API data; it preserves live log totals and renders the difference. Very old self-history may require multiple 30-day upstream reads, so deployment operators should retain normal request timeouts and avoid exposing the console without authentication.

## Verification required before release

- Complete Python and frontend tests, Playwright E2E, release check, shell syntax checks, Docker Compose validation, and image build.
- Production same-window comparison of New API consume logs against monitor SQLite after configured probe-token exclusions.
- Production comparison of analytics request/quota totals against New API log APIs and explicit reconciliation of model/flow attribution.
- Production comparison of resource API latest values and extrema against raw monitor samples.
- Desktop and 390 px responsive validation for overview, analytics, logs, resources, incidents, and settings.
