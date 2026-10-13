#!/usr/bin/env python3
"""arrbot: a small Telegram bot for the Arr stack. Standard library only.

Answers only the chats in TELEGRAM_CHAT_ID (comma-separated: your private chat
and, optionally, a family group). Read-only commands work for everyone in those
chats; actions (/restart, /clearstuck, /yes) only for TELEGRAM_ADMIN_IDS
(defaults to the private chat ID, which is your Telegram user ID).
Reads app API keys from the app config files mounted read-only at /appdata,
talks to Docker over its socket.

Commands: /menu (buttons for everything) /status /disk /vpn /downloads /stuck
          /recent /upcoming /leaving /watching /missing /random [movie|show]
          /search <name> /request <name> /pause /tdarr /restart [app] /clearstuck
Views have Refresh + Menu buttons; actions (/restart, /clearstuck, New VPN server)
ask for a Yes button (or /yes) within 60 seconds.

Background checks (every 15 min):
- Leaving soon: 2 days before Maintainerr deletes something, posts to every chat
  with a Keep button (excludes it in Maintainerr).
- Problems: fake releases (only an .exe inside) and downloads stuck 48 h+ are sent
  to the admins with a "Find another" button (remove + blocklist + search again).
- Nothing found: added a day+ ago but nothing on disk or downloading -> admins,
  with a "Search again" button.
- Ready to watch: new imports, grouped per show, to the group; a private copy to
  whoever requested it if they set their Telegram chat ID in Seerr.
- Tdarr: after the night's schedule (10:15), what was re-encoded -> admins.
Every minute: /pause auto-resume (paused torrents are listed in /data/paused.json).
Sent alerts are remembered in ARRBOT_STATE (default /data/state.json) so they
aren't repeated after a restart.
"""
import http.client
import json
import os
import random
import re
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHATS = {c.strip() for c in os.environ["TELEGRAM_CHAT_ID"].split(",") if c.strip()}
ADMINS = {a.strip() for a in (os.environ.get("TELEGRAM_ADMIN_IDS") or ",".join(c for c in CHATS if not c.startswith("-"))).split(",") if a.strip()}
ACTIONS = {"restart", "clearstuck", "yes", "fix", "vpnnew", "tdarr"}
# Non-admins get DEFAULT commands; TELEGRAM_USER_COMMANDS overrides per person:
#   "123456=status,search;789012=status,search,leaving,watching"
DEFAULT = {c.strip() for c in (os.environ.get("TELEGRAM_DEFAULT_COMMANDS") or
           "status,disk,vpn,downloads,stuck,recent,upcoming,leaving,search,request,keep,"
           "missing,pause,random").split(",") if c.strip()}
PER_USER = {}
for part in (os.environ.get("TELEGRAM_USER_COMMANDS") or "").split(";"):
    if "=" in part:
        uid, cmds = part.split("=", 1)
        PER_USER[uid.strip()] = {c.strip() for c in cmds.split(",") if c.strip()}


def allowed(user, cmd):
    if cmd in ("help", "start", "menu"):
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
PAUSE_FILE = os.path.join(os.path.dirname(STATE_FILE), "paused.json")
NOGRAB_HOURS = 24  # requested but nothing found or downloading after this long -> alert
TDARR_REPORT_AT = 10.25  # local hour (10:15), after the Tdarr schedule ends
QBIT_PORT = os.environ.get("QBIT_PORT", "8080")
MAINTAINERR = "http://maintainerr:6246/api"
SEERR = "http://seerr:5055/api/v1"
TDARR = "http://tdarr:8265/api/v2"
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


def docker(method, path, body=None):
    c = DockerConn()
    if body is None:
        c.request(method, path)
    else:
        c.request(method, path, json.dumps(body), {"Content-Type": "application/json"})
    r = c.getresponse()
    raw = r.read()
    if r.status >= 300:
        raise RuntimeError(f"docker {path}: {r.status}")
    if "logs" in path or path.startswith("/exec/"):  # multiplexed stream: strip 8-byte frame headers
        out, i = [], 0
        while i + 8 <= len(raw):
            n = int.from_bytes(raw[i + 4:i + 8], "big")
            out.append(raw[i + 8:i + 8 + n])
            i += 8 + n
        return b"".join(out).decode(errors="replace")
    return json.loads(raw) if raw.strip() else None


def docker_exec(container, cmd):
    """Run a command inside a container and return its output. qBittorrent and the gluetun
    control server only trust localhost, so the bot talks to them from inside qbittorrent."""
    ex = docker("POST", f"/containers/{container}/exec", {"Cmd": cmd, "AttachStdout": True, "AttachStderr": True})
    return docker("POST", f"/exec/{ex['Id']}/start", {"Detach": False, "Tty": False})


def qbit(path, data=None):
    cmd = ["curl", "-s", "-m", "20", f"http://localhost:{QBIT_PORT}/api/v2/{path}"]
    return docker_exec("qbittorrent", cmd + (["--data", data] if data is not None else []))


def arr_key(app):
    return re.search(r"<ApiKey>([^<]+)", open(f"{APPDATA}/{app}/config.xml").read()).group(1)


def arr(app, path, method="GET", body=None):
    port = {"sonarr": 8989, "radarr": 7878, "prowlarr": 9696}[app]
    ver = "v1" if app == "prowlarr" else "v3"
    return http_json(f"http://{app}:{port}/api/{ver}{path}", {"X-Api-Key": arr_key(app)}, method, body)


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
            body["reply_markup"] = kb(buttons)
        http_json(f"{TG}/sendMessage", body=body)


# ---------- remembered alerts ----------
state_lock = threading.Lock()


def load_state():
    try:
        return json.load(open(STATE_FILE))
    except (OSError, ValueError):
        return {}


state = load_state()


