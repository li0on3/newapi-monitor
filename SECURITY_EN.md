# Security Policy

[简体中文](SECURITY.md) | [English](SECURITY_EN.md)

## Supported Versions

Security fixes are guaranteed only on the latest main branch. Production deployments should pin a verified commit or release instead of tracking uncontrolled `latest` code.

## Reporting a Vulnerability

Use a private GitHub Security Advisory. Do not publish tokens, cookies, server addresses, raw logs, or directly exploitable steps in a public issue.

Include the affected version or commit, impact and prerequisites, a minimal reproduction, and proposed mitigations when possible.

## Deployment Baseline

- The monitor binds to `127.0.0.1` by default and must be exposed through an HTTPS reverse proxy.
- Configure an exact `DASHBOARD_ALLOWED_HOSTS`; never use `*` in production.
- Keep `.env` at permission mode `0600` and never commit it.
- Back up `MONITOR_SECRET_KEY`; it encrypts sensitive database settings.
- Expose Docker only through the restricted Socket Proxy. Never mount the production Docker socket directly into the monitor container.
- Keep emergency administrator credentials separate and rotate them regularly.
- Run `python manage.py doctor` and inspect `git status --ignored` before publishing or deploying.
- The one-click setup token is valid for only 15 minutes and is shown once. Do not expose the direct monitor port publicly before setup is complete.
- The New API administrator password entered in the setup wizard is only exchanged for a management token and dedicated probe key; it is not persisted. Confirm `/api/setup/status` returns `required: false` after setup.
- `monitorctl backup` includes both the database and environment encryption key. Treat every backup as production credentials and store it encrypted offline.

## Trust Boundaries

Monitor administrators can change New API endpoints, probe rules, and notification credentials. Treat monitor administrators as infrastructure-privileged identities.

API key usage lookup is admin-only by default. It uses POST forwarding, fixed upstream paths, and per-user/source rate limits. Raw keys are not stored in the database, URLs, audits, or API responses.

An on-host monitor cannot detect a full host outage, network loss, or complete disk failure. Use an independent external heartbeat for those cases.
