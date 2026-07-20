# Security Audit

审计日期：2026-07-20
范围：FastAPI 后端、React 前端、SQLite 配置存储、Docker/Compose、部署与维护脚本。

## Executive summary

本轮已修复监控平台最主要的应用、接口和部署风险：采集线程假活、状态变更接口 CSRF、Host Header 信任、动态配置输入过宽、数据库秘密明文、Root 容器、Docker Socket 暴露面和公开健康接口信息泄露。

Python 与 Bun 依赖审计均未发现已知漏洞。最终容器扫描仍报告 Alpine SQLite 的两个 High CVE，当前上游没有可用修复版本；风险已接受并持续通过 Dependabot、CI 和镜像扫描跟踪。

## Resolved findings

### SEC-001 — High — Cookie 鉴权状态变更缺少请求来源约束

- Location: `dashboard_app.py:353-362`, `web/src/api.ts`
- Impact: 同站点攻击面或错误代理配置下，浏览器可能被诱导发起配置变更请求。
- Fix: 所有 POST/PUT/PATCH/DELETE API 必须携带前端主动设置的 `X-Monitor-Request: 1`；跨站表单无法添加该 Header，接口同时继续执行角色授权。

### SEC-002 — High — Host Header 未做白名单校验

- Location: `dashboard_app.py:17`, `dashboard_app.py:341-347`
- Impact: 恶意 Host 可能影响绝对地址、安全边界判断和代理行为。
- Fix: 启用 `TrustedHostMiddleware`，生产域名通过 `DASHBOARD_ALLOWED_HOSTS` 精确配置；`manage.py doctor` 拒绝生产通配符。

### SEC-003 — High — 数据库中的管理 Token 和 SMTP 密码为明文

- Location: `dashboard_settings.py:11`, `dashboard_settings.py:336-351`
- Impact: 单独泄露 SQLite 备份即可获得 New API、Relay 和 SMTP 凭据。
- Fix: 使用 `MONITOR_SECRET_KEY` 派生 Fernet 密钥并自动迁移已有秘密字段；公开 API 与审计日志继续只返回掩码。

### SEC-004 — High — 监控线程存活不等于采集数据仍然有效

- Location: `newapi_monitor.py:405-451`, `newapi_monitor.py:915-970`, `newapi_monitor.py:1423-1433`, `newapi_monitor.py:1463-1472`
- Impact: 上游调用、Docker 采集或子线程永久失败时，旧健康检查仍可能返回正常，造成静默失效。
- Fix: 渠道同步、渠道探测、日志和资源采集记录最后成功时间、连续失败和错误；超过动态阈值后生成异常事件、发送邮件并使健康检查返回 503，恢复时生成恢复事件。

### SEC-005 — Medium — 动态配置接口接受任意字典

- Location: `dashboard_app.py:42-122`
- Impact: 类型混淆、未知字段和异常 URL 可能进入运行时配置，增加 SSRF、错误配置和拒绝服务风险。
- Fix: 使用 `extra="forbid"` 的 Pydantic Schema；URL 禁止凭据、query 和 fragment；探测路径只允许相对 API Path；数值设置范围上限。

### SEC-006 — Medium — 公开健康接口暴露内部错误

- Location: `dashboard_app.py:424-471`
- Impact: 未认证访问者可以获得数据库和采集器错误细节。
- Fix: `/api/health` 只返回状态和时间；详细状态迁移到需要登录的 `/api/system/status`。

### SEC-007 — High — 生产容器以 Root 运行

- Location: `Dockerfile:9-26`, `compose.yaml:36-105`
- Impact: 应用漏洞与容器逃逸链的影响被放大。
- Fix: Runtime 使用 Alpine 最小镜像并以 UID/GID 10001 运行；根文件系统只读、移除 capabilities、启用 `no-new-privileges`、PID/CPU/内存限制。一次性 `state-init` 只负责持久卷权限。

### SEC-008 — Medium — 部署与开源交付缺少防误提交和持续检查

- Location: `.gitignore`, `.dockerignore`, `.github/workflows/ci.yml`, `.github/workflows/codeql.yml`
- Impact: 密钥、数据库、备份或依赖漏洞可能进入公开仓库和发布镜像。
- Fix: 扩大秘密/数据库/备份忽略规则；增加依赖锁、测试、前端构建、Compose 校验、Docker 构建、CodeQL 和 Dependabot。

## Accepted and residual risks

### RISK-001 — 同机监控无法检测整机失联

这是部署拓扑的固有限制。若业务要求主机级失联告警，应增加独立外部心跳，而不是在目标主机继续增加组件。

### RISK-002 — SQLite Alpine package two High CVEs without upstream fix

Docker Scout 报告 `sqlite 3.51.2-r0` 的 CVE-2026-11824 与 CVE-2026-11822，当前无修复版本。监控不接受用户上传的 SQLite 文件，数据库位于私有持久卷，攻击前提受限。继续通过基础镜像升级和 Dependabot 跟踪；出现修复版本后应立即重建镜像。

### RISK-003 — 管理员可配置出站 New API 地址

这是平台功能所必需的能力。接口限制为 HTTP(S) 且禁止 URL 凭据，但允许私网地址，以支持同机和内网 New API。监控管理员因此属于高权限角色，必须使用最小人员范围和独立紧急密码。

### RISK-004 — 加密密钥遗失会导致秘密配置不可恢复

这是静态加密的二阶代价。备份数据库时必须同步安全保存 `MONITOR_SECRET_KEY`；不得在没有迁移流程的情况下直接更换该值。

## Verification evidence

- Python unit tests: 49 passed.
- TypeScript and Vite production build: passed.
- `pip-audit`: no known vulnerabilities after upgrading `cryptography` to 48.0.1.
- `bun audit`: no known vulnerabilities.
- Container smoke: non-root UID 10001, read-only filesystem, health endpoint successful.
- Interface smoke: invalid Host returns 400; missing request verification Header returns 403; valid login returns 200.