def save_state():
    cutoff = time.time() - 30 * 86400
    for k in [k for k, t in state.items() if not k.startswith("_") and t < cutoff]:
        del state[k]
    try:
        with open(STATE_FILE + ".tmp", "w") as f:
            json.dump(state, f)
        os.replace(STATE_FILE + ".tmp", STATE_FILE)
    except OSError as e:
        print(f"can't save {STATE_FILE} ({e}); alerts may repeat after a restart", flush=True)


def already_sent(key):
    """True if this alert went out before; otherwise records it and returns False."""
    with state_lock:
        if key in state:
            return True
        state[key] = time.time()
        save_state()
        return False


def remember(key, value):
    """Keep a value (cursor, counter) across restarts; '_' keys never expire."""
    with state_lock:
        state["_" + key] = value
        save_state()



def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def kb(buttons):
    return {"inline_keyboard": [[{"text": t[:60], "callback_data": d[:64]} for t, d in row] for row in buttons or []]}


def edit(chat, message_id, text, buttons=None):
    """Replace a message in place (used by Refresh and the confirm buttons)."""
    try:
        http_json(f"{TG}/editMessageText", body={"chat_id": chat, "message_id": message_id, "text": text,
                                                 "parse_mode": "HTML", "disable_web_page_preview": True,
                                                 "reply_markup": kb(buttons)})
    except urllib.error.HTTPError as e:
        if "not modified" not in e.read().decode(errors="replace"):
            raise


def bar(pct, width=10):
    full = round(max(0, min(100, pct)) / 100 * width)
    return "▰" * full + "▱" * (width - full)


def short_time(t):
    """Sonarr/Radarr timeleft ('1.02:03:04' or '00:12:34') -> '1d 2h' / '12m'."""
    m = re.match(r"(?:(\d+)\.)?(\d+):(\d+):(\d+)", t or "")
    if not m:
        return ""
    d, h, mi = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
    return f"{d}d {h}h" if d else f"{h}h {mi}m" if h else f"{mi}m"


# ---------- read-only commands ----------
def disk_lines(bars=False):
    out = []
    for name, path in DISKS.items():
        st = os.statvfs(path)
        total, free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
        pct = 100 * (total - free) / total
        dot = '🔴' if pct >= 90 else '🟡' if pct >= 80 else '🟢'
        if bars:
            out.append(f"{dot} <b>{name}</b>\n<code>{bar(pct, 16)}</code> {pct:.0f}%\n{gb(free)} free of {gb(total)}")
        else:
            out.append(f"{dot} {name}: {pct:.0f}% used, {gb(free)} free")
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
    items = queue_items()
    active = sum(1 for r in items if r.get("status") == "downloading")
    lines = ["📊 <b>Arr server</b>", "",
             f"{'🟢' if not bad else '🔴'} Apps: {len(cons) - len(bad)} running" + (
                 "\n   ❗ " + "\n   ❗ ".join(map(esc, bad)) if bad else ""),
             f"{'🟢' if ok else '🔴'} VPN: {esc(country or 'unknown')}" + (f", port {port}" if port else "")]
    lines += disk_lines()
    lines.append(f"⬇️ Downloads: {active} active, {len(items)} in queue")
    return "\n".join(lines)


def cmd_disk(_):
    return "💽 <b>Disk space</b>\n\n" + "\n\n".join(disk_lines(bars=True))


def cmd_vpn(_):
    country, port, ok = vpn_info()
    text = (f"🛡 <b>VPN</b>\n\n{'🟢 Connected' if ok else '🔴 NOT healthy (torrents paused by kill switch)'}\n"
            f"📍 {esc(country or 'unknown')}\n🔌 Forwarded port: {port or 'unknown'}")
    if not allowed(ctx["user"], "vpnnew") or not vpn_switchable():
        return text
    return text + "\n\nDownloads slow? Try a different server.", [[("🔀 New VPN server", "cmd:vpnnew")]]


def vpn_switchable():
    try:
        return "PUT /v1/vpn/status" in open(f"{APPDATA}/gluetun/auth/config.toml").read()
    except OSError:
        return False


def vpn_reconnect():
    """Stop + start the tunnel through gluetun's control server; it picks a new random server in
    the same countries. (Changing country means redeploying the stack.) Needs the routes allowed in
    gluetun's /gluetun/auth/config.toml; see README."""
    old = vpn_info()[0]
    since = int(time.time())
    for status in ("stopped", "running"):
        out = docker_exec("qbittorrent", ["curl", "-s", "-m", "60", "-X", "PUT", "-H", "Content-Type: application/json",
                                          "-d", json.dumps({"status": status}), "http://127.0.0.1:8000/v1/vpn/status"])
        if "outcome" not in out:
            raise RuntimeError(f"gluetun said: {out.strip()[:120] or 'nothing'}")
    for _ in range(30):
        time.sleep(3)
        logs = docker("GET", f"/containers/gluetun/logs?stdout=1&stderr=1&since={since}")
        new = re.findall(r"Public IP address is \S+ \(([^)]*?)(?: - source|\))", logs)
        if new:
            return f"🔀 New VPN server: <b>{esc(new[-1])}</b>" + (f"\n<i>was {esc(old)}</i>" if old else "")
    return "🔀 Reconnecting… check /vpn in a minute."


def cmd_vpnnew(_):
    return confirm("switch to a new VPN server (downloads pause for about a minute)", "ok:vpnnew", vpn_reconnect)


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
        return "⬇️ Nothing downloading right now."
    lines = [f"⬇️ <b>Downloading ({len(items)})</b>"]
    for r in sorted(items, key=lambda r: r["sizeleft"])[:15]:
        pct = 100 * (1 - r["sizeleft"] / max(r["size"], 1))
        eta = short_time(r.get("timeleft"))
        lines.append(f"\n{'📺' if r['_app'] == 'sonarr' else '🎬'} {esc(r['title'][:55])}\n"
                     f"<code>{bar(pct)}</code> {pct:.0f}%" + (f" · {eta} left" if eta else " · waiting for peers"))
    if len(items) > 15:
        lines.append(f"\n…and {len(items) - 15} more")
    return "\n".join(lines)


