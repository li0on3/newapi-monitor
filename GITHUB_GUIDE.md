# GitHub 维护指南

[简体中文](GITHUB_GUIDE.md) | [English](GITHUB_GUIDE_EN.md)

`main` 是受保护的生产基线，禁止直接推送、强制覆盖或删除。正常变更必须通过 Pull Request。

当前保护策略：

- 管理员也必须遵守保护规则，不能直接绕过；
- 必须通过 Pull Request，并等待四项必需检查；
- 分支必须基于最新 `main`，所有讨论必须解决；
- 仅允许线性历史和 Squash merge；
- 禁止强制推送和删除 `main`；
- `v*` 发布标签禁止删除或覆盖。

## 日常修改

```bash
git switch main
git pull --ff-only
git switch -c feat/short-description

# 修改、测试后
git add <files>
git commit -m "feat: describe the change"
git push -u origin feat/short-description
gh pr create --fill
```

等待以下检查全部通过：

- `test`
- `scan`
- `analyze (python)`
- `analyze (javascript-typescript)`

然后使用 **Squash and merge** 合并。仓库会自动删除远程功能分支。

## 发布版本

1. 在功能分支同步更新 `VERSION`、`web/package.json`、中英文 CHANGELOG 和相关文档。
2. 通过 Pull Request 合并到 `main`。
3. 确认 `main` 检查全部通过。
4. 在最新 `main` 创建版本标签：

```bash
git switch main
git pull --ff-only
git tag -a v1.0.1 -m "New API Monitor v1.0.1"
git push origin v1.0.1
```

Release 工作流会自动构建 AMD64/ARM64 镜像、发布 GHCR 镜像并创建 GitHub Release。

## 安全规则

- 不得提交 `.env`、数据库、备份、Cookie、Token、Webhook、服务器地址或真实日志正文。
- 不得使用 `git push --force` 更新 `main` 或版本标签。
- 不得删除已经发布的 `v*` 标签；需要修复时发布新的补丁版本。
- Dependabot 主版本升级必须单独评估，不要直接自动合并。
- 如果工作流故障导致无法合并，应先在功能分支修复工作流；只有仓库恢复操作才临时调整保护规则，并在恢复后立即重新启用。
