#!/usr/bin/env python3
"""arrbot: a small Telegram bot for the Arr stack. Standard library only.

Answers only the chats in TELEGRAM_CHAT_ID (comma-separated: your private chat
and, optionally, a family group). Read-only commands work for everyone in those
chats; actions (/restart, /clearstuck, /yes) only for TELEGRAM_ADMIN_IDS
(defaults to the private chat ID, which is your Telegram user ID).
Reads app API keys from the app config files mounted read-only at /appdata,
talks to Docker over its socket.

Commands: /status /disk /vpn /downloads /stuck /recent /leaving /watching
          /search <name> /restart <app> /clearstuck /help
Actions (/restart, /clearstuck) need a /yes within 60 seconds.
"""
import http.client
import json
import os
import re
import socket
import sys
import time
import traceback
import urllib.parse
import urllib.request
from datetime import datetime, timezone

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHATS = {c.strip() for c in os.environ["TELEGRAM_CHAT_ID"].split(",") if c.strip()}
ADMINS = {a.strip() for a in (os.environ.get("TELEGRAM_ADMIN_IDS") or ",".join(c for c in CHATS if not c.startswith("-"))).split(",") if a.strip()}
ACTIONS = {"restart", "clearstuck", "yes"}
APPDATA = os.environ.get("APPDATA_DIR", "/appdata")
DISKS = {"SSD (system, apps)": APPDATA, "Media disk": os.environ.get("MEDIA_DIR", "/media")}
PLEX = os.environ.get("PLEX_URL", "http://192.168.198.11:32400")
RESTARTABLE = {"plex", "sonarr", "radarr", "prowlarr", "qbittorrent", "gluetun", "seerr", "bazarr",
               "unpackerr", "flaresolverr", "jackett", "tautulli", "maintainerr", "uptime-kuma"}
STUCK_HOURS = 24
TG = f"https://api.telegram.org/bot{TOKEN}"
pending = {}  # (chat, user) -> (expires, description, function)
ctx = {"chat": None, "user": None}  # who sent the command being handled


# ---------- plumbing ----------
def http_json(url, headers=None, method="GET", body=None, timeout=20):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Accept": "application/json", "Content-Type": "application/json",
                                          **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if raw.strip() else None


class DockerConn(http.client.HTTPConnection):
    def __init__(self):
        super().__init__("localhost", timeout=30)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect("/var/run/docker.sock")


def docker(method, path):
    c = DockerConn()
    c.request(method, path)
    r = c.getresponse()
    raw = r.read()
    if r.status >= 300:
        raise RuntimeError(f"docker {path}: {r.status}")
    if "logs" in path:  # multiplexed stream: strip 8-byte frame headers
        out, i = [], 0
        while i + 8 <= len(raw):
            n = int.from_bytes(raw[i + 4:i + 8], "big")
            out.append(raw[i + 8:i + 8 + n])
            i += 8 + n
        return b"".join(out).decode(errors="replace")
    return json.loads(raw) if raw.strip() else None


def arr_key(app):
    return re.search(r"<ApiKey>([^<]+)", open(f"{APPDATA}/{app}/config.xml").read()).group(1)


def arr(app, path):
    port = {"sonarr": 8989, "radarr": 7878, "prowlarr": 9696}[app]
    ver = "v1" if app == "prowlarr" else "v3"
    return http_json(f"http://{app}:{port}/api/{ver}{path}", {"X-Api-Key": arr_key(app)})


def arr_delete_bulk(app, ids):
    port = {"sonarr": 8989, "radarr": 7878}[app]
    http_json(f"http://{app}:{port}/api/v3/queue/bulk?removeFromClient=true&blocklist=true&skipRedownload=false",
              {"X-Api-Key": arr_key(app)}, "DELETE", {"ids": ids})


def plex_token():
    prefs = open(f"{APPDATA}/plex/Library/Application Support/Plex Media Server/Preferences.xml").read()
    return re.search(r'PlexOnlineToken="([^"]+)"', prefs).group(1)


def gb(n):
    return f"{n / 2**30:,.0f} GB" if n < 2**40 else f"{n / 2**40:.2f} TB"