def stuck_items(hours=STUCK_HOURS):
    if paused():  # everything looks stuck while downloads are paused on purpose
        return []
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
        return f"🐢 No downloads stuck for more than {STUCK_HOURS} h. 👍"
    lines = [f"🐢 <b>Stuck &gt;{STUCK_HOURS} h ({len(items)})</b>", ""]
    lines += [f"• {esc(r['title'][:55])} <i>({ago(r['added'])})</i>" for r in items[:20]]
    if not allowed(ctx["user"], "fix"):
        return "\n".join(lines)
    buttons = [[(f"🔁 {r['title'][:45]}", f"fix:{r['_app']}:{r['id']}")] for r in items[:10]]
    buttons.append([("🧹 Find another for all", "cmd:clearstuck")])
    lines.append("\nTap one to remove it and find another release.")
    return "\n".join(lines), buttons


def cmd_recent(_):
    ev = []
    for app in ("sonarr", "radarr"):
        for r in arr(app, "/history?pageSize=15&sortKey=date&sortDirection=descending&eventType=3")["records"]:
            ev.append((r["date"], r["sourceTitle"], app))
    ev.sort(reverse=True)
    return "🆕 <b>Recently added</b>\n\n" + "\n".join(
        f"{'📺' if a == 'sonarr' else '🎬'} {esc(t[:55])} <i>({ago(d)})</i>" for d, t, a in ev[:10])


def cmd_upcoming(_):
    now = datetime.now(timezone.utc)
    start, end = now.strftime("%Y-%m-%d"), datetime.fromtimestamp(now.timestamp() + 7 * 86400).strftime("%Y-%m-%d")
    rows = []
    for e in arr("sonarr", f"/calendar?start={start}&end={end}&includeSeries=true"):
        if e.get("monitored") and e.get("airDateUtc"):
            rows.append((e["airDateUtc"], f"📺 {e['series']['title']} S{e['seasonNumber']:02}E{e['episodeNumber']:02}"
                         + (" ✅" if e.get("hasFile") else "")))
    for m in arr("radarr", f"/calendar?start={start}&end={end}"):
        for k in ("digitalRelease", "physicalRelease"):
            if m.get(k) and start <= m[k][:10] <= end:
                rows.append((m[k], f"🎬 {m['title']}" + (" ✅" if m.get("hasFile") else "")))
                break
    if not rows:
        return "📅 Nothing new due in the next 7 days."
    lines, day = ["📅 <b>Coming up this week</b>"], None
    for when, title in sorted(rows):
        d = datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone().strftime("%a %d %b")
        if d != day:
            day = d
            lines.append(f"\n<b>{d}</b>")
        lines.append(f"• {esc(title)}")
    return "\n".join(lines)


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
        return "🗑 Nothing scheduled for removal."
    tok = plex_token()
    lines = [f"🗑 <b>Leaving soon ({len(rows)})</b> — watch something to keep it"]
    day, buttons, seen = None, [], set()
    for due, pid, col in rows:
        when = datetime.fromtimestamp(due).strftime("%a %d %b")
        if when != day:
            day = when
            lines.append(f"\n<b>{when}</b>")
        title = plex_title(pid, tok)
        lines.append(f"• {esc(title)}")
        if due < time.time() + 7 * 86400 and pid not in seen and len(buttons) < 15:
            seen.add(pid)
            buttons.append([(f"💚 Keep {title}", f"keep:{pid}")])
    if not allowed(ctx["user"], "keep"):
        buttons = []
    return "\n".join(lines), buttons


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
        return "📺 Nobody is watching right now."
    lines = [f"📺 <b>Watching now ({s['size']})</b>"]
    for v in s.get("Metadata", []):
        title = v.get("grandparentTitle", "") + (" – " if v.get("grandparentTitle") else "") + v.get("title", "")
        user = v.get("User", {}).get("title", "?")
        ts = v.get("TranscodeSession")
        mode = "direct play" if not ts else ("GPU transcode" if ts.get("transcodeHwFullPipeline") or ts.get("transcodeHwEncoding") else "CPU transcode")
        pct = 100 * int(v.get("viewOffset", 0)) / max(int(v.get("duration", 1)), 1)
        lines.append(f"\n👤 <b>{esc(user)}</b> — {esc(title)}\n<code>{bar(pct)}</code> {pct:.0f}% · {mode}")
    return "\n".join(lines)


def cmd_search(arg):
    if not arg:
        return "🔎 Usage: /search &lt;name&gt;"
    q = arg.lower()
    out = []
    for s in arr("sonarr", "/series"):
        if q in s["title"].lower():
            st = s["statistics"]
            out.append(f"📺 <b>{esc(s['title'])}</b> ({s.get('year')})\n    {st['episodeFileCount']}/{st['totalEpisodeCount']} episodes · "
                       f"{'monitored' if s['monitored'] else 'not monitored'}")
    for m in arr("radarr", "/movie"):
        if q in m["title"].lower():
            out.append(f"🎬 <b>{esc(m['title'])}</b> ({m.get('year')})\n    {'✅ in library' if m['hasFile'] else '⏳ wanted' if m['monitored'] else 'not monitored'}")
    for r in queue_items():
        if q in r["title"].lower():
            out.append(f"⬇️ {esc(r['title'][:55])}\n    <code>{bar(100 * (1 - r['sizeleft'] / max(r['size'], 1)))}</code>")
    buttons = [[(f"📥 Request “{arg[:30]}”", f"rq:{arg[:50]}")]] if allowed(ctx["user"], "request") else []
    if not out:
        return f"🔎 Nothing matching “{esc(arg)}” in the library.", buttons
    return f"🔎 <b>{esc(arg)}</b>\n\n" + "\n".join(out[:20]), buttons


