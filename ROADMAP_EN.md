# Roadmap

[简体中文](ROADMAP.md) | [English](ROADMAP_EN.md)

The roadmap follows measured requirements and failure evidence. Dates are not guaranteed.

## Current Baseline (1.0)

- External, read-only New API integration without upstream source modifications.
- Real channel probes, usage-log latency analysis, resource monitoring, and collector self-monitoring.
- WeCom, Feishu, and email delivery with UI-based real test alerts.
- New API session SSO, role mapping, emergency administration, and configuration auditing.
- Single-host Docker Compose deployment, backups, diagnostics, and container hardening.

## Next

- Publish versioned multi-architecture GHCR images and generated GitHub Releases.
- Add configuration import/export with secrets excluded by default.
- Add notification previews, quiet hours, and escalation policies.
- Add an independent external heartbeat example for full-host outage detection.
- Expand observability, recovery drills, and upgrade compatibility tests.

## Intentionally Deferred

- Prometheus, Grafana, Loki, message queues, and external time-series databases are not default dependencies.
- Multi-node control planes and complex plugin systems are not current goals.

These components will be reconsidered only when host capacity, retention, or reliability evidence requires them.