def ago(iso):
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    s = (datetime.now(timezone.utc) - t).total_seconds()
    return f"{s / 60:.0f} min ago" if s < 3600 else f"{s / 3600:.0f} h ago" if s < 172800 else f"{s / 86400:.0f} days ago"


def send(text, chat=None):
    for i in range(0, len(text), 3900):
        http_json(f"{TG}/sendMessage", body={"chat_id": chat or ctx["chat"], "text": text[i:i + 3900],
                                             "parse_mode": "HTML", "disable_web_page_preview": True})


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------- read-only commands ----------
def disk_lines():
    out = []
    for name, path in DISKS.items():
        st = os.statvfs(path)
        total, free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
        pct = 100 * (total - free) / total
        out.append(f"{'🔴' if pct >= 90 else '🟡' if pct >= 80 else '🟢'} {name}: {pct:.0f}% used, {gb(free)} free")
    return out


def vpn_info():
    try:
        logs = docker("GET", "/containers/gluetun/logs?stdout=1&stderr=1&tail=400")
    except Exception:
        return None, None, False
    country = re.findall(r"Public IP address is \S+ \(([^)]*?)(?: - source|\))", logs)
    port = re.findall(r"port forwarded is (\d+)", logs)
    health = docker("GET", "/containers/gluetun/json")["State"].get("Health", {}).get("Status")
    return (country[-1] if country else None), (port[-1] if port else None), health == "healthy"


def cmd_status(_):
    cons = docker("GET", "/containers/json?all=1")
    bad = []
    for c in cons:
        name = c["Names"][0].lstrip("/")
        if name == "homarr":
            continue
        if c["State"] != "running" or "unhealthy" in c.get("Status", ""):
            bad.append(f"{name} ({c['Status']})")
    country, port, ok = vpn_info()
    lines = ["<b>Arr server</b>",
             f"{'🟢' if not bad else '🔴'} Containers: {len(cons) - len(bad)} running" + (
                 "\n   ❗ " + "\n   ❗ ".join(map(esc, bad)) if bad else ""),
             f"{'🟢' if ok else '🔴'} VPN: {esc(country or 'unknown')}" + (f", port {port}" if port else "")]
    lines += disk_lines()
    q = sum(len(arr(a, "/queue?pageSize=500")["records"]) for a in ("sonarr", "radarr"))
    lines.append(f"⬇️ Download queue: {q}")
    return "\n".join(lines)


def cmd_disk(_):
    return "<b>Disk space</b>\n" + "\n".join(disk_lines())


def cmd_vpn(_):
    country, port, ok = vpn_info()
    return (f"<b>VPN</b>\n{'🟢 connected' if ok else '🔴 NOT healthy (torrents paused by kill switch)'}\n"
            f"Location: {esc(country or 'unknown')}\nForwarded port: {port or 'unknown'}")


def queue_items():
    items = []
    for app in ("sonarr", "radarr"):
        for r in arr(app, "/queue?pageSize=500")["records"]:
            r["_app"] = app
            items.append(r)
    return items


def cmd_downloads(_):
    items = [r for r in queue_items() if r.get("status") == "downloading"]
    if not items:
        return "Nothing downloading right now."
    lines = [f"<b>Downloading ({len(items)})</b>"]
    for r in sorted(items, key=lambda r: r["sizeleft"])[:15]:
        pct = 100 * (1 - r["sizeleft"] / max(r["size"], 1))
        lines.append(f"• {esc(r['title'][:60])} — {pct:.0f}%" + (f", {r['timeleft']} left" if r.get("timeleft") else ""))
    return "\n".join(lines)


def stuck_items():
    now = datetime.now(timezone.utc)
    out = []
    for r in queue_items():
        added = r.get("added")
        age = (now - datetime.fromisoformat(added.replace("Z", "+00:00"))).total_seconds() / 3600 if added else 0
        msgs = str(r.get("statusMessages")) + str(r.get("errorMessage"))
        if age >= STUCK_HOURS and (r.get("status") in ("warning", "queued", "paused") or "stalled" in msgs.lower()):
            out.append(r)
    return out


