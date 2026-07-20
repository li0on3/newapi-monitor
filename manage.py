from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PLACEHOLDER_MARKERS = ("replace-with-", "not-a-real-", "example.com", "monitor.example.com")


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def write_env(path: Path, values: dict[str, str]) -> None:
    lines = []
    for raw_line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if raw_line and not raw_line.lstrip().startswith("#") and "=" in raw_line:
            key = raw_line.split("=", 1)[0]
            if key in values:
                raw_line = f"{key}={values[key]}"
        lines.append(raw_line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    if os.name != "nt":
        path.chmod(0o600)


def command_init(args: argparse.Namespace) -> int:
    target = Path(args.env_file).resolve()
    if target.exists() and not args.force:
        print(f"配置文件已存在：{target}（使用 --force 覆盖）", file=sys.stderr)
        return 2
    values = read_env(ROOT / ".env.example")
    password = secrets.token_urlsafe(18)
    values["DASHBOARD_ADMIN_PASSWORD"] = password
    values["MONITOR_SECRET_KEY"] = secrets.token_urlsafe(48)
    write_env(target, values)
    print(json.dumps({
        "env_file": str(target),
        "emergency_admin": values.get("DASHBOARD_ADMIN_USERNAME", "admin"),
        "emergency_password": password,
        "next": "填写 NEW_API_*、至少一种通知渠道和 DASHBOARD_ALLOWED_HOSTS 后运行 python manage.py doctor",
    }, ensure_ascii=False, indent=2))
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    env_path = Path(args.env_file).resolve()
    problems: list[str] = []
    warnings: list[str] = []
    if not env_path.is_file():
        problems.append(f"配置文件不存在：{env_path}")
        values: dict[str, str] = {}
    else:
        values = read_env(env_path)

    required = (
        "NEW_API_BASE_URL", "NEW_API_ACCESS_TOKEN", "NEW_API_USER_ID",
        "DASHBOARD_ADMIN_PASSWORD", "MONITOR_SECRET_KEY", "DASHBOARD_ALLOWED_HOSTS",
    )
    for key in required:
        value = values.get(key, "")
        if not value or any(marker in value for marker in PLACEHOLDER_MARKERS):
            problems.append(f"{key} 未配置或仍为示例值")
    base_url = values.get("NEW_API_BASE_URL", "")
    if base_url:
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            problems.append("NEW_API_BASE_URL 必须是绝对 HTTP(S) 地址")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            problems.append("NEW_API_BASE_URL 不得包含账号密码、查询参数或 fragment")
    if len(values.get("DASHBOARD_ADMIN_PASSWORD", "")) < 12:
        problems.append("DASHBOARD_ADMIN_PASSWORD 至少需要 12 个字符")
    if len(values.get("MONITOR_SECRET_KEY", "")) < 32:
        problems.append("MONITOR_SECRET_KEY 至少需要 32 个字符")
    if "*" in {item.strip() for item in values.get("DASHBOARD_ALLOWED_HOSTS", "").split(",")}:
        problems.append("DASHBOARD_ALLOWED_HOSTS 不应在生产环境使用通配符 *")
    if values.get("SMTP_STARTTLS", "").lower() == "true" and values.get("SMTP_SSL", "").lower() == "true":
        problems.append("SMTP_STARTTLS 与 SMTP_SSL 不能同时启用")
    notification_enabled = False
    notification_requirements = {
        "EMAIL_ENABLED": ("SMTP_HOST", "SMTP_TO"),
        "WECOM_APP_ENABLED": ("WECOM_CORP_ID", "WECOM_AGENT_ID", "WECOM_APP_SECRET"),
        "WECOM_WEBHOOK_ENABLED": ("WECOM_WEBHOOK_URL",),
        "FEISHU_APP_ENABLED": ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_RECEIVE_ID"),
        "FEISHU_WEBHOOK_ENABLED": ("FEISHU_WEBHOOK_URL",),
    }
    for enabled_key, requirements in notification_requirements.items():
        if values.get(enabled_key, "").lower() != "true":
            continue
        notification_enabled = True
        for key in requirements:
            if not values.get(key, "") or any(marker in values.get(key, "") for marker in PLACEHOLDER_MARKERS):
                problems.append(f"{enabled_key} 已启用，但 {key} 未配置或仍为示例值")
    if not notification_enabled:
        warnings.append("尚未启用通知渠道；监控会运行，但异常只记录在事件页面")
    if values.get("MONITOR_BIND_ADDRESS", "127.0.0.1") not in {"127.0.0.1", "::1", "localhost"}:
        warnings.append("监控端口不是仅回环监听，请确认外层防火墙和 TLS 反向代理")
    if values.get("DASHBOARD_COOKIE_SECURE", "true").lower() != "true":
        warnings.append("生产 HTTPS 环境应启用 DASHBOARD_COOKIE_SECURE")

    compose_ok = False
    if shutil.which("docker"):
        compose_environment = os.environ.copy()
        compose_environment["MONITOR_ENV_FILE"] = str(env_path)
        result = subprocess.run(
            ["docker", "compose", "--env-file", str(env_path), "config", "--quiet"],
            cwd=ROOT,
            env=compose_environment,
            capture_output=True,
            text=True,
            check=False,
        )
        compose_ok = result.returncode == 0
        if not compose_ok:
            problems.append("Docker Compose 配置无效：" + (result.stderr.strip() or result.stdout.strip()))
    else:
        warnings.append("未找到 docker，跳过 Compose 校验")

    report = {"ok": not problems, "env_file": str(env_path), "compose": compose_ok, "problems": problems, "warnings": warnings}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


def sqlite_backup(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30)
    target_connection = sqlite3.connect(target)
    try:
        source_connection.backup(target_connection)
        row = target_connection.execute("PRAGMA integrity_check").fetchone()
        if not row or row[0] != "ok":
            raise RuntimeError("backup integrity check failed")
    finally:
        target_connection.close()
        source_connection.close()


def command_backup(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).resolve()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = output_dir / f"newapi-monitor-{timestamp}.db"
    database = Path(args.database)
    if database.is_file():
        sqlite_backup(database, output)
    else:
        if not shutil.which("docker"):
            raise RuntimeError("database is not local and docker is unavailable")
        remote_temp = f"/data/.backup-{secrets.token_hex(8)}.db"
        create_code = (
            "import sqlite3;"
            "s=sqlite3.connect('/data/monitor.db');"
            f"t=sqlite3.connect('{remote_temp}');"
            "s.backup(t);t.close();s.close()"
        )
        subprocess.run(["docker", "compose", "exec", "-T", "monitor", "python", "-c", create_code], cwd=ROOT, check=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(["docker", "compose", "cp", f"monitor:{remote_temp}", str(output)], cwd=ROOT, check=True)
        finally:
            cleanup_code = f"from pathlib import Path; Path('{remote_temp}').unlink(missing_ok=True)"
            subprocess.run(["docker", "compose", "exec", "-T", "monitor", "python", "-c", cleanup_code], cwd=ROOT, check=False)
        check = sqlite3.connect(output)
        try:
            if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("backup integrity check failed")
        finally:
            check.close()
    print(json.dumps({"backup": str(output), "note": "恢复时必须同时保留原 MONITOR_SECRET_KEY"}, ensure_ascii=False))
    return 0


def command_release_check(_: argparse.Namespace) -> int:
    problems: list[str] = []
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        problems.append("VERSION 必须使用 x.y.z 语义化版本")
    package = json.loads((ROOT / "web" / "package.json").read_text(encoding="utf-8"))
    if package.get("version") != version:
        problems.append("VERSION 与 web/package.json 版本不一致")

    required_files = (
        "README.md", "README_EN.md", "CONTRIBUTING.md", "CONTRIBUTING_EN.md",
        "SECURITY.md", "SECURITY_EN.md", "ROADMAP.md", "ROADMAP_EN.md",
        "CHANGELOG.md", "CHANGELOG_EN.md", "GITHUB_GUIDE.md", "GITHUB_GUIDE_EN.md", "LICENSE",
    )
    for name in required_files:
        if not (ROOT / name).is_file():
            problems.append(f"缺少发布文件：{name}")

    tracked = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=False
    )
    if tracked.returncode != 0:
        problems.append("无法读取 Git 跟踪文件")
        tracked_files: list[str] = []
    else:
        tracked_files = [name for name in tracked.stdout.decode("utf-8").split("\0") if name]
    forbidden_names = {".env", ".env.local", ".env.deploy", ".dashboard_credentials"}
    forbidden_suffixes = (".db", ".db-wal", ".db-shm", ".pem", ".key", ".tar.gz")
    for name in tracked_files:
        path = Path(name)
        if path.name in forbidden_names or name.endswith(forbidden_suffixes) or "state/" in name.replace("\\", "/"):
            problems.append(f"禁止提交的文件：{name}")
            continue
        try:
            content = (ROOT / path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".ico"}:
                problems.append(f"非 UTF-8 文本文件：{name}")
            continue
        if ("OWNER" + "/REPOSITORY") in content:
            problems.append(f"尚未替换 GitHub 仓库占位符：{name}")

    report = {"ok": not problems, "version": version, "tracked_files": len(tracked_files), "problems": problems}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="New API Monitor deployment and maintenance helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="生成安全的 .env 初始配置")
    init_parser.add_argument("--env-file", default=str(ROOT / ".env"))
    init_parser.add_argument("--force", action="store_true")
    init_parser.set_defaults(handler=command_init)

    doctor_parser = subparsers.add_parser("doctor", help="部署前配置与 Compose 自检")
    doctor_parser.add_argument("--env-file", default=str(ROOT / ".env"))
    doctor_parser.set_defaults(handler=command_doctor)

    backup_parser = subparsers.add_parser("backup", help="生成经过完整性校验的 SQLite 在线备份")
    backup_parser.add_argument("--database", default="/data/monitor.db")
    backup_parser.add_argument("--output-dir", default=str(ROOT / "backups"))
    backup_parser.set_defaults(handler=command_backup)

    release_parser = subparsers.add_parser("release-check", help="检查公开仓库与发布文件完整性")
    release_parser.set_defaults(handler=command_release_check)

    args = parser.parse_args()
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
