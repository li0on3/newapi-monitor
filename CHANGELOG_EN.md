# Changelog

[简体中文](CHANGELOG.md) | [English](CHANGELOG_EN.md)

## Unreleased

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
