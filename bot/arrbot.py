#!/usr/bin/env python3
"""arrbot: a small Telegram bot for the Arr stack. Standard library only.

Answers only the chats in TELEGRAM_CHAT_ID (comma-separated: your private chat
and, optionally, a family group). Read-only commands work for everyone in those
chats; actions (/restart, /clearstuck, /yes) only for TELEGRAM_ADMIN_IDS
(defaults to the private chat ID, which is your Telegram user ID).
Reads app API keys from the app config files mounted read-only at /appdata,
talks to Docker over its socket.

Commands: /status /disk /vpn /downloads /stuck /recent /leaving /watching
          /search <name> /request <name> /restart <app> /clearstuck /help
Actions (/restart, /clearstuck) need a /yes within 60 seconds.

Background checks (every 15 min):
- Leaving soon: 2 days before Maintainerr deletes something, posts to every chat
  with a Keep button (excludes it in Maintainerr).
- Problems: fake releases (only an .exe inside) and downloads stuck 48 h+ are sent
  to the admins with a "Find another" button (remove + blocklist + search again).
Sent alerts are remembered in ARRBOT_STATE (default /data/state.json) so they
aren't repeated after a restart.
"""
import http.client
import json
import os
import re
import socket
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
from datetime import datetime, timezone

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHATS = {c.strip() for c in os.environ["TELEGRAM_CHAT_ID"].split(",") if c.strip()}
ADMINS = {a.strip() for a in (os.environ.get("TELEGRAM_ADMIN_IDS") or ",".join(c for c in CHATS if not c.startswith("-"))).split(",") if a.strip()}
ACTIONS = {"restart", "clearstuck", "yes", "fix"}
# Non-admins get DEFAULT commands; TELEGRAM_USER_COMMANDS overrides per person:
#   "123456=status,search;789012=status,search,leaving,watching"
DEFAULT = {c.strip() for c in (os.environ.get("TELEGRAM_DEFAULT_COMMANDS") or
           "status,disk,vpn,downloads,stuck,recent,leaving,search,request,keep").split(",") if c.strip()}
PER_USER = {}
for part in (os.environ.get("TELEGRAM_USER_COMMANDS") or "").split(";"):
    if "=" in part:
        uid, cmds = part.split("=", 1)
        PER_USER[uid.strip()] = {c.strip() for c in cmds.split(",") if c.strip()}


def allowed(user, cmd):
    if cmd in ("help", "start"):
        return True
    if user in ADMINS:
        return True
    if cmd in ACTIONS:
        return False
    return cmd in PER_USER.get(user, DEFAULT)
APPDATA = os.environ.get("APPDATA_DIR", "/appdata")
DISKS = {"SSD (system, apps)": APPDATA, "Media disk": os.environ.get("MEDIA_DIR", "/media")}
PLEX = os.environ.get("PLEX_URL", "http://192.168.198.11:32400")
RESTARTABLE = {"plex", "sonarr", "radarr", "prowlarr", "qbittorrent", "gluetun", "seerr", "bazarr",
               "unpackerr", "flaresolverr", "jackett", "tautulli", "maintainerr", "uptime-kuma"}
STUCK_HOURS = 24
STUCK_ALERT_HOURS = 48
LEAVING_ALERT_DAYS = 2
CHECK_EVERY = 900  # seconds between background checks
STATE_FILE = os.environ.get("ARRBOT_STATE", "/data/state.json")
MAINTAINERR = "http://maintainerr:6246/api"
SEERR = "http://seerr:5055/api/v1"
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


def chunks(text, size=3900):
    """Split on line boundaries so HTML tags are never cut (Telegram caps messages at 4096)."""
    out, cur = [], ""
    for line in text.split("\n"):
        while len(line) > size:
            out.append(line[:size])
            line = line[size:]
        if cur and len(cur) + 1 + len(line) > size:
            out.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    return out + [cur] if cur else out


def send(text, chat=None, buttons=None):
    """buttons: rows of (label, callback_data); attached to the last message part."""
    parts = chunks(text)
    for i, part in enumerate(parts):
        body = {"chat_id": chat or ctx["chat"], "text": part, "parse_mode": "HTML", "disable_web_page_preview": True}
        if buttons and i == len(parts) - 1:
            body["reply_markup"] = {"inline_keyboard": [[{"text": t[:60], "callback_data": d} for t, d in row]
                                                        for row in buttons]}
        http_json(f"{TG}/sendMessage", body=body)


# ---------- remembered alerts ----------
state_lock = threading.Lock()


