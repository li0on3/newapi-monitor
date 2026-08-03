# Changelog

[简体中文](CHANGELOG.md) | [English](CHANGELOG_EN.md)

## Unreleased

## 1.11.1 - 2026-08-03

### Fixed

- Analytics spend totals now use New API consume-log statistics as a live fallback. Requests not yet present in the model and flow projections appear as pending attribution instead of making the total temporarily low for up to one export interval.
- Usage flow now merges rows by the account/key, group, and model dimensions actually shown in the UI. Hidden node and channel dimensions no longer look like duplicate flows, and rows beyond the first 12 are closed into one Other total.

### Changed

- New API administrators can explicitly switch Analytics between Global and Current Account. Regular users remain server-enforced to Current Account, and username filtering is accepted only in Global scope.
- Model ranking and usage flow now show the same live total in their headers and distinguish attributed spend from the newest pending-attribution amount.

## 1.11.0 - 2026-08-01

### Fixed

- Channel synchronization now follows New API's supported `p` pagination parameter and fails closed on changing totals, truncated pages, duplicate IDs, or oversized responses, preventing channels beyond the first page from being silently omitted.
- A stale channel-catalog collector is no longer presented as current. The overview marks its channels unknown and gives administrators a direct synchronization-failure explanation.
- Resource history no longer carries container JSON through a SQLite window sort, fixing `database or disk is full` on 7-day and lifetime queries with the 32 MiB temporary filesystem.

### Changed

- Added ordered composite indexes for log time, channel, model, user, and probe history. On a production database copy, an 80,000-row deep page dropped from about 102 ms median to 20 ms.
- Resource history carries the end-of-range container snapshot only on its final point while numeric charts remain bounded to at most 1,440 time buckets, reducing sort and response cost.
- Closed historical ranges no longer poll. Log filters, resource views, and incidents cancel obsolete in-flight requests, while non-overview core status refreshes every 30 seconds to prevent request pileups and stale-response races.
- The UI now reports actual chart resolution and its footer matches the active 15-of-20 first-token and 5-of-10 channel-failure alert policy.

### Security

- Paginated snapshots accept at most 10,000 channels, malformed or incomplete snapshots never replace the last valid data, and raw collector errors remain hidden from regular users.

## 1.10.0 - 2026-07-27

### Changed

- Enabled a low-noise severe-experience policy by default. Only sustained channel unavailability, sustained severe first-token latency, and their recovery notices are sent externally; resource, collector, New API management, and provider-status anomalies remain in the incident center.
- First-token latency now triggers only when at least 15 of the latest 20 measurable requests exceed 15 seconds. Single long total durations, 3-of-5, 5-of-10, and periodic reminders no longer alert.
- Channel unavailability now requires 5 consecutive failures or at least 5 failures in the latest 10 probes, and recovers after 5 consecutive successes.
- Alert content now explains the trigger, user impact, sample window, latest error, and recovery condition consistently across all notification destinations.

### Fixed

- Legacy alert state is reset to the new policy on first load, preventing historical two-failure or single-long-request state from firing immediately after upgrade.

### Added

- Added a shared time-range selector with today, last 7/30/90 days, custom dates, and lifetime “since creation” queries.
- Usage logs, analytics, key-group usage, monitor logs, host resources, incidents, and alert deliveries now have independent query ranges.

### Changed

- Removed the hard-coded 366-day administrator and 30-day user history caps. Long ranges remain bounded through server pagination or resource bucketing instead of loading every detail row at once.
- Added historical pagination to monitor logs and automatic time-bucket aggregation to resource history queries.

## 1.9.0 - 2026-07-26

### Added

- Added an administrator Alert Delivery Center with pending, sending, delivered, dead-letter, and cancelled filters plus full errors, attempt counts, retry schedules, message bodies, and related incidents.
- Added single and bulk retry, cancellation, and dead-letter recovery with state restrictions, bounded batches, and configuration audit records.
- Added incident acknowledgement, global quiet hours, and scheduled per-channel maintenance windows. Critical alerts can bypass quiet hours, and probes resume automatically after maintenance.
- Added real FastAPI + SQLite + React Playwright E2E coverage for login, delivery operations, incident acknowledgement, dynamic settings, and channel maintenance configuration.

### Changed

- Settings now submit only changed fields so inactive notification-channel placeholders cannot block unrelated updates.
- Delivery records now carry priority, and quiet-hour deferrals return to pending without consuming an attempt.

### Security

