# Contributing

[简体中文](CONTRIBUTING.md) | [English](CONTRIBUTING_EN.md)

## 开发环境

- Python 3.13+
- Bun 1.3+
- Docker Engine + Docker Compose

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
cd web
bun install --frozen-lockfile
bun run build
```

## 变更要求

- 新功能和缺陷修复先增加可失败的回归测试。
- 不得提交 `.env`、数据库、备份、Cookie、令牌或真实域名配置。
- 监控项目保持外置，不得要求修改 New API 核心代码。
- 新增采集器必须同时实现：最后成功时间、失败计数、失效阈值、异常事件和恢复事件。
- 新增接口必须默认鉴权；状态变更接口必须通过请求校验头并使用明确的 Pydantic Schema。
- 修改前端后必须执行 TypeScript 与生产构建。
- 修改部署文件后必须执行 `docker compose config --quiet` 和镜像构建。
- 行为、配置、安全边界或部署步骤变化时，必须同步更新中英文文档。
