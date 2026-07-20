# New API External Monitoring Platform

[简体中文](README.md) | [English](README_EN.md)

[![CI](https://github.com/li0on3/newapi-monitor/actions/workflows/ci.yml/badge.svg)](https://github.com/li0on3/newapi-monitor/actions/workflows/ci.yml)
[![CodeQL](https://github.com/li0on3/newapi-monitor/actions/workflows/codeql.yml/badge.svg)](https://github.com/li0on3/newapi-monitor/actions/workflows/codeql.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

An independently deployed monitoring and alerting platform for New API. It does not modify New API source code. Data is collected through read-only management APIs, real relay probes, real usage logs, and a restricted Docker Socket Proxy. The project is designed for single-host and small-scale New API installations.

## Features

- Automatically synchronizes enabled New API channels and hides disabled channels.
- Performs real probes through OpenAI Responses, Chat Completions, and Anthropic Messages.
- Analyzes total latency, time to first token, users, tokens, models, and channels from real New API usage logs.
- Queries quota, model restrictions, and recent calls by API key without persisting the key or putting it in URLs or audit records.
- Alerts when 3 of the latest 5 or 5 of the latest 10 requests exceed the latency threshold; a single critical sample can alert immediately.
- Monitors host CPU, memory, disk, and Docker container status, resource use, restarts, and OOM events.
- Detects stale collectors so a live dashboard cannot silently hide a stopped collection pipeline.
- Supports email, WeCom applications, WeCom group bots, Feishu applications, and Feishu group bots with independent delivery and real test alerts.
- Reuses New API sessions for SSO, with role mapping, an emergency administrator, login throttling, and configuration auditing.
- Stores runtime configuration in the monitor database and never writes configuration back to New API.
- Maintains separate channel visibility lists for administrators/operators and regular viewers.

## Quick Start

### 1. Initialize secure configuration

Linux:

```bash
git clone <your-repository-url> newapi-monitor
cd newapi-monitor
python3 manage.py init
```

Windows:

```powershell
git clone <your-repository-url> newapi-monitor
Set-Location newapi-monitor
python manage.py init
```

The initialization command creates a random emergency administrator password, a `MONITOR_SECRET_KEY`, and a permission-restricted `.env` file. Store the one-time password in a password manager.

### 2. Configure `.env`

Required values:

- `NEW_API_BASE_URL`
- `NEW_API_ACCESS_TOKEN`
- `NEW_API_USER_ID`
- At least one notification channel, configured either through environment variables or later in **System Settings → Notification Center**
- `DASHBOARD_ALLOWED_HOSTS` with the actual monitoring hostname

Real probes should normally be enabled and configured from the Channel Settings page after startup.

### 3. Run preflight checks

```bash
python3 manage.py doctor
```

### 4. Start

```bash
./install.sh
```

Or:

```bash
docker compose build monitor
docker compose up -d
docker compose ps
```

The service listens on `127.0.0.1:18081` by default. Publish `/monitor/` through an HTTPS reverse proxy. Every nested path under `/monitor/` must be forwarded to the monitoring service.

```text
/monitor/                       Overview
/monitor/key-usage              API key usage lookup
/monitor/logs                   Usage logs
/monitor/resources              Host and container resources
/monitor/incidents              Incidents
/monitor/channels               Channel settings
/monitor/system                 System settings
/monitor/system/notifications   Notification center
```

Every configured notification channel can trigger a real test alert from the UI, even while the channel is disabled. Unsaved changes must be saved first so the test always uses the active configuration.

## Health Check

```bash
curl -fsS http://127.0.0.1:18081/api/health
```

Healthy response:

```json
{"status":"ok","timestamp":1784476800}
```

HTTP 503 is returned when SQLite is unavailable, the monitoring worker has stopped, or a channel sync, probe, log, or resource collector has exceeded its dynamic stale threshold.

## Default Policy

| Item | Default |
| --- | ---: |
| Channel synchronization | 5 seconds |
| Usage log synchronization | 30 seconds |
| Resource sampling | 15 seconds |
| Real channel probes | 5 minutes |
| Slow request | Any latency metric over 60 seconds |
| Window alert | 3 of 5, or 5 of 10 |
| Single critical alert | Over 180 seconds |
| Resource alert | Threshold sustained for 180 seconds |
| Retention | 90 days |

## Data and Security

- Prompt and response bodies are not stored. Only monitoring metrics and bounded error summaries are retained.
- New API tokens, relay tokens, SMTP passwords, application secrets, webhook URLs, and signing secrets are encrypted in SQLite with `MONITOR_SECRET_KEY`.
- The production container runs as non-root UID `10001`, with a read-only root filesystem and all Linux capabilities removed.
- Docker access is restricted through a read-only Socket Proxy; the monitor does not mount the Docker socket directly.
- State-changing APIs require authentication, role checks, strict Pydantic schemas, and a same-origin request header.
- Regular New API users can only see the overview by default. Operators can inspect logs, resources, incidents, and channels. Monitor administrators can manage settings and role mappings.
- API key usage lookup is admin-only by default, rate-limited, and only calls New API read-only endpoints.
- Configuration and role changes are audited, with secrets always masked.

See [SECURITY_EN.md](SECURITY_EN.md) for the security boundary and [ROADMAP_EN.md](ROADMAP_EN.md) for planned work.

## Backup

```bash
python3 manage.py backup
```

Backups use the SQLite Online Backup API and run an integrity check. Restoring encrypted configuration also requires the original `MONITOR_SECRET_KEY`. Never commit backups, `.env`, or reverse-proxy credentials.

## Upgrade and Rollback

```bash
python3 manage.py backup
git pull --ff-only
python3 manage.py doctor
docker compose build monitor
docker compose up -d
```

Pin production deployments to a Git tag or release. Rebuild the image after rollback and restore the matching database backup before a major-version rollback.

## Development Verification

```bash
python -m pip install -r requirements.txt
python manage.py release-check
python -m unittest discover -s tests -v

cd web
bun install --frozen-lockfile
bun run build

cd ..
docker compose --env-file .env.example config --quiet
docker build -t newapi-monitor:test .
```

## Design Principles

1. **Measure the real target:** channel health is based on real relay behavior, not connectivity alone.
2. **Monitor the monitor:** every collector records freshness and produces failure and recovery incidents.
3. **Least privilege:** read-only APIs, dedicated probe tokens, non-root containers, loopback binding, and minimal Docker access.
4. **Failure isolation:** monitoring failures must never modify or block New API traffic.
5. **Avoid premature complexity:** SQLite and a single-process scheduler are intentional for small deployments; external time-series databases and queues should only be introduced when capacity or reliability requirements justify them.

An on-host monitor cannot detect a complete host or network outage. Add an independent external HTTP heartbeat when host-down alerting is required.
