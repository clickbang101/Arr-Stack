#!/usr/bin/env bash
# Streams a compressed backup of APPDATA_PATH to stdout, safe to run while the
# stack is up: every SQLite database is snapshotted with `.backup` first, so the
# archive never holds a half-written database. Caches, logs and artwork are left
# out (Plex/Sonarr/Radarr rebuild them).
#
#   Local:  scripts/backup-appdata.sh > appdata-$(date +%F).tar.zst
#   Remote: ssh user@docker-host backup-appdata.sh > appdata-$(date +%F).tar.zst
#   Restore: zstd -dc appdata-DATE.tar.zst | tar -x -C /path/to/parent-of-appdata
set -euo pipefail

APPDATA_PATH="${APPDATA_PATH:-$HOME/appdata}"
NAME="$(basename "$APPDATA_PATH")"
SNAP="$(mktemp -d "${TMPDIR:-/tmp}/appdata-backup.XXXXXX")"
trap 'rm -rf "$SNAP"' EXIT

PLEX="plex/Library/Application Support/Plex Media Server"
EXCLUDES=(
  --exclude="MediaCover/" --exclude=".cache/" --exclude="JobReports/" --exclude="logs/" --exclude="Logs/" --exclude="Backups/"
  --exclude="/$PLEX/Cache/" --exclude="/$PLEX/Media/" --exclude="/$PLEX/Metadata/"
  --exclude="/$PLEX/Crash Reports/" --exclude="/$PLEX/Updates/"
  --exclude="*-wal" --exclude="*-shm" --exclude="*.db" --exclude="*.sqlite"
)

# Copy everything except caches and databases into a staging folder...
# Files this user can't read (e.g. root-owned certs of other stacks) are
# reported and skipped (rsync 23/24) rather than failing the whole backup.
rc=0; rsync -a "${EXCLUDES[@]}" "$APPDATA_PATH/" "$SNAP/$NAME/" 2>"$SNAP/rsync.err" || rc=$?
if [ "$rc" -ne 0 ]; then
  [ "$rc" -eq 23 ] || [ "$rc" -eq 24 ] || { cat "$SNAP/rsync.err" >&2; exit "$rc"; }
  # Summarise per app folder. awk reads everything, so no SIGPIPE under pipefail.
  echo "warn: unreadable paths skipped (count per folder):" >&2
  awk -v base="$APPDATA_PATH/" 'match($0, /"[^"]+"/) {
      p = substr($0, RSTART + 1, RLENGTH - 2); sub("^" base, "", p); split(p, a, "/"); n[a[1]]++ }
    END { for (k in n) printf "  %6d  %s\n", n[k], k }' "$SNAP/rsync.err" >&2
fi

# ...then add consistent snapshots of the live databases at the same paths.
cd "$APPDATA_PATH"
while IFS= read -r -d '' db; do
  mkdir -p "$SNAP/$NAME/$(dirname "$db")"
  sqlite3 "file:$db?mode=ro" ".timeout 10000" ".backup '$SNAP/$NAME/$db'" 2>/dev/null \
    || { echo "warn: could not snapshot $db, copying it as-is" >&2; cp -p "$db" "$SNAP/$NAME/$db"; }
done < <(find . \( -path "./$PLEX/Cache" -o -path "./$PLEX/Media" -o -path "./$PLEX/Metadata" \) -prune \
           -o \( -name "*.db" -o -name "*.sqlite" \) -type f -size +0 -print0 2>/dev/null)

rm -f "$SNAP/rsync.err"
tar -C "$SNAP" -c "$NAME" | zstd -q -T0 -10
