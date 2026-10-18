#!/usr/bin/env bash
# Every 10 min (cron): if Plex can't see the GPU, restart Plex/Tdarr while nobody is watching.
# A systemd daemon-reload (e.g. unattended upgrades) can drop the NVIDIA devices from running
# containers ("Failed to initialize NVML: Unknown Error"), so Plex silently transcodes on the
# CPU and clients buffer. A container restart gives the GPU back.
set -uo pipefail

docker exec plex nvidia-smi -L >/dev/null 2>&1 && exit 0

prefs="$HOME/appdata/plex/Library/Application Support/Plex Media Server/Preferences.xml"
token=$(grep -o 'PlexOnlineToken="[^"]*"' "$prefs" | cut -d'"' -f2)
streams=$(curl -fsS -m 10 "http://localhost:32400/status/sessions?X-Plex-Token=$token" \
  | grep -o '<MediaContainer size="[0-9]*"' | tr -dc 0-9)

if [ "${streams:-1}" -gt 0 ]; then
  logger -t gpu-check "Plex lost the GPU; ${streams:-?} stream(s) active, restart postponed"
  exit 0
fi

logger -t gpu-check "Plex lost the GPU; restarting plex and tdarr"
docker restart plex >/dev/null
docker ps --format '{{.Names}}' | grep -qx tdarr && docker restart tdarr >/dev/null
sleep 10
docker exec plex nvidia-smi -L >/dev/null 2>&1 \
  && logger -t gpu-check "GPU back in Plex" \
  || logger -t gpu-check "GPU still missing after restart"