def seerr(path, method="GET", body=None):
    key = json.load(open(f"{APPDATA}/overseerr/settings.json"))["main"]["apiKey"]
    return http_json(f"{SEERR}{path}", {"X-Api-Key": key}, method, body)


SEERR_STATUS = {2: "⏳ requested", 3: "⬇️ on its way", 4: "🟡 partly in library", 5: "✅ in library"}


def cmd_request(arg):
    if not arg:
        return "📥 Usage: /request &lt;name&gt;"
    res = [r for r in seerr(f"/search?query={urllib.parse.quote(arg)}&page=1")["results"]
           if r.get("mediaType") in ("movie", "tv")][:6]
    if not res:
        return f"📥 Nothing found for “{esc(arg)}”."
    lines, buttons = [f"📥 <b>Results for “{esc(arg)}”</b> — tap one to request it", ""], []
    lib = {}
    for r in res:
        name = r.get("title") or r.get("name")
        year = (r.get("releaseDate") or r.get("firstAirDate") or "")[:4]
        label = f"{'🎬' if r['mediaType'] == 'movie' else '📺'} {name}" + (f" ({year})" if year else "")
        info = r.get("mediaInfo") or {}
        status = SEERR_STATUS.get(info.get("status"))
        lines.append(f"• {esc(label)}" + (f" — {status}" if status else ""))
        if not status:
            buttons.append([(label, f"req:{r['mediaType']}:{r['id']}")])
        elif info.get("status") in (2, 3, 4):
            try:
                progress, search = arr_progress(r["mediaType"], info, lib)
            except Exception:
                traceback.print_exc()
                continue
            lines.append(f"   <i>{progress}</i>")
            if search:
                buttons.append([(f"🔎 Search now: {name}", search)])
    if not buttons:
        lines.append("\nEverything here is already requested or in the library.")
    return "\n".join(lines), buttons


def arr_progress(kind, info, lib):
    """Where a requested show/movie is at in Sonarr/Radarr: (text, search button data or None)."""
    app = "sonarr" if kind == "tv" else "radarr"
    if app not in lib:
        lib[app] = arr(app, "/series" if kind == "tv" else "/movie")
        lib["queue"] = lib.get("queue") or queue_items()
    if kind == "tv":
        item = next((s for s in lib[app] if s.get("tvdbId") and s["tvdbId"] == info.get("tvdbId")), None)
    else:
        item = next((m for m in lib[app] if m.get("tmdbId") == info.get("tmdbId")), None)
    if not item:
        return "not in Sonarr/Radarr yet", None
    queued = [q for q in lib["queue"] if q["_app"] == app and q.get("seriesId" if kind == "tv" else "movieId") == item["id"]]
    search = f"srch:{app[0]}:{item['id']}"
    if kind == "tv":
        st = item.get("statistics", {})
        have, aired = st.get("episodeFileCount", 0), st.get("episodeCount", 0)
        text = f"{have}/{aired} aired episodes" if aired else "nothing has aired yet"
    else:
        text = "in library" if item.get("hasFile") else "not released yet" if not item.get("isAvailable") else "not found yet"
    if queued:
        left = sum(q["sizeleft"] for q in queued) / max(sum(q["size"] for q in queued), 1)
        return text + f" · ⬇️ {len(queued)} downloading, {100 * (1 - left):.0f}%", None
    return text, search if (kind == "tv" and aired and have < aired) or (kind == "movie" and item.get("isAvailable") and not item.get("hasFile")) else None


def search_again(app, item_id):
    if app in ("s", "sonarr"):
        arr("sonarr", "/command", "POST", {"name": "SeriesSearch", "seriesId": int(item_id)})
        return arr("sonarr", f"/series/{int(item_id)}")["title"]
    arr("radarr", "/command", "POST", {"name": "MoviesSearch", "movieIds": [int(item_id)]})
    return arr("radarr", f"/movie/{int(item_id)}")["title"]


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


def cmd_missing(_):
    series = {s["id"]: s for s in arr("sonarr", "/series")}
    queued = {(q["_app"], q.get("seriesId") or q.get("movieId")) for q in queue_items()}
    shows = {}
    for e in arr("sonarr", "/wanted/missing?pageSize=5000&monitored=true")["records"]:
        s = shows.setdefault(e["seriesId"], {"n": 0, "seasons": set(), "last": ""})
        s["n"] += 1
        s["seasons"].add(e["seasonNumber"])
        s["last"] = max(s["last"], e.get("airDateUtc") or "")
    movies = [m for m in arr("radarr", "/wanted/missing?pageSize=500&monitored=true")["records"] if m.get("isAvailable")]
    if not shows and not movies:
        return "🔍 Nothing missing. 👍"
    lines, buttons = [f"🔍 <b>Missing</b> — aired but not in the library", ""], []
    for sid, s in sorted(shows.items(), key=lambda kv: kv[1]["last"], reverse=True)[:25]:
        title = series.get(sid, {}).get("title", f"series {sid}")
        seasons = sorted(s["seasons"])
        span = f"S{seasons[0]:02}" + (f"–S{seasons[-1]:02}" if len(seasons) > 1 else "")
        busy = ("sonarr", sid) in queued
        lines.append(f"📺 {esc(title)} — {s['n']} ep ({span})" + (" · ⬇️" if busy else ""))
        if not busy and len(buttons) < 10:
            buttons.append([(f"🔎 {title}", f"srch:s:{sid}")])
    if len(shows) > 25:
        lines.append(f"…and {len(shows) - 25} more shows")
    if movies:
        lines.append("")
    for m in movies[:15]:
        busy = ("radarr", m["id"]) in queued
        lines.append(f"🎬 {esc(m['title'])} ({m.get('year')})" + (" · ⬇️" if busy else ""))
        if not busy and len(buttons) < 15:
            buttons.append([(f"🔎 {m['title']}", f"srch:r:{m['id']}")])
    if buttons:
        lines.append("\n⬇️ = downloading. Tap one to search again.")
    return "\n".join(lines), buttons


