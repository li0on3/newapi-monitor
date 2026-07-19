# Changelog

## Unreleased

### Added

- 渠道同步、渠道探测、日志和资源采集的新鲜度自检。
- 采集器异常与恢复事件、详细运行状态页面和 503 健康降级。
- 数据库敏感配置加密、Host 白名单和状态变更请求校验。
- 非 Root 容器、资源限制、只读文件系统和 Docker Socket Proxy 加固。
- `manage.py` 初始化、部署诊断和 SQLite 在线备份。
- GitHub Actions、CodeQL、Dependabot、贡献和安全文档。

### Fixed

- New API 使用日志分页参数改为 `p`，避免回填和高流量场景漏日志。
- 渠道卡片时间与跳转箭头重叠。
