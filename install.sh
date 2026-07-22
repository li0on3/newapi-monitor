#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

REPOSITORY="li0on3/newapi-monitor"
IMAGE_REPOSITORY="ghcr.io/li0on3/newapi-monitor"
INSTALL_DIR="/opt/newapi-monitor"
BIND_ADDRESS="127.0.0.1"
PUBLIC_HOST=""
VERSION=""
INSTALL_DOCKER=false

log() { printf '[newapi-monitor] %s\n' "$*"; }
fail() { printf '[newapi-monitor] ERROR: %s\n' "$*" >&2; exit 1; }
usage() {
  cat <<'EOF'
Usage: install.sh [options]
  --version VERSION       Install a specific release (default: latest)
  --install-dir PATH      Installation directory (default: /opt/newapi-monitor)
  --bind ADDRESS          Published listen address (default: 127.0.0.1)
  --host HOSTNAME         Add the reverse-proxy hostname to the allowlist
  --install-docker        Install Docker with Docker's official convenience script if missing
  --upgrade               Preserve configuration and upgrade an existing installation
  -h, --help              Show this help
EOF
}

while (($#)); do
  case "$1" in
    --version) VERSION="${2:-}"; shift 2 ;;
    --install-dir) INSTALL_DIR="${2:-}"; shift 2 ;;
    --bind) BIND_ADDRESS="${2:-}"; shift 2 ;;
    --host) PUBLIC_HOST="${2:-}"; shift 2 ;;
    --install-docker) INSTALL_DOCKER=true; shift ;;
    --upgrade) shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown option: $1" ;;
  esac
done

[[ "$(id -u)" == "0" ]] || fail "run as root (for example: sudo bash install.sh)"
[[ -n "$INSTALL_DIR" && "$INSTALL_DIR" != "/" ]] || fail "unsafe installation directory"
[[ ! -L "$INSTALL_DIR" ]] || fail "installation directory must not be a symbolic link"
command -v curl >/dev/null || fail "curl is required"
command -v tar >/dev/null || fail "tar is required"
command -v sha256sum >/dev/null || fail "sha256sum is required"
command -v openssl >/dev/null || fail "openssl is required"

if ! command -v docker >/dev/null; then
  $INSTALL_DOCKER || fail "Docker is missing; rerun with --install-docker after reviewing https://get.docker.com"
  log "installing Docker from the official Docker convenience script"
  docker_script="$(mktemp)"
  trap 'rm -f "${docker_script:-}"; rm -rf "${work_dir:-}"' EXIT
  curl -fsSL https://get.docker.com -o "$docker_script"
  sh "$docker_script"
fi
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required"

if [[ -z "$VERSION" ]]; then
  latest_url="$(curl -fsSL -o /dev/null -w '%{url_effective}' "https://github.com/$REPOSITORY/releases/latest")"
  VERSION="${latest_url##*/v}"
fi
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "invalid release version: $VERSION"