# ---------- pause downloads ----------
def paused():
    try:
        return json.load(open(PAUSE_FILE))
    except (OSError, ValueError):
        return None


def qbit_torrents():
    return json.loads(qbit("torrents/info") or "[]")


def qbit_stop(hashes):
    if hashes and "Not Found" in qbit("torrents/stop", "hashes=" + "|".join(hashes)):
        qbit("torrents/pause", "hashes=" + "|".join(hashes))  # qBittorrent 4.x


def pause_downloads(hours):
    """Stop every running torrent and remember which, so resume only restarts those."""
    p = paused() or {"hashes": []}
    running = [t["hash"] for t in qbit_torrents() if not t["state"].startswith(("stopped", "paused"))]
    qbit_stop(running)
    p["hashes"] = sorted(set(p["hashes"]) | set(running))
    p["until"] = time.time() + hours * 3600 if hours else 0
    with open(PAUSE_FILE, "w") as f:
        json.dump(p, f)


def resume_downloads():
    p = paused()
    if p and p["hashes"] and "Not Found" in qbit("torrents/start", "hashes=" + "|".join(p["hashes"])):
        qbit("torrents/resume", "hashes=" + "|".join(p["hashes"]))
    try:
        os.remove(PAUSE_FILE)
    except OSError:
        pass


def cmd_pause(_):
    p = paused()
    if p:
        until = datetime.fromtimestamp(p["until"]).strftime("%H:%M") if p.get("until") else None
        return (f"⏸ <b>Downloads paused</b> ({len(p['hashes'])} torrents)\n"
                + (f"Back on at {until}." if until else "Until someone taps Resume."),
                [[("▶️ Resume now", "pz:r"), ("⏸ +1 hour", "pz:more")]])
    info = json.loads(qbit("transfer/info") or "{}")
    return (f"▶️ <b>Downloads running</b>\n⬇️ {info.get('dl_info_speed', 0) / 2**20:.1f} MB/s · "
            f"⬆️ {info.get('up_info_speed', 0) / 2**20:.1f} MB/s\n\nNeed the internet for something else?",
            [[("⏸ 1 hour", "pz:1"), ("⏸ 3 hours", "pz:3"), ("⏸ Until I resume", "pz:0")]])


def pause_button(arg):
    if arg == "r":
        resume_downloads()
        return "▶️ Downloads resumed"
    if arg == "more":
        p = paused()
        if not p:
            pause_downloads(1)
        elif p.get("until"):
            p["until"] = max(p["until"], time.time()) + 3600
            with open(PAUSE_FILE, "w") as f:
                json.dump(p, f)
        return "⏸ Paused for another hour"
    pause_downloads(int(arg))
    return "⏸ Downloads paused"


# ---------- fun ----------
def cmd_random(arg):
    kind = {"movie": "movie", "movies": "movie", "show": "show", "shows": "show", "tv": "show"}.get(
        (arg or "").strip().lower()) or random.choice(["movie", "show"])
    tok = {"X-Plex-Token": plex_token()}
    items = []
    for d in http_json(f"{PLEX}/library/sections", tok)["MediaContainer"]["Directory"]:
        if d["type"] == kind:
            items += http_json(f"{PLEX}/library/sections/{d['key']}/all?type={1 if kind == 'movie' else 2}&unwatched=1",
                               tok, timeout=60)["MediaContainer"].get("Metadata", [])
    buttons = [[("🎲 Another", f"rnd:{kind}"), ("🎬 Movie", "rnd:movie"), ("📺 Show", "rnd:show")]]
    if not items:
        return f"🎲 You've watched every {kind}!", buttons
    m = random.choice(items)
    genres = ", ".join(g["tag"] for g in m.get("Genre", [])[:3])
    if kind == "movie":
        extra = f"{int(m.get('duration', 0)) // 60000} min"
    else:
        extra = f"{m.get('childCount', '?')} season(s) · {int(m.get('leafCount', 0)) - int(m.get('viewedLeafCount', 0))} unwatched episodes"
    rating = m.get("audienceRating") or m.get("rating")
    summary = (m.get("summary") or "")[:350] + ("…" if len(m.get("summary") or "") > 350 else "")
    year = f" ({m['year']})" if m.get("year") and f"({m['year']})" not in m["title"] else ""
    return (f"🎲 <b>{'🎬' if kind == 'movie' else '📺'} {esc(m['title'])}</b>{year}"
            + f"\n{extra}" + (f" · ⭐ {rating}" if rating else "") + (f"\n<i>{esc(genres)}</i>" if genres else "")
            + (f"\n\n{esc(summary)}" if summary else "") + f"\n\n<i>One of {len(items)} unwatched.</i>"), buttons


# ---------- Tdarr ----------
def tdarr(collection, mode="getAll", doc=None):
    return http_json(f"{TDARR}/cruddb", body={"data": {"collection": collection, "mode": mode,
                                                       **({"docID": doc} if doc else {})}}, method="POST", timeout=60)


def tdarr_numbers():
    stats = tdarr("StatisticsJSONDB", "getById", "statistics") or {}
    staged = tdarr("StagedJSONDB") or []
    old = sum((s.get("originalLibraryFile") or {}).get("file_size", 0) for s in staged) * 2**20
    new = sum((s.get("lastCompletedCacheFile") or {}).get("file_size", 0) for s in staged) * 2**20
    return int(stats.get("totalTranscodeCount") or 0), float(stats.get("sizeDiff") or 0), len(staged), old, new


