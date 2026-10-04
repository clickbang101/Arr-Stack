#!/usr/bin/env bash
set -euo pipefail

# Prepares a host for the ARR stack: checks Docker, creates the shared network,
# creates a config file from the template and the app data directories, then
# optionally starts the stack. Safe to re-run — it never overwrites existing
# config or data.
#
#   ./setup.sh               prepare and offer to start
#   ./setup.sh --portainer   prepare only; deploy from Portainer afterwards

cd "$(dirname "$0")"

# ── colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}▶ $*${RESET}"; }
success() { echo -e "${GREEN}✔ $*${RESET}"; }
warn()    { echo -e "${YELLOW}⚠ $*${RESET}"; }
error()   { echo -e "${RED}✘ $*${RESET}"; exit 1; }
ask()     { echo -e "${BOLD}$*${RESET}"; }

# ── flags ─────────────────────────────────────────────────────────────────────
PORTAINER=false
for arg in "$@"; do
  case "$arg" in
    --portainer) PORTAINER=true ;;
    *) error "Unknown argument: $arg" ;;
  esac
done

ENV_FILE=".env"

# ── pre-flight checks ─────────────────────────────────────────────────────────
info "Checking requirements..."

command -v docker &>/dev/null \
  || error "Docker is not installed. Install it from https://docs.docker.com/engine/install/ and re-run."
docker info &>/dev/null \
  || error "Docker is not running or your user can't access it. Try: sudo usermod -aG docker \$USER (then log out and back in)"

success "Docker $(docker --version | grep -oP '[\d.]+' | head -1) is ready"

# ── shared network ────────────────────────────────────────────────────────────
if docker network inspect arr-net &>/dev/null; then
  success "Network arr-net already exists"
else
  docker network create arr-net >/dev/null
  success "Created network arr-net"
fi

# ── config file ───────────────────────────────────────────────────────────────
if [ -f "$ENV_FILE" ]; then
  success "Using existing $ENV_FILE"
else
  cp .env.example "$ENV_FILE"
  warn "Created $ENV_FILE from .env.example — review the paths, PUID/PGID and API keys in it."
fi

# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a

for var in APPDATA_PATH DATA_PATH MEDIA_PATH DOWNLOADS_PATH; do
  [ -n "${!var:-}" ] || error "$var is empty in $ENV_FILE"
done

# Hardlinked imports only work if both folders live inside DATA_PATH.
for var in MEDIA_PATH DOWNLOADS_PATH; do
  case "${!var%/}/" in
    "${DATA_PATH%/}"/*) ;;
    *) warn "$var is not inside DATA_PATH — imports will copy instead of hardlink" ;;
  esac
done

# ── directories ───────────────────────────────────────────────────────────────
info "Creating directories (existing ones are left alone)..."

mkdir -p \
  "${MEDIA_PATH}" \
  "${DOWNLOADS_PATH}" \
  "${DATA_PATH}/tdarr-cache" \
  "${APPDATA_PATH}"/{plex,tautulli,overseerr,maintainerr,sonarr,radarr,bazarr,prowlarr,jackett,qbittorrent} \
  "${APPDATA_PATH}"/tdarr/{server,configs,logs}

# Only fix ownership of the app data root's top level, so a restore of a large
# library doesn't trigger a slow recursive chown.
if [ "$(stat -c '%u:%g' "${APPDATA_PATH}")" != "${PUID}:${PGID}" ]; then
  sudo chown "${PUID}:${PGID}" "${APPDATA_PATH}" "${APPDATA_PATH}"/* \
    || warn "Could not set ownership — run: sudo chown -R ${PUID}:${PGID} ${APPDATA_PATH}"
fi

success "Directories ready"

if [ -z "${SONARR_API_KEY:-}" ] || [ -z "${RADARR_API_KEY:-}" ]; then
  warn "SONARR_API_KEY / RADARR_API_KEY are empty — Unpackerr won't work until you set them."
fi

# ── portainer mode: stop here ─────────────────────────────────────────────────
if $PORTAINER; then
  echo ""
  success "Host is ready. Now deploy in Portainer:"
  echo ""
  echo -e "  1. ${BOLD}Stacks → Add stack${RESET}, name it ${BOLD}arr-stack${RESET}"
  echo -e "  2. ${BOLD}Repository${RESET}: https://github.com/clickbang101/Arr-Stack, compose path docker-compose.yml"
  echo -e "  3. ${BOLD}Environment variables → Load variables from .env file${RESET} → upload $(pwd)/$ENV_FILE"
  echo -e "  4. ${BOLD}Deploy the stack${RESET}"
  echo ""
  exit 0
fi

# ── offer to start ────────────────────────────────────────────────────────────
echo ""
ask "Start the stack now? [Y/n]:"
read -r START
START="${START:-Y}"

if [[ "$START" =~ ^[Yy]$ ]]; then
  docker compose version &>/dev/null \
    || error "Docker Compose v2 is not available. Use Portainer or install a recent Docker."
  info "Starting containers (first run pulls images — may take a few minutes)..."
  docker compose up -d
  success "Stack is starting. Check with: make ps"
else
  echo -e "  Run ${BOLD}make up${RESET} whenever you're ready."
fi