asset="newapi-monitor-bundle-$VERSION.tar.gz"
work_dir="$(mktemp -d)"
trap 'rm -f "${docker_script:-}"; rm -rf "${work_dir:-}"' EXIT
base_url="https://github.com/$REPOSITORY/releases/download/v$VERSION"
log "downloading release v$VERSION"
curl -fL --retry 3 --retry-all-errors -o "$work_dir/$asset" "$base_url/$asset"
curl -fL --retry 3 --retry-all-errors -o "$work_dir/$asset.sha256" "$base_url/$asset.sha256"
(cd "$work_dir" && sha256sum --check "$asset.sha256")
mkdir "$work_dir/bundle"
while IFS= read -r entry; do
  [[ "$entry" != /* && "/$entry/" != *"/../"* ]] || fail "release bundle contains an unsafe path"
done < <(tar -tzf "$work_dir/$asset")
tar -xzf "$work_dir/$asset" --no-same-owner --no-same-permissions -C "$work_dir/bundle"
for required_file in compose.yaml monitorctl install.sh VERSION LICENSE; do
  [[ -f "$work_dir/bundle/$required_file" && ! -L "$work_dir/bundle/$required_file" ]] || fail "release bundle is incomplete"
done

mkdir -p "$INSTALL_DIR"
if [[ -f "$INSTALL_DIR/.env" ]]; then
  if [[ -x "$INSTALL_DIR/monitorctl" ]]; then
    log "creating a pre-upgrade backup"
    NEWAPI_MONITOR_HOME="$INSTALL_DIR" "$INSTALL_DIR/monitorctl" backup >/dev/null
  fi
  old_image="$(sed -n 's/^MONITOR_IMAGE=//p' "$INSTALL_DIR/.env" | tail -n 1)"
  [[ -n "$old_image" ]] && printf '%s\n' "$old_image" > "$INSTALL_DIR/.previous-image"
fi
install -m 0644 "$work_dir/bundle/compose.yaml" "$INSTALL_DIR/compose.yaml"
install -m 0755 "$work_dir/bundle/monitorctl" "$INSTALL_DIR/monitorctl"
install -m 0755 "$work_dir/bundle/install.sh" "$INSTALL_DIR/install.sh"
install -m 0644 "$work_dir/bundle/VERSION" "$INSTALL_DIR/VERSION"
install -m 0644 "$work_dir/bundle/LICENSE" "$INSTALL_DIR/LICENSE"
[[ -f "$work_dir/bundle/.env.example" ]] && install -m 0644 "$work_dir/bundle/.env.example" "$INSTALL_DIR/.env.example"
ln -sfn "$INSTALL_DIR/monitorctl" /usr/local/bin/monitorctl

set_env() {
  local key="$1" value="$2" env_file="$INSTALL_DIR/.env" temporary
  temporary="$(mktemp "$INSTALL_DIR/.env.XXXXXX")"
  if [[ -f "$env_file" ]]; then grep -v "^${key}=" "$env_file" > "$temporary" || true; fi
  printf '%s=%s\n' "$key" "$value" >> "$temporary"
  chmod 0600 "$temporary"
  mv "$temporary" "$env_file"
}

setup_token=""
admin_password=""
if [[ ! -f "$INSTALL_DIR/.env" ]]; then
  hostname_value="$(hostname -f 2>/dev/null || hostname)"
  primary_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  allowed_hosts="localhost,127.0.0.1,$hostname_value"
  [[ -n "$primary_ip" ]] && allowed_hosts="$allowed_hosts,$primary_ip"
  [[ -n "$PUBLIC_HOST" ]] && allowed_hosts="$allowed_hosts,$PUBLIC_HOST"
  admin_password="$(openssl rand -base64 24 | tr -d '\n')"
  setup_token="$(openssl rand -hex 24)"
  setup_hash="$(printf '%s' "$setup_token" | sha256sum | awk '{print $1}')"
  setup_expires="$(( $(date +%s) + 900 ))"
  cat > "$INSTALL_DIR/.env" <<EOF
NEW_API_BASE_URL=
NEW_API_ACCESS_TOKEN=
NEW_API_USER_ID=0
RELAY_API_TOKEN=
STATE_DB=/data/monitor.db
DASHBOARD_ADMIN_USERNAME=admin
DASHBOARD_ADMIN_PASSWORD=$admin_password
DASHBOARD_COOKIE_PATH=/monitor
DASHBOARD_COOKIE_SECURE=false
DASHBOARD_ALLOWED_HOSTS=$allowed_hosts
MONITOR_SECRET_KEY=$(openssl rand -hex 32)
MONITOR_WORKER_ENABLED=true
TRUST_PROXY_HEADERS=true
MONITOR_BIND_ADDRESS=$BIND_ADDRESS
MONITOR_PORT=18081
MONITOR_IMAGE=$IMAGE_REPOSITORY:$VERSION
SETUP_TOKEN_HASH=$setup_hash
SETUP_TOKEN_EXPIRES_AT=$setup_expires
EOF
  chmod 0600 "$INSTALL_DIR/.env"
else
  set_env MONITOR_IMAGE "$IMAGE_REPOSITORY:$VERSION"
  set_env MONITOR_BIND_ADDRESS "$BIND_ADDRESS"
fi

cd "$INSTALL_DIR"
log "pulling container image $IMAGE_REPOSITORY:$VERSION"
docker compose --env-file .env pull
docker compose --env-file .env up -d --no-build --remove-orphans

port="$(sed -n 's/^MONITOR_PORT=//p' .env | tail -n 1)"; port="${port:-18081}"
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$port/api/health" >/dev/null 2>&1; then break; fi
  sleep 3
done
curl -fsS "http://127.0.0.1:$port/api/health" >/dev/null || fail "service did not become healthy; run: monitorctl logs"

log "installation complete: v$VERSION"
printf 'Dashboard: http://127.0.0.1:%s/monitor/\n' "$port"
if [[ -n "$setup_token" ]]; then
  printf '\nSAVE THESE VALUES NOW (shown once):\n'
  printf 'Emergency admin password: %s\n' "$admin_password"
  printf 'One-time setup token (15 minutes): %s\n' "$setup_token"
  printf '\nRemote access before configuring a reverse proxy:\nssh -L %s:127.0.0.1:%s user@server\n' "$port" "$port"
fi
