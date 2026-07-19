# Security Policy

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

## 信任边界

监控管理员能够修改 New API 地址、探测规则和 SMTP 设置，因此管理员账号等同于基础设施高权限账号。角色映射、登录审计和配置审计不能替代账号安全。

同机监控无法检测整机断电、宿主机网络中断或磁盘完全损坏。需要此类告警时，应增加独立于目标主机的外部心跳检查。
