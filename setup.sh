#!/usr/bin/env bash
set -euo pipefail

# ── colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}▶ $*${RESET}"; }
success() { echo -e "${GREEN}✔ $*${RESET}"; }
warn()    { echo -e "${YELLOW}⚠ $*${RESET}"; }
error()   { echo -e "${RED}✘ $*${RESET}"; exit 1; }
ask()     { echo -e "${BOLD}$*${RESET}"; }

# ── flags ─────────────────────────────────────────────────────────────────────
# --portainer   skip .env.local and the start prompt; just create dirs/permissions
PORTAINER=false
for arg in "$@"; do
  case "$arg" in
    --portainer) PORTAINER=true ;;
    *) error "Unknown argument: $arg" ;;
  esac
done

echo ""
echo -e "${BOLD}╔══════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║       ARR Stack  —  Setup            ║${RESET}"
if $PORTAINER; then
echo -e "${BOLD}║       (Portainer mode)               ║${RESET}"
fi
echo -e "${BOLD}╚══════════════════════════════════════╝${RESET}"
echo ""

# ── pre-flight checks ─────────────────────────────────────────────────────────
info "Checking requirements..."

if ! command -v docker &>/dev/null; then
  error "Docker is not installed. Install it from https://docs.docker.com/engine/install/ and re-run this script."
fi

if ! docker info &>/dev/null; then
  error "Docker is not running or your user can't access it. Try: sudo usermod -aG docker \$USER  (then log out and back in)"
fi

success "Docker $(docker --version | grep -oP '[\d.]+' | head -1) is ready"
echo ""

# ── detect timezone ───────────────────────────────────────────────────────────
DETECTED_TZ=""
if [ -f /etc/timezone ]; then
  DETECTED_TZ=$(cat /etc/timezone)
elif command -v timedatectl &>/dev/null; then
  DETECTED_TZ=$(timedatectl show --property=Timezone --value 2>/dev/null || true)
fi
DETECTED_TZ="${DETECTED_TZ:-UTC}"

# ── detect uid/gid ────────────────────────────────────────────────────────────
DETECTED_PUID=$(id -u)
DETECTED_PGID=$(id -g)

# ── ask questions ─────────────────────────────────────────────────────────────
echo -e "${BOLD}── Where do you want to store things? ──────────────────────${RESET}"
echo ""
echo "  • Media library  — your final TV shows, movies, books"
echo "  • Downloads      — temporary folder while torrents are downloading"
echo "  • App data       — container configs and databases (SSD recommended)"
echo ""

ask "Media library path [/data/media]:"
read -r MEDIA_PATH
MEDIA_PATH="${MEDIA_PATH:-/data/media}"

ask "Downloads path [/data/downloads]:"
read -r DOWNLOADS_PATH
DOWNLOADS_PATH="${DOWNLOADS_PATH:-/data/downloads}"

ask "App data path [/data/appdata]:"
read -r APPDATA_PATH
APPDATA_PATH="${APPDATA_PATH:-/data/appdata}"

# ── create directories ────────────────────────────────────────────────────────
info "Creating directories..."

mkdir -p \
  "${MEDIA_PATH}" \
  "${DOWNLOADS_PATH}" \
  "${APPDATA_PATH}/plex" \
  "${APPDATA_PATH}/qbittorrent" \
  "${APPDATA_PATH}/sonarr" \
  "${APPDATA_PATH}/radarr" \
  "${APPDATA_PATH}/prowlarr" \
  "${APPDATA_PATH}/bazarr" \
  "${APPDATA_PATH}/overseerr"

success "Directories created"

# ── fix permissions ───────────────────────────────────────────────────────────
info "Setting permissions..."

for dir in "${MEDIA_PATH}" "${DOWNLOADS_PATH}" "${APPDATA_PATH}"; do
  if [ -w "$dir" ]; then
    chown -R "${DETECTED_PUID}:${DETECTED_PGID}" "$dir" 2>/dev/null || true
  else
    sudo chown -R "${DETECTED_PUID}:${DETECTED_PGID}" "$dir" || warn "Could not set ownership of $dir — run: sudo chown -R \$(id -u):\$(id -g) $dir"
  fi
done

success "Permissions set"

# ── portainer mode: stop here ─────────────────────────────────────────────────
if $PORTAINER; then
  echo ""
  success "Directories and permissions are ready."
  echo ""
  echo -e "  Now deploy the stack in Portainer:"
  echo ""
  echo -e "  1. Open Portainer → ${BOLD}Stacks → Add Stack${RESET}"
  echo -e "  2. Choose ${BOLD}Repository${RESET} and enter your repo URL, or paste the docker-compose.yml"
  echo -e "  3. Scroll to ${BOLD}Environment variables${RESET} and add these values:"
  echo ""
  echo -e "     PUID              = ${DETECTED_PUID}"
  echo -e "     PGID              = ${DETECTED_PGID}"
  echo -e "     TZ                = ${DETECTED_TZ}"
  echo -e "     MEDIA_PATH        = ${MEDIA_PATH}"
  echo -e "     DOWNLOADS_PATH    = ${DOWNLOADS_PATH}"
  echo -e "     APPDATA_PATH      = ${APPDATA_PATH}"
  echo -e "     RESTART_POLICY    = unless-stopped"
  echo -e "     PLEX_VERSION      = docker"
  echo -e "     QBITTORRENT_PORT  = 8081"
  echo -e "     SONARR_PORT       = 8989"
  echo -e "     RADARR_PORT       = 7878"
  echo -e "     PROWLARR_PORT     = 9696"
  echo -e "     BAZARR_PORT       = 6767"
  echo -e "     OVERSEERR_PORT    = 5055"
  echo -e "     JACKETT_PORT      = 9117"
  echo -e "     FLARESOLVERR_PORT = 8191"
  echo -e "     LAZYLIBRARIAN_PORT= 8299"
  echo ""
  echo -e "  4. Click ${BOLD}Deploy the stack${RESET}"
  echo ""
  echo -e "${GREEN}${BOLD}Setup complete.${RESET}"
  echo ""
  exit 0