def tdarr_text(done_today=None):
    count, saved_gb, n_staged, old, new = tdarr_numbers()
    lines = ["🎞 <b>Tdarr</b>", ""]
    if done_today is not None:
        lines.append(f"Last night: {done_today} file(s) shrunk")
    lines.append(f"All time: {count} encoded" + (f", {saved_gb:,.0f} GB saved" if saved_gb > 0 else ""))
    if n_staged:
        lines.append(f"\n⏳ <b>{n_staged} waiting for approval</b>\n{gb(old)} → {gb(new)} "
                     f"(saves {gb(old - new)}, {100 * (old - new) / max(old, 1):.0f}%)\nApprove them in Tdarr → Staging.")
    return "\n".join(lines)


def cmd_tdarr(_):
    return tdarr_text()


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
        lines = ["🗑 <b>Leaving soon</b> — watch it, or tap Keep to save it", ""]
        buttons = []
        for due, pid in batch:
            t = plex_title(pid, tok)
            lines.append(f"• {esc(t)} — {datetime.fromtimestamp(due).strftime('%a %d %b')}")
            buttons.append([(f"💚 Keep {t}", f"keep:{pid}")])
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
    lines = ["⚠️ <b>Download problems</b>", ""]
    lines += [f"• {kind}: {esc(r['title'][:60])}" for kind, r in found]
    buttons = [[(f"🔁 Find another: {r['title'][:40]}", f"fix:{r['_app']}:{r['id']}")] for _, r in found[:25]]
    for admin in ADMINS:
        send("\n".join(lines), admin, buttons)