- Delivery bodies and failure details are administrator-only. Mutations still require the same-origin request marker and reject sending or delivered records.
- Retry and cancellation use immediate SQLite transactions and conditional status updates to prevent races with the delivery worker.

## 1.8.0 - 2026-07-25

### Added

- Added a durable SQLite notification outbox. Incidents are persisted before independent email, WeCom, and Feishu delivery; failures use exponential backoff, survive restarts, and become dead letters after the configured attempt limit.
- Added database, WAL, pending-delivery, and dead-letter health signals, with independent retention for raw samples, resolved incidents, and delivery records.
- Added Playwright browser E2E coverage for administrator deep links, viewer authorization boundaries, and persistent sign-out behavior.

### Changed

- Split monitor state machines, protocol validation, persistence, and delivery into focused core modules, and moved the system-settings catalog out of the main React component.
- Latency windows and reminders now advance only on new log samples. Five newest healthy samples recover an incident even when an older critical sample remains in retained history.
- Resource trends are bucketed across the full requested range, so the 24-hour view no longer truncates to a fixed number of newest rows.

### Fixed

- Prevented malformed channel-sync responses from becoming an empty snapshot, kept slow but valid real probes available, and corrected first-restart and OOM transition alerting.
- Disabled, deleted, maintenance, or alert-disabled channels now reconcile open incidents, while restart history no longer leaves overall health permanently degraded.

## 1.7.0 - 2026-07-25

### Added

- Added an interactive model-stacked request trend to Analytics, with Requests, Tokens, and Spend metrics, top-model grouping, peak and average summaries, a model legend, and hover details.
- Upgraded custom key groups to many-to-many membership. One API key can belong to multiple reporting groups, and clicking a group card opens a searchable bulk member editor.
- Added Monitor Overview to the regular-user business navigation, backed by the separately configurable viewer channel scope.

### Changed

- Today, Last 7 Days, and Last 30 Days presets now query immediately, with request sequencing so a slower stale response cannot overwrite the latest filter.
- The displayed data range now always comes from the timestamps returned by the API. Single-bucket trends render a visible point, and the chart supports keyboard, touch, and an accessible data table.
- Regular-user screens now use a neutral API Service Center identity and no longer expose New API branding, module labels, or sign-in copy. Administrators and operators retain the full technical view.
- Group analytics now state their current multi-membership attribution: overlapping groups each include the key, while account totals remain deduplicated and group totals must not be added together.
- Existing single-group membership tables migrate automatically to the many-to-many composite key on startup. Existing memberships need no manual work, and legacy `group_id` requests remain compatible.
- The browser title and account-service errors visible to regular users now use neutral wording. The “Other models” bucket uses a collision-proof internal key and cannot merge with a real model of the same name.
- When the browser has no existing account session, the neutral service-center login stays in place and asks the user to complete account sign-in instead of automatically redirecting to a branded upstream page.

### Security

- Regular users can read only channel cards and summary status enabled for the viewer audience. Monitor logs, machine resources, incidents, key lookup, and system settings still require an operator or administrator on the server.
- Viewer channel APIs use a strict field allowlist and never return internal probe configuration, original channel names, recent request logs, or raw upstream error bodies.
- Group-member updates continue to revalidate complete key ownership through the current session and remain isolated by `user_id`. Deleting a group never deletes keys or their other memberships.
- The single-group to multi-group database migration runs inside an explicit transaction, rolls back completely on failure, and can be retried safely after data repair.

## 1.6.1 - 2026-07-25

### Changed

- New API Admin and Root accounts now both map to monitor administrators. Regular New API users are still identified live from their login session and never need duplicate monitor accounts.
- Regular users now land on the New API Overview and see only Overview, Analytics, API Keys, and Usage Logs for their own account.
- API-key usage lookup now requires at least the operator role. Legacy `viewer` settings are interpreted as operator to keep configuration aligned with the enforced API boundary.
- Overview visibility settings now present only the monitor channel list used by administrators and operators. Legacy viewer-visibility fields remain in the database and API for migration-free compatibility.

### Security

- Monitor Overview, channels, official status, monitor logs, resources, incidents, key lookup, and configuration APIs now reject regular users on the server instead of relying on hidden navigation.
- A regular user opening a monitor deep link is redirected to the first available personal New API page and cannot bypass the boundary by editing the URL.

## 1.6.0 - 2026-07-25

### Fixed