def cmd_stuck(_):
    items = stuck_items()
    if not items:
        return f"No downloads stuck for more than {STUCK_HOURS} h. 👍"
    lines = [f"<b>Stuck &gt;{STUCK_HOURS} h ({len(items)})</b>"]
    lines += [f"• {esc(r['title'][:60])} ({ago(r['added'])})" for r in items[:20]]
    lines.append("\nSend /clearstuck to remove + blocklist them and search again.")
    return "\n".join(lines)


def cmd_recent(_):
    ev = []
    for app, et in (("sonarr", "downloadFolderImported"), ("radarr", "downloadFolderImported")):
        for r in arr(app, f"/history?pageSize=15&sortKey=date&sortDirection=descending&eventType=3")["records"]:
            ev.append((r["date"], r["sourceTitle"]))
    ev.sort(reverse=True)
    return "<b>Recently added</b>\n" + "\n".join(f"• {esc(t[:60])} ({ago(d)})" for d, t in ev[:10])


def cmd_leaving(_):
    cols = http_json("http://maintainerr:6246/api/collections")
    rows = []
    for c in cols:
        if not c.get("isActive"):
            continue
        for m in c.get("media", []):
            add = datetime.fromisoformat(str(m["addDate"]).replace("Z", "+00:00").replace(" ", "T"))
            if add.tzinfo is None:
                add = add.replace(tzinfo=timezone.utc)
            due = add.timestamp() + c["deleteAfterDays"] * 86400
            rows.append((due, m.get("plexId"), c["title"]))
    if not rows:
        return "Nothing scheduled for removal."
    rows.sort()
    tok = plex_token()
    lines = [f"<b>Leaving soon ({len(rows)})</b> — watch something to keep it"]
    for due, pid, col in rows[:20]:
        try:
            t = http_json(f"{PLEX}/library/metadata/{pid}", {"X-Plex-Token": tok})["MediaContainer"]["Metadata"][0]["title"]
        except Exception:
            t = f"Plex item {pid}"
        lines.append(f"• {esc(t)} — {datetime.fromtimestamp(due).strftime('%d %b')}")
    if len(rows) > 20:
        lines.append(f"…and {len(rows) - 20} more")
    return "\n".join(lines)


def cmd_watching(_):
    s = http_json(f"{PLEX}/status/sessions", {"X-Plex-Token": plex_token()})["MediaContainer"]
    if not s.get("size"):
        return "Nobody is watching right now."
    lines = [f"<b>Watching now ({s['size']})</b>"]
    for v in s.get("Metadata", []):
        title = v.get("grandparentTitle", "") + (" – " if v.get("grandparentTitle") else "") + v.get("title", "")
        user = v.get("User", {}).get("title", "?")
        ts = v.get("TranscodeSession")
        mode = "direct play" if not ts else ("transcode (GPU)" if ts.get("transcodeHwFullPipeline") or ts.get("transcodeHwEncoding") else "transcode (CPU)")
        lines.append(f"• {esc(user)}: {esc(title)} — {mode}")
    return "\n".join(lines)


def cmd_search(arg):
    if not arg:
        return "Usage: /search <name>"
    q = arg.lower()
    out = []
    for s in arr("sonarr", "/series"):
        if q in s["title"].lower():
            st = s["statistics"]
            out.append(f"📺 {esc(s['title'])} ({s.get('year')}) — {st['episodeFileCount']}/{st['totalEpisodeCount']} episodes, "
                       f"{'monitored' if s['monitored'] else 'not monitored'}")
    for m in arr("radarr", "/movie"):
        if q in m["title"].lower():
            out.append(f"🎬 {esc(m['title'])} ({m.get('year')}) — {'✅ in library' if m['hasFile'] else '⏳ wanted' if m['monitored'] else 'not monitored'}")
    for r in queue_items():
        if q in r["title"].lower():
            out.append(f"⬇️ downloading: {esc(r['title'][:60])} {100 * (1 - r['sizeleft'] / max(r['size'], 1)):.0f}%")
    return "\n".join(out[:20]) or f"Nothing matching “{esc(arg)}” in Sonarr/Radarr. Request it in Seerr."


