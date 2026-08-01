# New API Monitor 1.11.0 Differential Security Review

Review date: 2026-08-01

Baseline: `d1f4274` (`origin/main`)

Scope: channel synchronization, dashboard authorization scope, historical queries, and browser refresh behavior

## Executive summary

| Severity | Findings |
| --- | ---: |
| Critical | 0 |
| High | 0 |
| Medium | 0 |
| Low | 0 |

Overall risk: Low after verification.

Recommendation: Approve after the production reconciliation checks in this document pass.

The review covered all 20 changed files and the four security-relevant paths: the New API management boundary, fail-closed channel snapshots, role-scoped overview data, and bounded historical queries. No authentication or authorization check was removed, no new unauthenticated endpoint was added, and all dynamic SQL values remain parameterized.

## Security invariants

1. A partial or malformed New API response must never replace the last complete channel snapshot.
2. Disabled or audience-hidden channels must not influence the corresponding viewer or administrator overview.
3. A stale collector must be presented as unknown, not as healthy or failed based on old data.
4. User-selected historical ranges must not produce unbounded response payloads or duplicate large container JSON in temporary SQLite results.
5. A slower browser response must not overwrite a newer filter or route result.

## Change analysis

| Area | Risk | Review result |
| --- | --- | --- |
| `NewAPIClient.get_channels()` | High: privileged external API and destructive snapshot reconciliation | All pages use New API's `p` contract with a maximum page size of 100. Totals must remain stable, IDs must be unique and valid, and incomplete snapshots raise an error before persistence. The maximum accepted snapshot is 10,000 channels. |
| Dashboard summary scope | High: role-based data boundary | Visibility is calculated only from enabled channel IDs, then applied to latency and incident data. Viewer payloads remove collector error details. Existing `AuthenticatedUser` and role checks remain in place. |
| Resource history query | Medium: authenticated resource exhaustion | Output is clamped to 5,000 points, normal UI requests use 1,440, future time is ignored, and large `containers_json` values no longer participate in the aggregation window. Only the latest container snapshot is attached. |
| Log and incident indexes | Medium: database migration and storage growth | Indexes match the actual filter/order pairs and are created idempotently. The measured database growth was about 20 MB on a 77 MB production-sized copy, within the configured 2 GB capacity guard. |
| Browser polling | Low: stale response and request amplification | Overlapping requests are aborted, closed historical ranges stop polling, and non-overview core refreshes are limited to at least 30 seconds. |

## Adversarial scenarios

### Truncated channel response

Attacker position: a broken, changing, or compromised upstream management response.

Attempt: return a valid first page while reporting additional records, return duplicate IDs across pages, or change `total` during pagination.

Result: synchronization raises an error and keeps the previous complete snapshot. It cannot turn a partial response into mass channel deletion.

### Hidden-channel overview disclosure

Attacker position: an authenticated viewer.

Attempt: infer hidden channel health through total counts, request totals, slow counts, incidents, or collector error text.

Result: the repository query is scoped to visible channel IDs and the viewer response strips the collector's raw error. Hidden channels do not affect the scoped overview.

### Historical-query amplification

Attacker position: an authenticated operator selecting an all-time range or deep log page.

Attempt: force SQLite to carry repeated container JSON through a window query or return an unbounded time series.

Result: numeric resource fields are bucketed with a bounded output, container JSON is fetched once, and supporting indexes reduce deep filtered scans. Authorization, retention, and the database capacity guard remain unchanged.

### Stale browser response

Attacker position: a slow or intentionally delayed network response.

Attempt: let an old filter request finish after a new one and overwrite the UI.

Result: route, filter, and refresh changes abort the previous request. Only the current request can clear loading state or update data.

## Verification evidence

- Backend: 204 deterministic unit and integration tests passed.
- Frontend: 36 tests passed; TypeScript and production Vite build passed.
- Release checks: repository release check and Docker Compose static configuration passed.
- Production-sized database: 12 concurrent workers completed 240 mixed queries with zero errors; median 95 ms, P95 463 ms, maximum 915 ms.
- Same database copy: all-time resources completed in about 122 ms; an earlier implementation failed the 7-day query because SQLite temporary storage was exhausted.
- Deep all-time log offset on the same data improved from about 102 ms to 20 ms median after the index migration.
- Secret scan of tracked changes found no environment files, tokens, cookies, databases, or backups.

## Historical context and blast radius

The modified channel client is called only by the channel synchronization worker. Snapshot replacement remains centralized in `StateStore.sync_channels()`. Dashboard summary has two HTTP call paths: the authenticated dashboard endpoint and the internal health check. Resource history has one authenticated API endpoint. This keeps the direct blast radius small while preserving the existing authentication boundaries.

No security validation from prior commits was removed or weakened. The two existing CodeQL alerts on the baseline concern an administrator-only diagnostics response and the one-time local bootstrap password display; neither data flow was introduced or expanded by this change.

## Residual risks

- Management credentials can still expire or be revoked. The platform now exposes synchronization freshness and fails closed, but credential renewal remains an operational responsibility.
- SQLite offset pagination remains linear at very large offsets. The current indexes are sufficient for the measured workload; keyset pagination should be considered only if retained log volume grows by an order of magnitude.
- An all-time resource query still scans the retained numeric samples. Retention and the database capacity guard are therefore part of the safety model.
- Live acceptance must reconcile source channel IDs and statuses against the monitoring database after deployment; a healthy HTTP response alone is not sufficient.

## Methodology

Strategy: focused differential review for a medium-sized codebase.

Techniques: baseline diff review, call-site tracing, New API source-contract verification, authorization-boundary review, malformed-response tests, production-sized A/B benchmarks, concurrent query testing, release checks, and tracked-secret inspection.

Confidence: High for the changed paths and current workload; medium for future workloads substantially larger than the configured retention and capacity assumptions.