def hours_since(iso):
    return (datetime.now(timezone.utc) - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds() / 3600


def check_nograb():
    """Added 1-30 days ago, still nothing on disk and nothing downloading: the search is finding nothing."""
    queued = {(q["_app"], q.get("seriesId") or q.get("movieId")) for q in queue_items()}
    found = []
    for s in arr("sonarr", "/series"):
        st = s.get("statistics", {})
        if (s["monitored"] and st.get("episodeCount", 0) and not st.get("episodeFileCount")
                and NOGRAB_HOURS <= hours_since(s["added"]) <= 30 * 24 and ("sonarr", s["id"]) not in queued):
            why = " — set to <b>Daily</b> type, so it searches by date; change it to Standard in Sonarr" \
                if s.get("seriesType") == "daily" else ""
            found.append((f"📺 {esc(s['title'])}{why}", f"srch:s:{s['id']}", s["title"]))
    for m in arr("radarr", "/movie"):
        if (m["monitored"] and not m.get("hasFile") and m.get("isAvailable")
                and NOGRAB_HOURS <= hours_since(m["added"]) <= 30 * 24 and ("radarr", m["id"]) not in queued):
            found.append((f"🎬 {esc(m['title'])} ({m.get('year')})", f"srch:r:{m['id']}", m["title"]))
    found = [f for f in found if not already_sent(f"nograb:{f[1]}")]
    if not found:
        return
    lines = ["🔍 <b>Requested, but nothing found yet</b>", ""] + [f"• {line}" for line, _, _ in found]
    buttons = [[(f"🔎 Search again: {title[:40]}", data)] for _, data, title in found[:25]]
    for admin in ADMINS:
        send("\n".join(lines), admin, buttons)


def episode_span(eps):
    """{(1,1),(1,2),(1,3),(2,5)} -> 'S01E01–E03, S02E05'; big batches become 'S02 (22 episodes)'."""
    out = []
    for season in sorted({s for s, _ in eps}):
        nums = sorted(e for s, e in eps if s == season)
        if len(nums) > 6:
            out.append(f"S{season:02} ({len(nums)} episodes)")
        elif nums == list(range(nums[0], nums[-1] + 1)) and len(nums) > 1:
            out.append(f"S{season:02}E{nums[0]:02}–E{nums[-1]:02}")
        else:
            out.append(", ".join(f"S{season:02}E{n:02}" for n in nums))
    return ", ".join(out)


def requester_chats():
    """('tv', tvdbId) / ('movie', tmdbId) -> Telegram chat IDs of the Seerr users who requested it
    (only users who put their Telegram chat ID in Seerr → Profile → Notifications → Telegram)."""
    out, chat_of = {}, {}
    for r in seerr("/request?take=200&filter=all&sort=added")["results"]:
        uid = (r.get("requestedBy") or {}).get("id")
        if uid not in chat_of:
            try:
                chat_of[uid] = str(seerr(f"/user/{uid}/settings/notifications").get("telegramChatId") or "")
            except Exception:
                chat_of[uid] = ""
        media = r.get("media") or {}
        key = ("tv", media.get("tvdbId")) if r.get("type") == "tv" else ("movie", media.get("tmdbId"))
        if chat_of[uid]:
            out.setdefault(key, set()).add(chat_of[uid])
    return out


def check_ready():
    """New imports since the last check, one line per show/movie, to the group (and the requester)."""
    since = state.get("_ready_since")
    now = time.time()
    if not since:  # first run: start from now, don't replay history
        return remember("ready_since", now)
    iso = datetime.fromtimestamp(since, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    shows, movies = {}, {}
    for r in arr("sonarr", f"/history/since?date={iso}&eventType=3&includeSeries=true&includeEpisode=true"):
        s, e = r.get("series") or {}, r.get("episode") or {}
        shows.setdefault(s.get("id"), {"title": s.get("title", "?"), "tvdb": s.get("tvdbId"), "eps": set()})["eps"].add(
            (e.get("seasonNumber", 0), e.get("episodeNumber", 0)))
    for r in arr("radarr", f"/history/since?date={iso}&eventType=3&includeMovie=true"):
        m = r.get("movie") or {}
        movies[m.get("id")] = m
    remember("ready_since", now)
    if not shows and not movies:
        return
    rows = [(("tv", s["tvdb"]), f"📺 <b>{esc(s['title'])}</b> — {episode_span(s['eps'])}") for s in shows.values()]
    rows += [(("movie", m.get("tmdbId")), f"🎬 <b>{esc(m.get('title', '?'))}</b>" + (f" ({m['year']})" if m.get("year") else ""))
             for m in movies.values()]
    targets = [c for c in CHATS if c.startswith("-")] or list(CHATS)  # groups if any, else private chats
    for chat in targets:
        send("🍿 <b>Ready to watch</b>\n\n" + "\n".join(line for _, line in rows), chat)
    try:
        who = requester_chats()
    except Exception:
        traceback.print_exc()
        return
    personal = {}
    for key, line in rows:
        for chat in who.get(key, ()):
            if chat not in targets:
                personal.setdefault(chat, []).append(line)
    for chat, lines in personal.items():
        try:
            send("🍿 <b>Your request is ready</b>\n\n" + "\n".join(lines), chat)
        except Exception:  # they never opened a chat with the bot
            traceback.print_exc()


def check_tdarr():
    """Once a day after the Tdarr schedule ends: what it did overnight. Silent if nothing happened."""
    now = datetime.now()
    if now.hour + now.minute / 60 < TDARR_REPORT_AT or state.get("_tdarr_day") == now.strftime("%Y-%m-%d"):
        return
    try:
        count = tdarr_numbers()[0]
    except (urllib.error.URLError, OSError):  # Tdarr not installed / not running
        return
    last = state.get("_tdarr_count")
    remember("tdarr_day", now.strftime("%Y-%m-%d"))
    remember("tdarr_count", count)
    if last is None or count <= last:
        return
    for admin in ADMINS:
        send(tdarr_text(count - last), admin, [[("🎞 Tdarr", "ref:tdarr")]])


def check_pause():
    p = paused()
    if not p:
        return
    if p.get("until") and time.time() >= p["until"]:
        resume_downloads()
        for admin in ADMINS:
            send("▶️ Downloads resumed (pause time is up).", admin)
        return
    # Sonarr/Radarr may have added new downloads since; keep those stopped too.
    if any(not t["state"].startswith(("stopped", "paused")) for t in qbit_torrents()):
        pause_downloads((p["until"] - time.time()) / 3600 if p.get("until") else 0)


def watcher():
    time.sleep(60)
    last = 0
    while True:
        checks = [check_pause]
        if time.time() - last >= CHECK_EVERY:
            last = time.time()
            checks += [check_leaving, check_problems, check_nograb, check_ready, check_tdarr]
        for check in checks:
            try:
                check()
            except Exception:
                traceback.print_exc()
        time.sleep(60)


# ---------- actions (confirm with a button, or /yes) ----------
def confirm(desc, data, do):
    pending[(ctx["chat"], ctx["user"])] = (time.time() + 60, desc, do)
    return f"⚠️ {desc[0].upper() + desc[1:]}?", [[("✅ Yes", data), ("✖️ Cancel", "cancel")]]


def restart_app(name):
    docker("POST", f"/containers/{name}/restart?t=30")
    if name == "gluetun":  # qBittorrent lives in gluetun's network; it loses it on a gluetun restart
        docker("POST", "/containers/qbittorrent/restart?t=30")
        return "🔄 <b>gluetun</b> and <b>qbittorrent</b> restarted."
    return f"🔄 <b>{name}</b> restarted."


def cmd_restart(arg):
    name = (arg or "").strip().lower()
    if name not in RESTARTABLE:
        apps = sorted(RESTARTABLE)
        return "🔄 <b>Restart which app?</b>", [[(a, f"rs:{a}") for a in apps[i:i + 3]] for i in range(0, len(apps), 3)]
    return confirm(f"restart {name}", f"ok:restart:{name}", lambda: restart_app(name))


def clear_stuck():
    items = stuck_items()
    for app in ("sonarr", "radarr"):
        ids = [r["id"] for r in items if r["_app"] == app]
        if ids:
            arr_delete_bulk(app, ids)
    return f"🧹 Removed + blocklisted {len(items)} stuck download(s); searching for other releases."


def cmd_clearstuck(_):
    n = len(stuck_items())
    if not n:
        return "🐢 Nothing stuck. 👍"
    return confirm(f"remove + blocklist {n} stuck download(s) and search again", "ok:clearstuck", clear_stuck)


def cmd_yes(_):
    exp, desc, fn = pending.pop((ctx["chat"], ctx["user"]), (0, None, None))
    if not fn or time.time() > exp:
        return "Nothing to confirm (or it expired)."
    return fn()


# ---------- menu ----------
MENU = [("status", "📊 Status"), ("downloads", "⬇️ Downloads"), ("upcoming", "📅 This week"),
        ("recent", "🆕 Recent"), ("leaving", "🗑 Leaving"), ("stuck", "🐢 Stuck"),
        ("missing", "🔍 Missing"), ("pause", "⏸ Pause"), ("random", "🎲 Random"),
        ("disk", "💽 Disk"), ("vpn", "🛡 VPN"), ("watching", "📺 Watching"),
        ("tdarr", "🎞 Tdarr"), ("restart", "🔄 Restart"), ("clearstuck", "🧹 Clear stuck")]
REFRESHABLE = {"status", "disk", "vpn", "downloads", "stuck", "recent", "leaving", "watching", "upcoming",
               "missing", "pause", "tdarr"}


def cmd_menu(_):
    items = [(label, f"cmd:{c}") for c, label in MENU if allowed(ctx["user"], c)]
    text = "🏠 <b>Arr server</b> — what do you want to see?"
    extra = [f"/{c} &lt;name&gt;" for c in ("request", "search") if allowed(ctx["user"], c)]
    if extra:
        text += "\n\nOr type " + " · ".join(extra)
    return text, [items[i:i + 3] for i in range(0, len(items), 3)]


def run(cmd, arg=""):
    """Run a command; returns (text, buttons). Read-only views get Refresh + Menu buttons."""
    out = COMMANDS[cmd](arg)
    text, buttons = out if isinstance(out, tuple) else (out, None)
    if cmd in REFRESHABLE:
        buttons = (buttons or []) + [[("🔄 Refresh", f"ref:{cmd}"), ("🏠 Menu", "cmd:menu")]]
    return text, buttons


COMMANDS = {"status": cmd_status, "disk": cmd_disk, "vpn": cmd_vpn, "downloads": cmd_downloads, "stuck": cmd_stuck,
            "recent": cmd_recent, "upcoming": cmd_upcoming, "leaving": cmd_leaving, "watching": cmd_watching,
            "search": cmd_search, "request": cmd_request, "restart": cmd_restart, "clearstuck": cmd_clearstuck,
            "missing": cmd_missing, "pause": cmd_pause, "random": cmd_random, "tdarr": cmd_tdarr,
            "vpnnew": cmd_vpnnew, "yes": cmd_yes, "menu": cmd_menu, "help": cmd_menu, "start": cmd_menu}
BOT_COMMANDS = [("menu", "Buttons for everything"), ("status", "Everything at a glance"),
                ("downloads", "What's downloading"), ("upcoming", "New episodes/movies this week"),
                ("recent", "Last 10 added"), ("leaving", "What Maintainerr removes next"),
                ("request", "Ask for a show or movie"), ("search", "Is it in the library?"),
                ("missing", "Aired but not downloaded"), ("pause", "Pause downloads for a while"),
                ("random", "Pick something unwatched"), ("tdarr", "Space saved by re-encoding"),
                ("stuck", "Downloads stuck >24h"), ("disk", "Disk space"), ("vpn", "VPN location and port"),
                ("watching", "Who's streaming"), ("restart", "Restart one app"), ("clearstuck", "Clear stuck downloads")]


# ---------- buttons ----------
BUTTON_CMD = {"keep": "keep", "req": "request", "rq": "request", "fix": "fix", "rs": "restart",
              "srch": "missing", "pz": "pause", "rnd": "random"}


def on_button(cq):
    user = str(cq["from"]["id"])
    who = cq["from"].get("first_name", "someone")
    msg = cq.get("message") or {}
    chat = str(msg.get("chat", {}).get("id"))
    mid = msg.get("message_id")
    data = cq.get("data", "")
    kind, _, rest = data.partition(":")
    args = rest.split(":") if rest else []
    print(f"button {data} from {who} (user id {user})", flush=True)

    def toast(text=""):
        http_json(f"{TG}/answerCallbackQuery", body={"callback_query_id": cq["id"], "text": text})
    if chat not in CHATS:
        return toast("Not available here.")
    ctx["chat"], ctx["user"] = chat, user
    if kind == "cancel":
        pending.pop((chat, user), None)
        toast()
        return edit(chat, mid, "✖️ Cancelled.")
    if kind in ("cmd", "ref"):
        cmd = rest
    elif kind == "ok":
        cmd = args[0]
    else:
        cmd = BUTTON_CMD.get(kind)
    if cmd not in COMMANDS and cmd not in ("keep", "fix"):
        return toast("Unknown button.")
    if not allowed(user, cmd) and not (kind == "srch" and allowed(user, "request")):
        return toast("🔒 You don't have access to that.")
    try:
        if kind == "cmd":  # open a view as a new message
            toast()
            text, buttons = run(cmd)
            return send(text, chat, buttons)
        if kind in ("ref", "rs"):  # update this message in place
            toast("Updated" if kind == "ref" else "")
            text, buttons = run(cmd, args[0] if kind == "rs" else "")
            return edit(chat, mid, text, buttons) if len(text) <= 3900 else send(text, chat, buttons)
        if kind == "rq":
            toast()
            text, buttons = run("request", rest)
            return send(text, chat, buttons)
        if kind == "rnd":
            toast()
            text, buttons = run("random", args[0])
            return edit(chat, mid, text, buttons)
        if kind == "pz":
            toast(pause_button(args[0]))
            text, buttons = run("pause")
            return edit(chat, mid, f"{text}\n<i>last change by {esc(who)}</i>", buttons)
        if kind == "ok":
            pending.pop((chat, user), None)
            toast("On it…")  # answer now: Telegram drops callback answers after ~15 s
            edit(chat, mid, "⏳ On it…")
            do = {"restart": lambda: restart_app(args[1]), "clearstuck": clear_stuck, "vpnnew": vpn_reconnect}[cmd]
            try:
                result = do()
            except Exception as e:
                traceback.print_exc()
                result = f"⚠️ Failed: {esc(e)}"
            return edit(chat, mid, f"{result}\n<i>by {esc(who)}</i>")
        if cmd == "keep":
            keep(args[0])
            done = f"💚 <b>{esc(plex_title(args[0], plex_token()))}</b> will be kept <i>(by {esc(who)})</i>."
        elif cmd == "request":
            done = f"📥 Requested <b>{esc(do_request(args[0], args[1]))}</b> <i>(by {esc(who)})</i>."
        elif kind == "srch":
            done = f"🔎 Searching again for <b>{esc(search_again(args[0], args[1]))}</b> <i>(by {esc(who)})</i>."
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
    http_json(f"{TG}/editMessageReplyMarkup", body={"chat_id": chat, "message_id": mid,
                                                    "reply_markup": {"inline_keyboard": [r for r in rows if r]}})
    send(done, chat)


def main():
    http_json(f"{TG}/setMyCommands", body={"commands": [{"command": c, "description": d} for c, d in BOT_COMMANDS]})
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
                if cmd not in COMMANDS:
                    send("Unknown command. /menu")
                    continue
                if not allowed(ctx["user"], cmd):
                    send(f"🔒 You don't have access to /{esc(cmd)}. /menu shows yours.")
                    continue
                try:
                    reply, buttons = run(cmd, arg.strip())
                    if reply:
                        send(reply, buttons=buttons)
                except Exception as e:
                    traceback.print_exc()
                    send(f"⚠️ {esc(cmd)} failed: {esc(e)}")
        except Exception:
            traceback.print_exc()
            time.sleep(10)


if __name__ == "__main__":
    sys.exit(main())