# ---------- actions (need /yes) ----------
def cmd_restart(arg):
    name = (arg or "").strip().lower()
    if name not in RESTARTABLE:
        return "Usage: /restart &lt;app&gt;\nApps: " + ", ".join(sorted(RESTARTABLE))

    def do():
        docker("POST", f"/containers/{name}/restart?t=30")
        return f"🔄 {name} restarted."
    pending[(ctx["chat"], ctx["user"])] = (time.time() + 60, f"restart {name}", do)
    return f"Restart <b>{name}</b>? Reply /yes within 60 s."


def cmd_clearstuck(_):
    items = stuck_items()
    if not items:
        return "Nothing stuck. 👍"

    def do():
        for app in ("sonarr", "radarr"):
            ids = [r["id"] for r in items if r["_app"] == app]
            if ids:
                arr_delete_bulk(app, ids)
        return f"🧹 Removed + blocklisted {len(items)} stuck download(s); searching for other releases."
    pending[(ctx["chat"], ctx["user"])] = (time.time() + 60, f"clear {len(items)} stuck downloads", do)
    return f"Remove + blocklist <b>{len(items)}</b> stuck download(s) and search again? Reply /yes within 60 s."


def cmd_yes(_):
    exp, desc, fn = pending.pop((ctx["chat"], ctx["user"]), (0, None, None))
    if not fn or time.time() > exp:
        return "Nothing to confirm (or it expired)."
    return fn()


def cmd_help(_):
    return ("<b>Commands</b>\n/status – everything at a glance\n/disk – disk space\n/vpn – VPN location + port\n"
            "/downloads – what's downloading\n/stuck – downloads stuck &gt;24 h\n/recent – last 10 added\n"
            "/leaving – what Maintainerr removes next\n/watching – who's streaming\n/search &lt;name&gt; – find a show/movie\n"
            "/restart &lt;app&gt; – restart one app (asks to confirm)\n/clearstuck – clear stuck downloads (asks to confirm)")


COMMANDS = {"status": cmd_status, "disk": cmd_disk, "vpn": cmd_vpn, "downloads": cmd_downloads, "stuck": cmd_stuck,
            "recent": cmd_recent, "leaving": cmd_leaving, "watching": cmd_watching, "search": cmd_search,
            "restart": cmd_restart, "clearstuck": cmd_clearstuck, "yes": cmd_yes, "help": cmd_help, "start": cmd_help}


def main():
    http_json(f"{TG}/setMyCommands", body={"commands": [
        {"command": c, "description": d} for c, d in (
            ("status", "Everything at a glance"), ("disk", "Disk space"), ("vpn", "VPN location and port"),
            ("downloads", "What's downloading"), ("stuck", "Downloads stuck >24h"), ("recent", "Last 10 added"),
            ("leaving", "What Maintainerr removes next"), ("watching", "Who's streaming"),
            ("search", "Find a show or movie"), ("restart", "Restart one app"),
            ("clearstuck", "Clear stuck downloads"), ("help", "All commands"))]})
    offset = None
    print("arrbot running", flush=True)
    while True:
        try:
            q = f"{TG}/getUpdates?timeout=50" + (f"&offset={offset}" if offset else "")
            for u in http_json(q, timeout=60).get("result", []):
                offset = u["update_id"] + 1
                msg = u.get("message") or {}
                chat = str(msg.get("chat", {}).get("id"))
                text = (msg.get("text") or "").strip()
                if chat not in CHATS:
                    if text.startswith("/"):  # helps find a new group's chat ID; never answers
                        print(f"ignored command from chat {chat} ({msg.get('chat', {}).get('title') or 'private'})", flush=True)
                    continue
                if not text.startswith("/"):
                    continue
                ctx["chat"], ctx["user"] = chat, str(msg.get("from", {}).get("id"))
                cmd, _, arg = text[1:].partition(" ")
                cmd = cmd.split("@")[0].lower()
                if cmd in ACTIONS and ctx["user"] not in ADMINS:
                    send("🔒 Only the server admin can do that.")
                    continue
                fn = COMMANDS.get(cmd)
                try:
                    send(fn(arg.strip()) if fn else "Unknown command. /help")
                except Exception as e:
                    traceback.print_exc()
                    send(f"⚠️ {esc(cmd)} failed: {esc(e)}")
        except Exception:
            traceback.print_exc()
            time.sleep(10)


if __name__ == "__main__":
    sys.exit(main())
