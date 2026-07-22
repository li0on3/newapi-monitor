# Security Policy

[简体中文](SECURITY.md) | [English](SECURITY_EN.md)

## Supported versions

安全修复只保证合入最新主分支。生产部署应固定到已验证的 Git commit 或 release，不建议长期运行不受控的 `latest` 代码。

## 报告漏洞

请通过 GitHub Security Advisory 私密报告，不要在公开 Issue 中提交令牌、Cookie、服务器地址、日志原文或可直接利用的攻击步骤。

报告建议包含：

- 受影响版本或 commit；
- 影响范围和前置条件；
- 最小复现；
- 建议修复或缓解措施。

## 部署安全基线

- 监控端口默认只绑定 `127.0.0.1`，公网访问必须经过 HTTPS 反向代理。
- 必须配置精确的 `DASHBOARD_ALLOWED_HOSTS`，不得在生产使用 `*`。
- `.env` 权限应为 `0600`，不得提交到 Git。
- 必须保留并备份 `MONITOR_SECRET_KEY`；它用于加密数据库中的 New API、Relay 和 SMTP 密钥。
- Docker Socket 只能通过只读 Socket Proxy 暴露，不得将 Socket 直接挂载给生产监控容器。
- 紧急管理员只用于 New API SSO 不可用时的恢复操作，密码应单独保管并定期轮换。
- 上传公开仓库前运行 `python manage.py doctor` 并检查 `git status --ignored`。
- 一键安装生成的初始化令牌只有 15 分钟有效且只显示一次；初始化前不要将本地监控端口直接暴露到公网。
- 初始化向导中的 New API 管理员密码只用于换取管理令牌和独立探测 Key，不会持久化；初始化完成后应确认 `/api/setup/status` 返回 `required: false`。
- `monitorctl backup` 会同时备份数据库和含加密密钥的环境文件，备份等同于生产凭据，必须离线加密保管。

## 信任边界

监控管理员能够修改 New API 地址、探测规则和 SMTP 设置，因此管理员账号等同于基础设施高权限账号。角色映射、登录审计和配置审计不能替代账号安全。

Key 用量查询默认仅管理员可见，并采用服务端 POST 转发、固定上游路径、按用户和来源地址限流。原始 Key 不写入监控数据库、URL、配置审计或接口响应；生产环境不建议将最低角色下调为普通用户。

同机监控无法检测整机断电、宿主机网络中断或磁盘完全损坏。需要此类告警时，应增加独立于目标主机的外部心跳检查。
