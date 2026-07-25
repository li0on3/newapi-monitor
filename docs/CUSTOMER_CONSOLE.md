# New API 功能页架构

[简体中文](CUSTOMER_CONSOLE.md) | [English](CUSTOMER_CONSOLE_EN.md)

概览、数据看板、API 密钥和使用日志是监控平台内的外置 New API 功能页，并作为独立顶级模块展示。它们不修改 New API 源码，不复制用户、Token、额度或日志表，也不接管 New API 的鉴权和计费。

## 请求链路

```text
浏览器 -> 监控平台固定 BFF -> New API 固定 API
          session + 已校验 user_id
```

1. 浏览器携带 New API 的 `session` Cookie 和 `New-Api-User`。
2. 监控平台通过 New API `/api/user/self` 校验 Session、账号状态和用户 ID 一致性。
3. BFF 只向代码内固定的 New API API 转发当前 Session 与已校验用户 ID。
4. New API 继续负责数据范围、Token 所有权、额度校验和所有写入。

监控平台不批量复制 New API 用户表。用户每次登录时按 Session 实时同步当前身份；账号禁用、删除或角色变化会在短缓存失效后自动生效，无需在两个平台重复维护账号。

为复用浏览器中的 New API Session 与 `uid`，生产环境应把监控平台挂载在 New API 的同一 Origin 下，例如 `https://api.example.com/monitor/`。独立域名或不同端口默认无法共享这两项浏览器状态，不应通过复制 Cookie 或管理 Token 冒充用户来绕过。

New API Admin 与 Root 默认映射为监控管理员，可使用全部监控模块；普通 New API 用户默认只能使用下列四个个人业务页面。监控平台的紧急管理员没有 New API 身份，因此不能进入这些业务页面。显式角色覆盖只能改变监控入口，不能把普通 New API 用户升级为 New API 全局数据权限。

用户点击监控平台退出后，服务端设置独立的监控 SSO 抑制 Cookie。该 Cookie 不包含身份或凭据，只阻止监控自动复用 New API Session；用户主动点击“使用 New API 账号登录”后才解除。整个过程不会删除或修改 New API `session` Cookie。

## 页面和上游接口

| 页面 | 路径 | New API 数据源 |
| --- | --- | --- |
| 概览 | `/monitor/console` | `/api/status`、`/api/user/self`、`/api/user/models`、`/api/token/`、日志统计 |
| 数据看板 | `/monitor/console/analytics` | `/api/data[/self]`、`/api/data/flow[/self]`、日志统计 |
| API 密钥 | `/monitor/console/keys` | `/api/token/*`、`/api/user/models`、`/api/user/self/groups`、`/api/data/flow/self` |
| 使用日志 | `/monitor/console/logs` | `/api/log/` 或 `/api/log/self`、对应统计接口 |

源角色为 New API 管理员时调用全局接口；普通用户只调用 `self` 接口。普通用户单次查询最多 30 天。

普通用户不会看到“监控”工作区，并且监控总览、渠道、官方状态、监控日志、机器资源、事件、Key 查询和系统配置接口都在服务端拒绝 Viewer；直接输入对应 URL 也会返回个人 New API 页面，不能仅靠前端隐藏来保护数据。

## 数据与密钥

- 客户业务数据只在请求期间读取，不写入监控 SQLite。
- Token 列表只返回 New API 已脱敏的 Key。
- 明文 Key 只能由用户主动执行一次性查看，使用 POST、单独限速和 `Cache-Control: no-store`。
- 明文 Key 不进入配置、审计、应用日志、URL、localStorage 或 sessionStorage；关闭弹窗后从 React 状态清除。
- 所有 Token 写操作由 New API 再次校验所有权和业务规则，监控平台只记录脱敏操作审计。
- 监控平台只额外保存自定义密钥分组名称、颜色和 `user_id + token_id` 成员关系，不复制额度、日志或明文 Key。
- 自定义密钥分组与 New API Token 的原生 `group` 字段相互独立；后者仍用于 New API 路由/计费，前者只用于监控页面组织和统计。
- 个人密钥和分组用量固定读取 `/api/data/flow/self`，按不可变 `token_id` 关联当前账号的现有密钥。即使账号是 New API 管理员，该页面也不读取全局 Flow。
- 分组汇总采用“当前归属”口径。New API Flow 没有历史自定义分组快照，因此移动密钥后，所选 1/7/30 天用量会按新的当前分组重新汇总；页面会明确提示这一口径。
- 创建、修改、删除和移动分组均写入监控配置审计；移动前再次读取当前用户完整 Token 清单，拒绝不存在或不属于当前用户的 Token ID。

## 兼容与故障边界

- BFF 没有任意 URL、路径、请求头或方法透传能力，避免升级兼容功能退化为 SSRF 或开放代理。
- 上游超时、非 JSON、超大响应和异常 HTTP 状态被转换为有限错误，不回显 Cookie、Token 或上游响应正文。
- 携带 Session、管理员凭据或 Key 的上游请求不会跟随 HTTP 重定向，避免凭据被转发到其他主机。
- New API API 合约发生变化时，只需调整 `dashboard_newapi_console.py` 及对应契约测试；New API 主业务不受监控平台故障影响。
- New API 功能页可以在“系统配置 → New API 功能页”整体关闭，或按页面逐项关闭。
