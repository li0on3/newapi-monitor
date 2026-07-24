# Customer Console Architecture

[简体中文](CUSTOMER_CONSOLE.md) | [English](CUSTOMER_CONSOLE_EN.md)

The Customer Console is an external New API user interface hosted by the monitor. It does not modify New API source code, duplicate user/token/quota/log tables, or replace New API authentication and billing.

## Request flow

```text
Browser -> fixed monitor BFF -> fixed New API endpoints
           session + verified user_id
```

1. The browser sends the New API `session` cookie and `New-Api-User` header.
2. The monitor verifies the session, account status, and matching user ID through New API `/api/user/self`.
3. The BFF forwards only the current session and verified user ID to code-defined New API endpoints.
4. New API remains responsible for data scope, token ownership, quota validation, and every mutation.

To reuse the browser's New API Session and `uid`, production should mount the monitor on the same Origin as New API, for example `https://api.example.com/monitor/`. A separate hostname or port cannot share both browser states by default and must not work around this boundary by copying cookies or impersonating users with a management token.

The emergency monitor administrator has no New API identity and cannot enter the console. Monitor role mappings only control entry visibility and cannot promote a regular New API user to global administrator scope.

## Pages and upstream endpoints

| Page | Path | New API sources |
| --- | --- | --- |
| Overview | `/monitor/console` | `/api/status`, `/api/user/self`, `/api/user/models`, `/api/token/`, log statistics |
| Analytics | `/monitor/console/analytics` | `/api/data[/self]`, `/api/data/flow[/self]`, log statistics |
| API Keys | `/monitor/console/keys` | `/api/token/*`, `/api/user/models`, `/api/user/self/groups` |
| Usage Logs | `/monitor/console/logs` | `/api/log/` or `/api/log/self`, plus the matching statistics endpoint |

New API administrators use global endpoints; regular users use only self endpoints. A regular-user query is capped at 30 days.

## Data and keys

- Customer business data is read only for the current request and is not stored in monitor SQLite.
- Token lists expose only the masked key returned by New API.
- Plaintext keys require an explicit one-time POST reveal with a separate rate limit and `Cache-Control: no-store`.
- Plaintext keys never enter settings, audit records, application logs, URLs, localStorage, or sessionStorage, and React state is cleared when the reveal dialog closes.
- New API revalidates ownership and business rules for every token mutation. The monitor records only redacted operation audits.

## Compatibility and failure boundary

- The BFF cannot forward arbitrary URLs, paths, headers, or methods, preventing an upgrade-compatibility layer from becoming an SSRF primitive or open proxy.
- Upstream timeouts, non-JSON data, oversized responses, and abnormal HTTP statuses are normalized to bounded errors without echoing cookies, tokens, or upstream response bodies.
- Upstream requests carrying a Session, administrator credentials, or a Key never follow HTTP redirects, preventing credentials from being forwarded to another host.
- If a New API API contract changes, only `dashboard_newapi_console.py` and its contract tests need adjustment. Monitor failures do not block New API traffic.
- The console can be disabled globally or page by page under System Settings -> Customer Console.
