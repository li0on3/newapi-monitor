# Contributing

[简体中文](CONTRIBUTING.md) | [English](CONTRIBUTING_EN.md)

## Development Environment

- Python 3.13+
- Bun 1.3+
- Docker Engine and Docker Compose

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
cd web
bun install --frozen-lockfile
bun run build
```

## Change Requirements

- Add a failing regression test before implementing a feature or bug fix.
- Never commit `.env`, databases, backups, cookies, tokens, production domains, or real credentials.
- Keep the monitor external. Changes must not require modifications to New API core code.
- Every new collector must expose last-success time, failure count, stale threshold, failure incident, and recovery incident.
- New APIs must require authentication by default. State-changing APIs must use explicit Pydantic schemas and request validation headers.
- Frontend changes must pass TypeScript and the production build.
- Deployment changes must pass `docker compose config --quiet` and a container image build.
- Update both Chinese and English documentation when behavior, configuration, security boundaries, or deployment steps change.

See the pull request template for the required verification and second-order impact checklist.
