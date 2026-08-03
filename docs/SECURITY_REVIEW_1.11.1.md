# New API Monitor 1.11.1 Differential Security Review

Review date: 2026-08-03

Baseline: `f76e65d` (`main`)

Scope: analytics scope selection, quota reconciliation, and visible flow aggregation

## Executive summary

| Severity | Findings |
| --- | ---: |
| Critical | 0 |
| High | 0 |
| Medium | 0 |
| Low | 0 |

Overall risk: Low after verification.

Recommendation: Approve after the production reconciliation and responsive browser checks pass.

The change preserves New API as the source of truth and adds no unauthenticated endpoint, credential handling, database write, or upstream target selection. It narrows analytics queries through an explicit bounded scope and changes only the presentation aggregation of already-authorized data. All 18 changed or added files were reviewed; the repository has 135 tracked files, so the focused strategy covered the complete diff plus one-hop callers of the high-risk paths.

## Security invariants

1. A regular user must never select the global analytics scope or another username.
2. Monitor-local emergency administration must not grant access to New API customer-console data.
3. An administrator selecting the current-account scope must use New API's self endpoints.
4. Invalid or oversized scope values must be rejected before an upstream request is made.
5. Hidden channel and node dimensions may be collapsed for presentation, but quota must never be discarded or counted twice.

## Change analysis

| Area | Risk | Review result |
| --- | --- | --- |
| Analytics BFF route | High: role-scoped management data | Existing session authentication and New API identity checks remain mandatory. Global scope requires the verified New API source role, and username filtering is rejected for self scope. The scope query is limited to `auto`, `global`, or `self` and six characters. |
| New API console client | High: privileged upstream endpoints | Administrators use global endpoints only for global or auto scope. Current-account and regular-user queries use fixed `/self` paths. No caller-controlled URL or method was introduced. |
| Quota reconciliation | Medium: misleading billing totals | The authoritative displayed total is the maximum of log statistics, model attribution, and flow attribution for the same interval. The unassigned remainder is shown explicitly and never added back into the source totals. |
| Flow presentation | Medium: accidental double count | Rows are merged only by the visible identity, group, and model dimensions. Count, token, and quota fields are normalized to non-negative finite values before summation; overflow rows retain the full remainder. |
| React rendering | Low: untrusted labels | User, token, group, and model labels continue through React text rendering. No raw HTML rendering or script-capable sink was added. |

The authorization boundary is at `dashboard_app.py:1350-1389`. The route still requires `AuthenticatedUser` at line 1353 and a verified New API console identity at line 1361; the new global-role and self-username checks are at lines 1363-1368. Fixed upstream path selection and the repeated role check are at `dashboard_newapi_console.py:303-337`. Quota reconciliation is at lines 334-354. Visible flow aggregation is isolated to `web/src/console/utils.ts:10-45` and has one UI caller at `web/src/console/ConsoleAnalytics.tsx:65`.

## Adversarial scenarios

### Global-scope escalation

Attacker position: an authenticated non-administrator.

Attempt: call the analytics BFF with `scope=global` or supply another username.

Result: the BFF returns 403 before calling New API. The client repeats the global-role check as defense in depth.

### Local-role confusion

Attacker position: a monitor emergency administrator without a verified New API session.

Attempt: use the local monitor role to read customer-console analytics.

Result: the existing console identity boundary rejects the request because customer-console access requires a New API SSO identity and source role.

### Scope parser abuse

Attacker position: an authenticated caller sending case variants, oversized strings, or unknown values.

Attempt: influence upstream path selection through the scope value.

Result: FastAPI validates the bounded enum before the handler runs, and the client independently rejects values outside the same allowlist. Upstream paths remain fixed constants.

### Hidden-dimension inflation

Attacker position: a viewer comparing flow rows with the reported total.

Attempt: interpret channel- or node-split records as distinct visible destinations, or lose quota outside the top 12 rows.

Result: hidden dimensions are collapsed once, and every row after the visible limit is represented by a single aggregate remainder. The model and flow panels both reconcile to the same interval total.

## Verification evidence

- Backend unit and endpoint tests cover administrator self scope, regular-user global rejection, username rejection in self scope, and log-to-attribution reconciliation.
- Frontend unit tests cover hidden-dimension aggregation, deterministic ordering, non-negative normalization, and overflow totals.
- Component and browser tests verify explicit scope selection and that both ranking panels close to the same displayed total.
- Production acceptance requires a same-window comparison among New API logs, model attribution, flow attribution, and the rendered dashboard.
- Tracked-change secret scanning is required before release; no deployment credentials or session artifacts belong in this change.

## Blast radius and residual risks

The direct blast radius is one authenticated monitor API route, one New API client method, and one customer-console page. Repository search found one production caller for each layer: `ConsoleAnalytics` calls `consoleApi.analytics`, the API wrapper calls `/api/console/analytics`, and that route calls `NewAPIConsoleClient.analytics`. New API source code, billing, logs, quota aggregation, and channel routing are unchanged.

Git history traces the original analytics route, authenticated-user dependency, console identity check, and client method to `b2787d7`. The flexible-range extension came from `6f4d7c9`. No prior authentication or validation line was removed; the diff only adds narrower checks and an explicit scope parameter.

New API periodically materializes analytics attribution. Very recent log quota can therefore exceed model or flow attribution for a short period. The page now labels that remainder instead of presenting inconsistent totals. If log retention is shorter than attribution retention, the maximum-of-sources rule still preserves the complete available total, but source history policies must remain aligned for long-term reconciliation.

## Methodology

Strategy: focused differential review.

Techniques: authorization call-chain tracing, fixed-path review, malformed-scope tests, aggregation invariants, source-total reconciliation, frontend rendering review, and production acceptance planning.

Confidence: High for the changed authorization and aggregation paths; medium for future data-retention configurations that differ substantially from the current deployment.