fi

echo ""
echo -e "${BOLD}── Timezone ─────────────────────────────────────────────────${RESET}"
ask "Your timezone [${DETECTED_TZ}]:"
read -r TZ
TZ="${TZ:-$DETECTED_TZ}"

echo ""
echo -e "${BOLD}── Ports (press Enter to keep defaults) ─────────────────────${RESET}"
ask "qBittorrent Web UI port [8081]:"
read -r QBITTORRENT_PORT
QBITTORRENT_PORT="${QBITTORRENT_PORT:-8081}"

ask "Sonarr port [8989]:"
read -r SONARR_PORT
SONARR_PORT="${SONARR_PORT:-8989}"

ask "Radarr port [7878]:"
read -r RADARR_PORT
RADARR_PORT="${RADARR_PORT:-7878}"

ask "Prowlarr port [9696]:"
read -r PROWLARR_PORT
PROWLARR_PORT="${PROWLARR_PORT:-9696}"

ask "Bazarr port [6767]:"
read -r BAZARR_PORT
BAZARR_PORT="${BAZARR_PORT:-6767}"

ask "Overseerr port [5055]:"
read -r OVERSEERR_PORT
OVERSEERR_PORT="${OVERSEERR_PORT:-5055}"

# ── write .env.local ──────────────────────────────────────────────────────────
echo ""

if [ -f .env.local ]; then
  warn ".env.local already exists."
  ask "Overwrite it? Your current settings will be lost. [y/N]:"
  read -r OVERWRITE
  OVERWRITE="${OVERWRITE:-N}"
  if [[ ! "$OVERWRITE" =~ ^[Yy]$ ]]; then
    echo -e "  Keeping existing .env.local — skipping config write."
    SKIP_ENV=true
  else
    SKIP_ENV=false
  fi
else
  SKIP_ENV=false
fi

if ! $SKIP_ENV; then
  info "Writing .env.local..."
  cat > .env.local <<EOF
# Generated by setup.sh — edit this file to change your configuration

PUID=${DETECTED_PUID}
PGID=${DETECTED_PGID}
TZ=${TZ}

MEDIA_PATH=${MEDIA_PATH}
DOWNLOADS_PATH=${DOWNLOADS_PATH}
APPDATA_PATH=${APPDATA_PATH}

QBITTORRENT_PORT=${QBITTORRENT_PORT}
SONARR_PORT=${SONARR_PORT}
RADARR_PORT=${RADARR_PORT}
PROWLARR_PORT=${PROWLARR_PORT}
BAZARR_PORT=${BAZARR_PORT}
OVERSEERR_PORT=${OVERSEERR_PORT}
JACKETT_PORT=9117
FLARESOLVERR_PORT=8191
LAZYLIBRARIAN_PORT=8299

RESTART_POLICY=unless-stopped
PLEX_VERSION=docker
EOF
  success ".env.local created"
fi

# ── optional extras ───────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}── Optional extras ──────────────────────────────────────────${RESET}"
echo "  Jackett, FlareSolverr, and LazyLibrarian are available but not"
echo "  started by default. Enable them later with: make extras"
echo ""

# ── offer to start ────────────────────────────────────────────────────────────
ask "Start the stack now? [Y/n]:"
read -r START
START="${START:-Y}"

echo ""
if [[ "$START" =~ ^[Yy]$ ]]; then
  if ! docker compose version &>/dev/null; then
    error "Docker Compose v2 is not available. Use Portainer or install a recent version of Docker."
  fi
  info "Starting containers (this will pull images on first run — may take a few minutes)..."
  docker compose --env-file .env.local up -d
  echo ""
  success "Stack is starting up!"
  echo ""
  echo -e "  Give containers about ${BOLD}60 seconds${RESET} to become healthy, then open:"
  echo ""
  echo -e "  ${CYAN}Plex       ${RESET}→  http://localhost:32400/web"
  echo -e "  ${CYAN}Sonarr     ${RESET}→  http://localhost:${SONARR_PORT}"
  echo -e "  ${CYAN}Radarr     ${RESET}→  http://localhost:${RADARR_PORT}"
  echo -e "  ${CYAN}qBittorrent${RESET}→  http://localhost:${QBITTORRENT_PORT}"
  echo -e "  ${CYAN}Prowlarr   ${RESET}→  http://localhost:${PROWLARR_PORT}"
  echo -e "  ${CYAN}Bazarr     ${RESET}→  http://localhost:${BAZARR_PORT}"
  echo -e "  ${CYAN}Overseerr  ${RESET}→  http://localhost:${OVERSEERR_PORT}"
  echo ""
  echo -e "  Check status: ${BOLD}docker compose ps${RESET}"
  echo -e "  View logs:    ${BOLD}make logs${RESET}"
else
  echo -e "  Run ${BOLD}make up${RESET} whenever you're ready."
fi

echo ""
echo -e "${GREEN}${BOLD}Setup complete.${RESET}"
echo ""