- Fixed explicit monitor sign-out being undone on refresh by a remaining New API session. The signed-out state now persists within the monitor until the user explicitly selects “Sign in with New API”.
- Emergency administrator sign-in now reloads the server-issued identity and no longer fabricates an administrator state in the frontend when identity retrieval fails.

### Changed

- Removed the umbrella Customer Console module and nested sidebar. Overview, Analytics, API Keys, and Usage Logs are now independent top-level modules while retaining the existing deep links and BFF data contracts.
- Split primary navigation into `New API` and `Monitor` workspaces, with explicit labels such as Monitor Overview and Monitor Logs to avoid ambiguity with New API business pages.
- Renamed the settings entry to “New API Pages” without changing persisted setting keys or requiring a configuration migration.

### Security

- Added a dedicated HttpOnly, SameSite=Lax monitor SSO-suppression cookie. Monitor sign-out never deletes, changes, or impersonates the New API `session` cookie.
- Re-enabling New API SSO requires an explicit same-origin-verified POST, preventing cross-site requests from silently clearing the signed-out state.

## 1.5.0 - 2026-07-25

### Added

- Added System, Light, and Dark theme modes with browser preference detection, live operating-system theme updates, and persistent local selection.
- Added the same theme control to sign-in, first-run setup, and authenticated screens, with pre-render theme initialization to prevent a light/dark flash.

### Changed

- Aligned the complete visual language with the New API default frontend: blue primary color, semantic surfaces and borders, a compact header, workspace sidebar, and consistent cards, forms, tables, dialogs, and status treatments.
- Replaced the legacy hard-coded dark palette with shared semantic light/dark tokens while preserving every monitoring, configuration, Customer Console, and deep-link behavior.
- Improved desktop, tablet, and mobile navigation density, focus visibility, reduced-motion handling, and light-theme readability.

### Security

- Loads the pre-render theme bootstrap from a same-origin static asset so the existing strict Content Security Policy remains unchanged.

## 1.4.0 - 2026-07-25

### Added

- Added independent key groups to the Customer Console API Keys page. Users can create, rename, color, delete, and bulk-assign their own keys without touching native New API model routing or billing groups.
- Added 1-day, 7-day, and 30-day per-key usage plus group totals, joined to real `/api/data/flow/self` data by immutable New API `token_id`, including requests, quota, tokens, and called models.
- Added local group-membership tables, per-user isolation constraints, group-operation audits, key-ownership revalidation, and contract/regression coverage.

### Changed

- Reworked the API Keys page into account totals, grouped usage, and per-key detail, with an explicit visual distinction between monitor key groups and native New API routing/billing groups.
- Group totals use current membership: after moving a key, the selected period is recalculated under its current group instead of presenting historical membership that New API cannot reconstruct.

### Security

- Groups and memberships are isolated by the current New API `user_id`. Assignment re-fetches the full key list through the current Session so another user's token ID cannot be inserted into the caller's groups.
- Usage always calls the self-scoped `/api/data/flow/self` endpoint, even for New API administrators, because this page reports personal key usage rather than global administrator analytics.

## 1.3.0 - 2026-07-24

### Added

- Added a standalone Customer Console with Overview, Analytics, API Keys, and real Usage Logs, without modifying New API source code.
- Added a fixed allowlist BFF that only uses the verified current user's New API session and user ID. It never substitutes the monitor management token for customer identity and exposes no generic proxy.
- Added API key create, edit, enable/disable, delete, batch delete, and rate-limited one-time reveal. Plaintext keys are never persisted in the database, logs, audit records, or frontend storage.
- Added dynamic settings for the console master switch, minimum monitor role, per-page switches, default query range, write rate limit, and reveal rate limit.
- Added console operation auditing, New API identity-ID consistency checks, the regular-user 30-day query boundary, and source-role-based global/self data scope.
- Added deep links for `/monitor/console`, `/monitor/console/analytics`, `/monitor/console/keys`, `/monitor/console/logs`, and `/monitor/system/console`.

### Changed

- The application now understands the `/monitor/*` prefix directly, so source runs and reverse-proxy deployments use the same URLs without a separate path-stripping rule.
- Frontend CI runs Bun unit tests before the production build, and the Docker image includes the Customer Console BFF module.

### Security

