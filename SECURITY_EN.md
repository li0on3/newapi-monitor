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

API key usage lookup is admin-only by default and can be lowered only to operators. It uses POST forwarding, fixed upstream paths, and per-user/source rate limits. Raw keys are not stored in the database, URLs, audits, or API responses.

The New API pages and legacy key-usage lookup are separate trust boundaries:

- Only a New API session verified through `/api/user/self`, with a returned user ID matching the request header, is accepted. Emergency administrators are denied.
- The BFF exposes only fixed overview, analytics, token, and log APIs. It has no configurable path, arbitrary headers, or generic reverse-proxy function.
- The original New API role selects global versus self-only APIs; monitor role mappings cannot elevate upstream permissions.
- New API Admin and Root accounts map to monitor administrators by default. Regular accounts can access only the viewer-filtered Monitor Overview plus the four personal New API pages. The standalone official-status page, monitor logs, resources, incidents, key lookup, channel settings, and system settings still require an operator or administrator on the server.
- Viewer channel APIs use a field allowlist: original channel names, probe configuration, recent request logs, and raw upstream error bodies are excluded, and future administrative fields are not exposed by default.
- Plaintext keys are available only through a rate-limited one-time POST response with caching disabled. They are never written to databases, logs, audit records, URLs, or browser storage.
- Custom key-group membership is isolated by `user_id + token_id + group_id`. Every member write revalidates key ownership through the current session, and deleting a group cannot delete keys or their other memberships.
- The single-group membership migration to a composite multi-group key runs inside an explicit SQLite transaction. Any error restores the old table so the migration can be retried safely after repair.
- Regular-user logs strip administrator metadata, audit metadata, stream status, and channel names, and regular-user time ranges are capped at 30 days.
- Mutations require the same-origin `X-Monitor-Request: 1` header and produce redacted audit records.
- Production must host the New API pages on the same browser Origin as New API; page, setup, and Key-lookup requests never follow redirects with credentials.
- Explicit sign-out uses a dedicated HttpOnly monitor cookie to suppress automatic SSO. The monitor must not delete or modify the New API `session` cookie, and SSO resume requires an explicit same-origin-verified POST.
- A single-layer Nginx proxy should overwrite `X-Real-IP` and `X-Forwarded-For` instead of forwarding caller-supplied address chains, or source auditing and rate limits can be spoofed.

An on-host monitor cannot detect a full host outage, network loss, or complete disk failure. Use an independent external heartbeat for those cases.
