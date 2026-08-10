# CPA Weekly Quota Monitoring

CPA Weekly Quota is an administrator-only module isolated from New API monitoring. It reads weekly limits, reset times, and nearby request pressure for enabled Codex and Antigravity accounts managed by CLIProxyAPI / API Management.

## Data flow and boundary

1. The backend uses the CPA management key to read the active credential catalog.
2. It uses CPA's controlled `api-call` endpoint with fixed provider quota URLs: ChatGPT Usage for Codex and Cloud Code quota summaries for Antigravity.
3. It can optionally read 1-hour, 6-hour, and 24-hour aggregates from Token Usage Tracker as request-pressure context.
4. SQLite stores only an irreversible account identifier, a masked account label, quota percentages, reset times, and aggregated activity.

The management key, `auth_index`, `project_id`, and upstream tokens are never written to quota history or returned to the browser. The management key is encrypted with `MONITOR_SECRET_KEY`, like other platform secrets.

## Alert policy

- Collection runs every 30 minutes by default, with a minimum configurable interval of 5 minutes. Manual collection can be queued at most once per minute.
- An alert opens when real remaining quota is `<= 20%` and resolves after recovery to `>= 25%`, using hysteresis to avoid flapping.
- Persistent low quota repeats at most once every 6 hours by default.
- Forecasts never trigger alerts by themselves. Provider formulas are opaque, so real quota decline is authoritative and nearby request pressure is only a bounded adjustment.
- One failed account does not block the others; the collector fails only when all supported active accounts fail.

Intervals, timeouts, thresholds, repeat timing, and request-pressure collection are dynamically configurable under **System Settings → CPA Weekly Quota**.

## Forecast definition

- Forecasting starts only after at least two real samples spanning two hours.
- Only samples from the same reset cycle are compared; a weekly reset starts a new baseline.
- The median decline across adjacent samples reduces single-sample noise.
- One-hour pressure relative to the six-hour average applies only a bounded adjustment and is never treated as the provider's charging formula.
- The page exposes time to threshold, time to exhaustion, reset-first evaluation, and low/medium/high confidence.

## Authorization and isolation

- `/monitor/cpa-quota`, `/monitor/system/cpa-quota`, and their APIs are administrator-only.
- CPA quota incidents never affect New API Overall Status and are excluded from the generic operator incident page.
- The module is read-only. It does not enable or disable CPA credentials, change quota, modify New API channels, or enter the request path.

## Same-host Docker deployment

The default endpoint is:

```text
http://host.docker.internal:8317
```

The repository `compose.yaml` maps `host.docker.internal` to the Linux host gateway. For a remote CPA instance, configure its private HTTP(S) URL instead. Keep the CPA management interface restricted to trusted networks.

## Acceptance

After enabling the module, verify that:

1. The account count matches active CPA Codex/Antigravity credentials and labels are masked.
2. Every displayed remaining percentage matches the provider quota response, while daily or five-hour limits are excluded.
3. Regular users and operators are denied access to both the page and APIs.
4. New API overview, channel status, and incident totals remain unchanged by CPA quota state.
5. The database and API responses contain no management key, `auth_index`, `project_id`, or upstream token.