- Emergency administrators are explicitly denied Customer Console access. Monitor roles only control entry visibility and cannot elevate upstream New API permissions.
- Customer-data APIs use `Cache-Control: no-store`; mutations keep the same-origin verification header and enforce bounded upstream routes, fields, response sizes, and timeouts.
- Regular-user log responses strip administrator metadata, audit metadata, stream status, and channel names. CSV export neutralizes spreadsheet formula injection.
- Customer Console, setup, and Key-lookup requests no longer follow HTTP redirects with credentials, and their response sizes are bounded.
- The reverse-proxy example overwrites the client address instead of trusting caller-supplied `X-Forwarded-For`, preserving audit and rate-limit integrity.

## 1.2.2 - 2026-07-23

### Fixed

- Accept OpenAI status summaries that omit the `incidents` field when no incident is active, preventing repeated provider-collector failures and false degraded platform health.

## 1.2.1 - 2026-07-22

### Changed

- Moved the large OpenAI status section from the overview to `/monitor/upstream-status`; the overview now keeps only a compact contextual hint so channel cards remain primary.
- Established real local channel probes as the primary decision signal and OpenAI global status as secondary context, preventing unrelated official incidents from looking like local channel failures.
- Even when administrators opt into official-status influence, only degraded workload-relevant components can affect `OVERALL STATUS`.
- Refined desktop, narrow-screen, and mobile navigation so the additional page never causes wrapped or overlapping menu items.

## 1.2.0 - 2026-07-22

### Added

- Integrated OpenAI's official status feed for platform state, component health, active incidents, and official update timelines.
- Added a dedicated OpenAI status section to the overview; it is excluded from local channel overall health by default to avoid misclassifying upstream advisories as local failures.
- Added an Upstream Status settings page for polling, timeout, alert impact, consecutive confirmation, component scope, role visibility, and live connection testing.
- Correlated official incidents with local OpenAI-model channel health and exposed provider filtering, details, and recovery evidence in the incident workspace.

### Security

- Restricted official status collection to a hard-coded HTTPS endpoint with response-size, timeout, and schema validation to prevent configurable-URL SSRF exposure.

## 1.1.0 - 2026-07-22

### Added

- SHA-256-verified one-click Linux installer using pinned multi-architecture GHCR images and loopback binding by default.
- First-run setup wizard with a 15-minute one-time token, automatic New API credential exchange, and explicit-token mode.
- `monitorctl` lifecycle commands for status, logs, diagnostics, online backup, update, rollback, emergency admin reset, setup-token renewal, and safe uninstall.
- GitHub Releases now attach the installer, deployment bundle, and checksum.

### Security

- The New API administrator password is only used in memory to exchange tokens and is never persisted, logged, or returned.
- The setup endpoint closes after completion and is protected by a token hash, expiry, and failed-attempt throttling.

### Added

- Collector freshness checks for channel synchronization, probes, usage logs, and resource sampling.
- Collector failure/recovery incidents, detailed runtime status, and degraded HTTP 503 health checks.
- Encrypted sensitive settings, host allowlists, and request validation for state-changing APIs.
- Non-root containers, resource limits, read-only filesystems, and a restricted Docker Socket Proxy.
- Initialization, deployment diagnostics, SQLite online backups, CI, CodeQL, Dependabot, and secret scanning.
- An incident investigation workspace with filters, timelines, trigger causes, recovery evidence, and resolution metrics.
- Independent channel visibility and overall-status calculation for administrators/operators and regular viewers.
- Unified email, WeCom application/bot, and Feishu application/bot notification delivery.
- UI-based notification configuration and real per-channel test alerts.
- API key quota, model restriction, and recent-call lookup with role and rate controls.
- History API routing with deep links, refresh support, and browser back/forward navigation.
- Chinese and English README, contribution guide, security policy, changelog, and roadmap.
- Tag-based GitHub Releases and multi-architecture GHCR images with SBOM and provenance.

### Fixed

- Moved channel probing to an independent worker with bounded concurrency so slow probes no longer block log, resource, or channel-sync collection.
- Added consecutive failure/recovery confirmation and downgraded transient 5xx, 429, and timeout failures to reduce alert flapping.
- Collapsed common multi-channel authentication or group-permission failures into one probe-credential incident.
- Recorded channel-sync freshness directly in its worker to prevent false stale-collector alerts caused by delayed queue draining.
- Corrected New API usage-log pagination to use the `p` parameter.
- Prevented channel-card timestamps from overlapping navigation controls.
- Scoped overview health, request statistics, and incidents to channels visible to the current audience.
- Reconciled stale container incidents after the container returned to a healthy running state.
- Kept original incident trigger details when recovery information is recorded.
