# New API Pages Architecture

[简体中文](CUSTOMER_CONSOLE.md) | [English](CUSTOMER_CONSOLE_EN.md)

Regular users receive five independent top-level modules: Monitor Overview, account Overview, Analytics, API Keys, and Usage Logs. The four account pages are external New API pages hosted by the monitor. None of these modules modifies New API source code, duplicates user/token/quota/log tables, or replaces New API authentication and billing.

## Request flow

```text
Browser -> fixed monitor BFF -> fixed New API endpoints
           session + verified user_id
```

1. The browser sends the New API `session` cookie and `New-Api-User` header.
2. The monitor verifies the session, account status, and matching user ID through New API `/api/user/self`.
3. The BFF forwards only the current session and verified user ID to code-defined New API endpoints.
4. New API remains responsible for data scope, token ownership, quota validation, and every mutation.

The monitor does not batch-copy the New API user table. It synchronizes the current identity from the login session on demand; account disablement, deletion, and role changes take effect after the short identity cache expires, so accounts are never maintained twice.

To reuse the browser's New API Session and `uid`, production should mount the monitor on the same Origin as New API, for example `https://api.example.com/monitor/`. A separate hostname or port cannot share both browser states by default and must not work around this boundary by copying cookies or impersonating users with a management token.

New API Admin and Root accounts map to monitor administrators and can use every monitor module. Regular New API users receive the viewer-filtered Monitor Overview plus the four personal business pages below. Their UI uses a neutral API Service Center identity without New API branding or technical operations modules. The emergency monitor administrator has no New API identity and cannot enter the four account pages. Explicit monitor-role overrides change monitor entry access only and cannot grant a regular user global New API data scope.

After explicit monitor sign-out, the server sets a dedicated monitor SSO-suppression cookie. It contains no identity or credential and only prevents automatic reuse of the New API session. The user must explicitly select “Sign in” to clear it. The New API `session` cookie is never deleted or modified.

The monitor login page never collects or stores regular-user upstream passwords. If the browser has no reusable account session, the neutral API Service Center remains visible and asks the user to sign in to the account service first; it does not automatically redirect to a branded upstream page.

## Pages and upstream endpoints

| Page | Path | Data sources |
| --- | --- | --- |
| Monitor Overview | `/monitor/` | Channel probes, real-log aggregates, and resource metrics from monitor SQLite, filtered by viewer channel scope |
| Overview | `/monitor/console` | `/api/status`, `/api/user/self`, `/api/user/models`, `/api/token/`, log statistics |
| Analytics | `/monitor/console/analytics` | `/api/data[/self]`, `/api/data/flow[/self]`, log statistics |
| API Keys | `/monitor/console/keys` | `/api/token/*`, `/api/user/models`, `/api/user/self/groups`, `/api/data/flow/self` |
| Usage Logs | `/monitor/console/logs` | `/api/log/` or `/api/log/self`, plus the matching statistics endpoint |

Analytics defaults to global endpoints for New API administrators, who can explicitly switch to the current account's self endpoints. Regular users are server-enforced to self endpoints, and username filtering is accepted only in administrator Global scope.

Regular users do not see the technical Monitor workspace. They can read only channel cards and summary status enabled for the viewer audience. The official-status page, monitor logs, machine resources, incidents, key lookup, channel settings, and system settings still reject viewers on the server, and editing a URL cannot bypass this boundary.

Viewer channel responses use a fixed allowlist containing only display status, latency, availability, model scope, and aggregate metrics. Original channel names, probe configuration, recent request logs, and raw upstream error bodies are never sent. Administrators and operators retain the full troubleshooting view.

Analytics uses real New API data to render a model-stacked timeline and can switch between Requests, Tokens, and Spend. The spend total uses the most complete value among live consume-log statistics, model attribution, and flow attribution. Live logs normally close the current-period gap, while model and flow details use New API's `quota_data` projection. Any newest-period difference is shown explicitly as pending attribution. Usage-flow rows are merged by the account/key, group, and model dimensions visible in the UI; hidden node and channel dimensions cannot create duplicate-looking rows, and everything after row 12 is summarized as Other so visible details close to the total. A slower stale request cannot overwrite the user's later selection.

## Data and keys

- Customer business data is read only for the current request and is not stored in monitor SQLite.
- Token lists expose only the masked key returned by New API.
- Plaintext keys require an explicit one-time POST reveal with a separate rate limit and `Cache-Control: no-store`.
- Plaintext keys never enter settings, audit records, application logs, URLs, localStorage, or sessionStorage, and React state is cleared when the reveal dialog closes.
- New API revalidates ownership and business rules for every token mutation. The monitor records only redacted operation audits.
- The monitor additionally stores only custom key-group names, colors, and `user_id + token_id + group_id` membership. It does not duplicate quotas, logs, or plaintext keys, and one key may belong to multiple reporting groups.
- Custom key groups are independent from the native New API Token `group` field. The native field still controls New API routing/billing; the custom field organizes monitor statistics only.
- Per-key and grouped usage always reads `/api/data/flow/self` and joins current keys by immutable `token_id`. Even a New API administrator does not receive global Flow data on this personal-key page.
- Group totals use current multi-membership. New API Flow has no historical custom-group snapshot, so member changes recalculate the selected 1/7/30-day period from current relationships. A key contributes to each group it belongs to, account totals stay deduplicated, and overlapping group totals must not be added together.
- Group create, update, delete, bulk assignment, and member editing enter the monitor configuration audit. Each write re-fetches the caller's complete token list and rejects unknown or foreign token IDs. Deleting a group never deletes keys or their other memberships.

## Compatibility and failure boundary

- The BFF cannot forward arbitrary URLs, paths, headers, or methods, preventing an upgrade-compatibility layer from becoming an SSRF primitive or open proxy.
- Upstream timeouts, non-JSON data, oversized responses, and abnormal HTTP statuses are normalized to bounded errors without echoing cookies, tokens, or upstream response bodies.
- Upstream requests carrying a Session, administrator credentials, or a Key never follow HTTP redirects, preventing credentials from being forwarded to another host.
- If a New API API contract changes, only `dashboard_newapi_console.py` and its contract tests need adjustment. Monitor failures do not block New API traffic.
- New API pages can be disabled globally or page by page under System Settings -> New API Pages.
- Legacy single-group membership tables migrate automatically to the many-to-many composite key at startup. The legacy `group_id` request field remains compatible during rolling upgrades and browser-cache transitions.
- The database migration runs inside an explicit transaction. Any failed step preserves the old table and memberships, and restarting after data repair retries safely.