def load_state():
    try:
        return json.load(open(STATE_FILE))
    except (OSError, ValueError):
        return {}


state = load_state()


def already_sent(key):
    """True if this alert went out before; otherwise records it and returns False."""
    with state_lock:
        if key in state:
            return True
        state[key] = time.time()
        cutoff = time.time() - 30 * 86400
        for k in [k for k, t in state.items() if t < cutoff]:
            del state[k]
        try:
            with open(STATE_FILE + ".tmp", "w") as f:
                json.dump(state, f)
            os.replace(STATE_FILE + ".tmp", STATE_FILE)
        except OSError as e:
            print(f"can't save {STATE_FILE} ({e}); alerts may repeat after a restart", flush=True)
        return False


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


def stuck_items(hours=STUCK_HOURS):
    now = datetime.now(timezone.utc)
    out = []
    for r in queue_items():
        added = r.get("added")
        age = (now - datetime.fromisoformat(added.replace("Z", "+00:00"))).total_seconds() / 3600 if added else 0
        msgs = str(r.get("statusMessages")) + str(r.get("errorMessage"))
        if age >= hours and (r.get("status") in ("warning", "queued", "paused") or "stalled" in msgs.lower()):
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


def leaving_rows():
    """(due timestamp, plex id, collection title), soonest first."""
    rows = []
    for c in http_json(f"{MAINTAINERR}/collections"):
        if not c.get("isActive"):
            continue
        for m in c.get("media", []):
            add = datetime.fromisoformat(str(m["addDate"]).replace("Z", "+00:00").replace(" ", "T"))
            if add.tzinfo is None:
                add = add.replace(tzinfo=timezone.utc)
            due = add.timestamp() + c["deleteAfterDays"] * 86400
            rows.append((due, m.get("plexId"), c["title"]))
    return sorted(rows)


def cmd_leaving(_):
    rows = leaving_rows()
    if not rows:
        return "Nothing scheduled for removal."
    tok = plex_token()
    lines = [f"<b>Leaving soon ({len(rows)})</b> — watch something to keep it"]
    day = None
    for due, pid, col in rows:
        when = datetime.fromtimestamp(due).strftime("%a %d %b")
        if when != day:
            day = when
            lines.append(f"\n<b>{when}</b>")
        lines.append(f"• {esc(plex_title(pid, tok))}")
    return "\n".join(lines)


def plex_title(pid, tok):
    try:
        m = http_json(f"{PLEX}/library/metadata/{pid}", {"X-Plex-Token": tok})["MediaContainer"]["Metadata"][0]
    except Exception:
        return f"Plex item {pid}"
    if m.get("type") == "season":
        return f"{m.get('parentTitle', '?')} – {m['title']}"
    if m.get("type") == "episode":
        return f"{m.get('grandparentTitle', '?')} S{m.get('parentIndex', 0):02}E{m.get('index', 0):02}"
    return m["title"] + (f" ({m['year']})" if m.get("type") == "movie" and m.get("year") else "")


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
    return "\n".join(out[:20]) or f"Nothing matching “{esc(arg)}” in Sonarr/Radarr. Try /request {esc(arg)}"


def seerr(path, method="GET", body=None):
    key = json.load(open(f"{APPDATA}/overseerr/settings.json"))["main"]["apiKey"]
    return http_json(f"{SEERR}{path}", {"X-Api-Key": key}, method, body)


SEERR_STATUS = {2: "⏳ requested", 3: "⬇️ on its way", 4: "🟡 partly in library", 5: "✅ in library"}


def cmd_request(arg):
    if not arg:
        return "Usage: /request &lt;name&gt;"
    res = [r for r in seerr(f"/search?query={urllib.parse.quote(arg)}&page=1")["results"]
           if r.get("mediaType") in ("movie", "tv")][:6]
    if not res:
        return f"Nothing found for “{esc(arg)}”."
    lines, buttons = [f"<b>Results for “{esc(arg)}”</b> — tap one to request it"], []
    for r in res:
        name = r.get("title") or r.get("name")
        year = (r.get("releaseDate") or r.get("firstAirDate") or "")[:4]
        label = f"{'🎬' if r['mediaType'] == 'movie' else '📺'} {name}" + (f" ({year})" if year else "")
        status = SEERR_STATUS.get((r.get("mediaInfo") or {}).get("status"))
        lines.append(f"• {esc(label)}" + (f" — {status}" if status else ""))
        if not status:
            buttons.append([(label, f"req:{r['mediaType']}:{r['id']}")])
    if not buttons:
        lines.append("\nEverything here is already requested or in the library.")
    send("\n".join(lines), buttons=buttons)
    return None


