# GitHub Maintenance Guide

[简体中文](GITHUB_GUIDE.md) | [English](GITHUB_GUIDE_EN.md)

`main` is the protected production baseline. Direct pushes, force pushes, and deletion are blocked. Normal changes must use a pull request.

Current protection policy:

- Administrators must follow the rule and cannot bypass it with a direct push.
- Every change requires a pull request and all four required checks.
- Branches must be up to date with `main`, and conversations must be resolved.
- Only linear history and squash merging are enabled.
- Force pushes and deletion of `main` are blocked.
- Published `v*` tags cannot be deleted or overwritten.

## Daily Changes

```bash
git switch main
git pull --ff-only
git switch -c feat/short-description

# After editing and testing
git add <files>
git commit -m "feat: describe the change"
git push -u origin feat/short-description
gh pr create --fill
```

Wait for all required checks:

- `test`
- `scan`
- `analyze (python)`
- `analyze (javascript-typescript)`

Use **Squash and merge** after the checks pass. The remote feature branch is deleted automatically.

## Releases

1. Update `VERSION`, `web/package.json`, both changelogs, and relevant Chinese/English documentation on a feature branch.
2. Merge through a pull request.
3. Confirm that the latest `main` checks are green.
4. Tag the latest `main`:

```bash
git switch main
git pull --ff-only
git tag -a v1.0.1 -m "New API Monitor v1.0.1"
git push origin v1.0.1
```

The release workflow builds AMD64/ARM64 images, publishes GHCR tags, and creates the GitHub Release.

## Security Rules

- Never commit `.env`, databases, backups, cookies, tokens, webhooks, server addresses, or raw production logs.
- Never force-push `main` or published version tags.
- Do not delete published `v*` tags; publish a new patch release instead.
- Review Dependabot major-version upgrades separately and never auto-merge them blindly.
- If a workflow failure blocks merging, repair the workflow on a feature branch. Temporarily change protection only for repository recovery, and restore it immediately.
