#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -f .env ]; then
  python3 manage.py init
  echo "请编辑 $(pwd)/.env，填写 New API、SMTP 和域名配置，然后重新运行 ./install.sh"
  exit 0
fi

python3 manage.py doctor
docker compose pull docker-proxy
docker compose build monitor
docker compose up -d --remove-orphans
docker compose ps
curl --fail --silent --show-error http://127.0.0.1:${MONITOR_PORT:-18081}/api/health
echo
