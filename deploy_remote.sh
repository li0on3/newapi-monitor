#!/usr/bin/env bash
set -euo pipefail

release_archive=/tmp/newapi-monitor-release.tar.gz
release_env=/tmp/newapi-monitor.env
timestamp=$(date +%Y%m%d-%H%M%S)
stage_dir="/tmp/newapi-monitor-release-${timestamp}"
target_dir=/opt/newapi-monitor
backup_dir="/opt/newapi-monitor-backup-${timestamp}"
cleanup() {
  rm -rf "$stage_dir"
  rm -f "$release_archive" "$release_env"
}
trap cleanup EXIT

test -s "$release_archive"
test -s "$release_env"
install -d -m 0750 "$stage_dir"
tar -xzf "$release_archive" -C "$stage_dir"

if [ -d "$target_dir" ]; then
  cp -a "$target_dir" "$backup_dir"
else
  install -d -m 0750 "$target_dir"
fi

cp -a "$stage_dir"/. "$target_dir"/
install -m 0600 "$release_env" "$target_dir/.env"
chown -R root:root "$target_dir"
find "$target_dir" -type d -exec chmod 0750 {} +
find "$target_dir" -type f ! -name .env -exec chmod 0640 {} +
chmod 0600 "$target_dir/.env"

cd "$target_dir"
MONITOR_ENV_FILE=.env docker compose config --quiet
docker compose pull docker-proxy
docker compose build monitor
docker compose up -d --remove-orphans

healthy=0
for _attempt in $(seq 1 30); do
  if curl --fail --silent http://127.0.0.1:18081/api/health >/dev/null; then
    healthy=1
    break
  fi
  sleep 2
done

if [ "$healthy" -ne 1 ]; then
  docker compose ps -a
  docker compose logs --tail=100 monitor
  echo "monitor failed to become healthy" >&2
  exit 1
fi

docker compose ps
curl --fail --silent --show-error http://127.0.0.1:18081/api/health
printf '\n'
echo "backup=$backup_dir"
