# 客户控制台架构

[简体中文](CUSTOMER_CONSOLE.md) | [English](CUSTOMER_CONSOLE_EN.md)

客户控制台是监控平台内的外置 New API 用户界面。它不修改 New API 源码，不复制用户、Token、额度或日志表，也不接管 New API 的鉴权和计费。

## 请求链路

```text
浏览器 -> 监控平台固定 BFF -> New API 固定 API
          session + 已校验 user_id
```

1. 浏览器携带 New API 的 `session` Cookie 和 `New-Api-User`。
2. 监控平台通过 New API `/api/user/self` 校验 Session、账号状态和用户 ID 一致性。
3. BFF 只向代码内固定的 New API API 转发当前 Session 与已校验用户 ID。
4. New API 继续负责数据范围、Token 所有权、额度校验和所有写入。

为复用浏览器中的 New API Session 与 `uid`，生产环境应把监控平台挂载在 New API 的同一 Origin 下，例如 `https://api.example.com/monitor/`。独立域名或不同端口默认无法共享这两项浏览器状态，不应通过复制 Cookie 或管理 Token 冒充用户来绕过。

监控平台的紧急管理员没有 New API 身份，因此不能进入客户控制台。监控角色映射只控制入口是否显示，不能把普通 New API 用户升级为全局管理员。

## 页面和上游接口

| 页面 | 路径 | New API 数据源 |
| --- | --- | --- |
| 概览 | `/monitor/console` | `/api/status`、`/api/user/self`、`/api/user/models`、`/api/token/`、日志统计 |
| 数据看板 | `/monitor/console/analytics` | `/api/data[/self]`、`/api/data/flow[/self]`、日志统计 |
| API 密钥 | `/monitor/console/keys` | `/api/token/*`、`/api/user/models`、`/api/user/self/groups` |
| 使用日志 | `/monitor/console/logs` | `/api/log/` 或 `/api/log/self`、对应统计接口 |

源角色为 New API 管理员时调用全局接口；普通用户只调用 `self` 接口。普通用户单次查询最多 30 天。

## 数据与密钥

- 客户业务数据只在请求期间读取，不写入监控 SQLite。
- Token 列表只返回 New API 已脱敏的 Key。
- 明文 Key 只能由用户主动执行一次性查看，使用 POST、单独限速和 `Cache-Control: no-store`。
- 明文 Key 不进入配置、审计、应用日志、URL、localStorage 或 sessionStorage；关闭弹窗后从 React 状态清除。
- 所有 Token 写操作由 New API 再次校验所有权和业务规则，监控平台只记录脱敏操作审计。

## 兼容与故障边界

- BFF 没有任意 URL、路径、请求头或方法透传能力，避免升级兼容功能退化为 SSRF 或开放代理。
- 上游超时、非 JSON、超大响应和异常 HTTP 状态被转换为有限错误，不回显 Cookie、Token 或上游响应正文。
- 携带 Session、管理员凭据或 Key 的上游请求不会跟随 HTTP 重定向，避免凭据被转发到其他主机。
- New API API 合约发生变化时，只需调整 `dashboard_newapi_console.py` 及对应契约测试；New API 主业务不受监控平台故障影响。
- 客户控制台可以在“系统配置 → 客户控制台”整体关闭，或按页面逐项关闭。
