# Changelog

[简体中文](CHANGELOG.md) | [English](CHANGELOG_EN.md)

## Unreleased

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