def do_request(kind, tmdb_id):
    body = {"mediaType": kind, "mediaId": int(tmdb_id)}
    if kind == "tv":
        body["seasons"] = "all"
    seerr("/request", "POST", body)
    m = seerr(f"/{kind}/{tmdb_id}")
    return m.get("title") or m.get("name") or tmdb_id


def keep(plex_id):
    """Exclude from every Maintainerr rule and take it out of the leaving collections now."""
    http_json(f"{MAINTAINERR}/rules/exclusion", method="POST", body={"mediaId": int(plex_id), "action": 0})
    http_json(f"{MAINTAINERR}/collections/media?mediaId={int(plex_id)}", method="DELETE")


def fix_download(app, queue_id):
    port = {"sonarr": 8989, "radarr": 7878}[app]
    http_json(f"http://{app}:{port}/api/v3/queue/{int(queue_id)}?removeFromClient=true&blocklist=true&skipRedownload=false",
              {"X-Api-Key": arr_key(app)}, "DELETE")


# ---------- background checks ----------
def is_fake(r):
    """qBittorrent skips excluded files (.exe etc.); a 'finished' download with nothing selected is a fake."""
    msgs = (str(r.get("statusMessages")) + str(r.get("errorMessage"))).lower()
    return "executable" in msgs or ".exe" in msgs or (r.get("size") == 0 and r.get("status") == "completed")


def check_leaving():
    soon = time.time() + LEAVING_ALERT_DAYS * 86400
    rows, seen = [], set()
    for due, pid, _ in leaving_rows():
        if time.time() < due <= soon and pid not in seen:
            seen.add(pid)
            if not already_sent(f"leave:{pid}:{int(due) // 86400}"):
                rows.append((due, pid))
    if not rows:
        return
    tok = plex_token()
    for i in range(0, len(rows), 25):
        batch = rows[i:i + 25]
        lines = ["🗑 <b>Leaving soon</b> — watch it, or tap Keep to save it"]
        buttons = []
        for due, pid in batch:
            t = plex_title(pid, tok)
            lines.append(f"• {esc(t)} — {datetime.fromtimestamp(due).strftime('%a %d %b')}")
            buttons.append([(f"Keep {t}", f"keep:{pid}")])
        for chat in [c for c in CHATS if c.startswith("-")] or CHATS:  # groups if any, else private chats
            send("\n".join(lines), chat, buttons)


def check_problems():
    items = queue_items()
    stuck_ids = {(r["_app"], r["id"]) for r in stuck_items(STUCK_ALERT_HOURS)}
    found = []
    for r in items:
        if is_fake(r):
            kind = "🦠 Fake release (only an .exe inside, blocked)"
        elif (r["_app"], r["id"]) in stuck_ids:
            kind = f"🐢 Stuck {ago(r['added']).replace(' ago', '')}"
        else:
            continue
        if not already_sent(f"problem:{r.get('downloadId') or r['id']}"):
            found.append((kind, r))
    if not found:
        return
    lines = ["⚠️ <b>Download problems</b>"]
    lines += [f"• {kind}: {esc(r['title'][:60])}" for kind, r in found]
    buttons = [[(f"Find another: {r['title'][:40]}", f"fix:{r['_app']}:{r['id']}")] for _, r in found[:25]]
    for admin in ADMINS:
        send("\n".join(lines), admin, buttons)


def watcher():
    time.sleep(60)
    while True:
        for check in (check_leaving, check_problems):
            try:
                check()
            except Exception:
                traceback.print_exc()
        time.sleep(CHECK_EVERY)


# ---------- buttons ----------
def on_button(cq):
    user = str(cq["from"]["id"])
    who = cq["from"].get("first_name", "someone")
    msg = cq.get("message") or {}
    chat = str(msg.get("chat", {}).get("id"))
    data = cq.get("data", "")
    kind, *args = data.split(":")
    cmd = {"keep": "keep", "req": "request", "fix": "fix"}.get(kind)
    print(f"button {data} from {who} (user id {user})", flush=True)

    def toast(text):
        http_json(f"{TG}/answerCallbackQuery", body={"callback_query_id": cq["id"], "text": text})
    if chat not in CHATS or not cmd:
        return toast("Not available here.")
    if not allowed(user, cmd):
        return toast("🔒 You don't have access to that.")
    try:
        if cmd == "keep":
            keep(args[0])
            done = f"✅ {esc(plex_title(args[0], plex_token()))} will be kept (by {esc(who)})."
        elif cmd == "request":
            done = f"📥 Requested {esc(do_request(args[0], args[1]))} (by {esc(who)})."
        else:
            fix_download(args[0], args[1])
            done = f"🔁 Removed + blocklisted; {args[0]} is searching for another release."
    except Exception as e:
        traceback.print_exc()
        return toast(f"Failed: {e}"[:190])
    toast("Done")
    # Drop the pressed button so it can't be tapped twice.
    rows = [[b for b in row if b.get("callback_data") != data]
            for row in (msg.get("reply_markup") or {}).get("inline_keyboard", [])]
    http_json(f"{TG}/editMessageReplyMarkup", body={"chat_id": chat, "message_id": msg.get("message_id"),
                                                    "reply_markup": {"inline_keyboard": [r for r in rows if r]}})
    send(done, chat)


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


