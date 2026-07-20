# New API 外置监控平台

独立部署、无需修改 New API 源码的监控与告警平台。它通过 New API 管理接口、真实 Relay 请求、真实使用日志和 Docker 只读接口采集数据，适合单机或小规模 New API 环境。

## 核心能力

- 启用渠道自动同步，禁用渠道自动隐藏；支持 OpenAI Responses、Chat Completions 和 Anthropic Messages 真实探测。
- 使用 New API 原始使用日志分析总耗时、首字耗时、用户、令牌、模型和渠道。
- 最近 5 次中 3 次或最近 10 次中 5 次超过慢请求阈值时告警；单次超过严重阈值立即告警。
- 宿主机 CPU、内存、磁盘以及 Docker 容器资源、状态、重启和 OOM 监控。
- 渠道、日志、资源和渠道同步采集器的新鲜度自检，防止“监控页面还活着但数据已经停止更新”。
- SMTP 异常、恢复和周期报告；事件在数据库中保留并可审计。
- New API Session 单点登录、角色映射、紧急管理员、登录限速和配置审计。
- 页面动态配置，不写回 New API，不影响 New API 升级。

## 快速部署

### 1. 初始化安全配置

Linux：

```bash
git clone <your-repository-url> newapi-monitor
cd newapi-monitor
python3 manage.py init
```

Windows：

```powershell
git clone <your-repository-url> newapi-monitor
Set-Location newapi-monitor
python manage.py init
```

初始化命令会生成：

- 随机紧急管理员密码；
- 用于数据库敏感配置加密的 `MONITOR_SECRET_KEY`；
- 权限受限的 `.env` 配置文件。

紧急管理员密码只显示一次，请保存到密码管理器。

### 2. 编辑 `.env`

必须填写：

- `NEW_API_BASE_URL`
- `NEW_API_ACCESS_TOKEN`
- `NEW_API_USER_ID`
- `SMTP_HOST`、`SMTP_TO` 及对应认证参数
- `DASHBOARD_ALLOWED_HOSTS`，填写监控页面实际域名

真实探测建议在系统启动后通过“渠道配置”页面启用，不需要手工编写 JSON。

### 3. 部署前检查

```bash
python3 manage.py doctor
```

只有配置、Host 白名单、密码、加密密钥和 Compose 均检查通过后再部署。

### 4. 启动

```bash
./install.sh
```

或：

```bash
docker compose build monitor
docker compose up -d
docker compose ps
```

默认仅监听：

```text
127.0.0.1:18081
```

请使用 Nginx、OpenResty、Caddy 或其他 HTTPS 反向代理对外提供 `/monitor/`。

## 健康检查

公开健康检查只返回最少信息：

```bash
curl -fsS http://127.0.0.1:18081/api/health
```

正常：

```json
{"status":"ok","timestamp":1784476800}
```

以下任一情况返回 HTTP 503：

- SQLite 无法读取；
- 监控主线程停止；
- 渠道同步、渠道探测、日志或资源采集超过动态失效阈值。

管理员可以在“系统配置 → 运行状态”查看每个采集器最后成功时间、连续失败次数、错误摘要和失效阈值。

## 默认策略

| 项目 | 默认值 |
| --- | ---: |
| 渠道同步 | 5 秒 |
| 日志同步 | 30 秒 |
| 资源采样 | 15 秒 |
| 渠道真实探测 | 5 分钟 |
| 慢请求 | 任一耗时指标超过 60 秒 |
| 窗口告警 | 5 次中 3 次，或 10 次中 5 次 |
| 单次严重告警 | 超过 180 秒 |
| 资源告警 | 超阈值持续 180 秒 |
| 数据保留 | 90 天 |

采集器失效阈值根据采集周期自动计算，通常为采集周期的 3～4 倍，并设置合理的最小宽限时间。

## 数据与安全

- 不保存模型提示词和响应正文，只保存监控所需指标与错误摘要。
- New API 管理 Token、Relay Token 和 SMTP 密码在 SQLite 中使用 `MONITOR_SECRET_KEY` 加密。
- 生产容器以 UID `10001` 非 Root 用户运行，根文件系统只读，移除全部 Linux capabilities。
- Docker Socket 不直接暴露给监控程序，只通过只读 Socket Proxy 提供必要接口。
- 状态变更接口使用严格 Pydantic Schema、角色校验和同源请求校验头。
- 公共健康接口不返回内部错误、路径和采集详情。
- 监控数据不匿名公开：普通 New API 用户通过现有 Session 登录后默认仅能查看总览；运维员可查看日志、资源、事件与渠道配置；监控管理员可管理系统配置和角色映射。
- 配置变更和角色变更写入审计表，秘密字段始终脱敏。

详细安全边界见 [SECURITY.md](SECURITY.md)。

## 备份

```bash
python3 manage.py backup
```

备份使用 SQLite Online Backup API，并在输出前执行完整性检查。恢复备份时必须同时持有原来的 `MONITOR_SECRET_KEY`，否则数据库中的敏感配置无法解密。

建议同时安全备份：

- `backups/*.db`
- `.env` 中的 `MONITOR_SECRET_KEY`
- 反向代理配置

不要将以上文件提交到 Git。

## 升级与回滚

升级前：

```bash
python3 manage.py backup
git pull --ff-only
python3 manage.py doctor
docker compose build monitor
docker compose up -d
```

建议使用 Git tag 或 release 固定生产版本。回滚代码后重新构建镜像，数据库表采用向后兼容的增量创建方式；执行重大版本回滚前仍应恢复对应备份。

## 开发验证

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v

cd web
bun install --frozen-lockfile
bun run build

cd ..
docker compose --env-file .env.example config --quiet
docker build -t newapi-monitor:test .
```

本地完整联调环境仍可使用：

```bash
docker compose --env-file .env.local -f docker-compose.local.yml up -d --build
```

## 架构原则

1. **测量真实目标**：渠道健康优先使用真实请求，不把仅连通测试等同于真实可用。
2. **监控监控本身**：每条采集链路必须留下最后成功时间，并能产生异常和恢复事件。
3. **最小权限**：只读 API、独立探测 Token、非 Root 容器、回环端口和最小 Docker Socket 权限。
4. **故障隔离**：监控故障不得修改或阻塞 New API 主业务。
5. **拒绝过度设计**：当前使用 SQLite 和单进程调度，适合单机及小规模环境；只有容量和可靠性目标发生变化时才引入外部时序数据库或消息队列。

同机部署不能检测整机失联。如果需要主机宕机告警，应增加一个独立于目标服务器的外部 HTTP 心跳检查，而不是继续在同一台机器堆叠组件。