HELP = [("status", "everything at a glance"), ("disk", "disk space"), ("vpn", "VPN location + port"),
        ("downloads", "what's downloading"), ("stuck", "downloads stuck &gt;24 h"), ("recent", "last 10 added"),
        ("leaving", "what Maintainerr removes next"), ("watching", "who's streaming"),
        ("search", "&lt;name&gt; – find a show/movie"), ("request", "&lt;name&gt; – ask for a show/movie"),
        ("restart", "&lt;app&gt; – restart one app (asks to confirm)"),
        ("clearstuck", "clear stuck downloads (asks to confirm)")]


def cmd_help(_):
    mine = [f"/{c} – {d}" for c, d in HELP if allowed(ctx["user"], c)]
    return "<b>Your commands</b>\n" + "\n".join(mine)


COMMANDS = {"status": cmd_status, "disk": cmd_disk, "vpn": cmd_vpn, "downloads": cmd_downloads, "stuck": cmd_stuck,
            "recent": cmd_recent, "leaving": cmd_leaving, "watching": cmd_watching, "search": cmd_search,
            "request": cmd_request,
            "restart": cmd_restart, "clearstuck": cmd_clearstuck, "yes": cmd_yes, "help": cmd_help, "start": cmd_help}


def main():
    http_json(f"{TG}/setMyCommands", body={"commands": [
        {"command": c, "description": d} for c, d in (
            ("status", "Everything at a glance"), ("disk", "Disk space"), ("vpn", "VPN location and port"),
            ("downloads", "What's downloading"), ("stuck", "Downloads stuck >24h"), ("recent", "Last 10 added"),
            ("leaving", "What Maintainerr removes next"), ("watching", "Who's streaming"),
            ("search", "Find a show or movie"), ("request", "Ask for a show or movie"),
            ("restart", "Restart one app"),
            ("clearstuck", "Clear stuck downloads"), ("help", "All commands"))]})
    offset = None
    threading.Thread(target=watcher, daemon=True).start()
    print("arrbot running", flush=True)
    while True:
        try:
            q = f"{TG}/getUpdates?timeout=50" + (f"&offset={offset}" if offset else "")
            for u in http_json(q, timeout=60).get("result", []):
                offset = u["update_id"] + 1
                kinds = [k for k in u if k != "update_id"]
                if "my_chat_member" in u:  # bot added to / removed from a chat
                    c = u["my_chat_member"]["chat"]
                    print(f"membership update: {u['my_chat_member']['new_chat_member'].get('status')} in "
                          f"{c.get('type')} chat {c.get('id')} ({c.get('title') or 'private'})", flush=True)
                elif "callback_query" in u:
                    try:
                        on_button(u["callback_query"])
                    except Exception:
                        traceback.print_exc()
                    continue
                elif "message" not in u:
                    print(f"update of type {kinds}", flush=True)
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
                who = msg.get("from", {})
                print(f"/{cmd} from {who.get('first_name', '?')} {who.get('last_name', '')}".rstrip()
                      + f" (user id {ctx['user']}) in {msg['chat'].get('type')} chat", flush=True)
                if cmd in COMMANDS and not allowed(ctx["user"], cmd):
                    send(f"🔒 You don't have access to /{esc(cmd)}. /help shows yours.")
                    continue
                fn = COMMANDS.get(cmd)
                try:
                    reply = fn(arg.strip()) if fn else "Unknown command. /help"
                    if reply:
                        send(reply)
                except Exception as e:
                    traceback.print_exc()
                    send(f"⚠️ {esc(cmd)} failed: {esc(e)}")
        except Exception:
            traceback.print_exc()
            time.sleep(10)


if __name__ == "__main__":
    sys.exit(main())
