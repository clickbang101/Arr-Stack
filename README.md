# ARR Stack — Automated Media Server

<p align="center">
  <a href="https://github.com/clickbang101/arr-stack">
    <img src="https://img.shields.io/badge/Repo-ARR%20Stack-blue?style=for-the-badge" />
  </a>
  <a href="https://github.com/clickbang101/arr-stack/blob/main/LICENSE">
    <img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" />
  </a>
  <img src="https://img.shields.io/badge/Docker-Ready-blue?style=for-the-badge" />
  <img src="https://img.shields.io/badge/Maintained-Yes-brightgreen?style=for-the-badge" />
  <a href="https://github.com/clickbang101/arr-stack/stargazers">
    <img src="https://img.shields.io/github/stars/clickbang101/arr-stack?style=for-the-badge" />
  </a>
</p>

A one-command setup for a self-hosted media server. Automatically finds, downloads, organizes, and streams your TV shows, movies, and books.

**What you get:** Plex · Sonarr · Radarr · qBittorrent · Prowlarr · Bazarr · Seerr · Jackett · FlareSolverr · Unpackerr · Tautulli · Maintainerr · Homarr · Nginx Proxy Manager

---

## Setup

Choose your method:

- [Command line (recommended)](#command-line-setup)
- [Portainer](#portainer-setup)

---

## Command line setup

**1. Install Docker**

If you don't have Docker yet:

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Log out and back in after this
```

**2. Clone and run the setup script**

```bash
git clone https://github.com/clickbang101/arr-stack.git
cd arr-stack
./setup.sh
```

The script will ask where you want to store your media and downloads, then set everything up for you automatically — directories, permissions, config. It will offer to start the stack when done.

**3. Open your services**

After about 60 seconds:

| Service | Address | What it does |
|---------|---------|--------------|
| Plex | http://localhost:32400/web | Watch your media |
| Seerr | http://localhost:5055 | Request new movies & shows (Overseerr fork) |
| Sonarr | http://localhost:8989 | Manages TV shows |
| Radarr | http://localhost:7878 | Manages movies |
| qBittorrent | http://localhost:8081 | Downloads torrents |
| Prowlarr | http://localhost:9696 | Finds torrents on trackers |
| Bazarr | http://localhost:6767 | Downloads subtitles |
| Jackett | http://localhost:9117 | Backup indexer proxy |
| FlareSolverr | http://localhost:8191 | Cloudflare bypass (used by Jackett/Prowlarr) |
| Tautulli | http://localhost:8181 | Plex watch stats & monitoring |
| Maintainerr | http://localhost:6246 | Automated library cleanup rules |
| Homarr | http://localhost:7575 | Dashboard for the whole stack |
| Nginx Proxy Manager | http://localhost:81 | Reverse proxy admin UI |
| Unpackerr | — (no UI) | Auto-extracts compressed downloads |

---

## Portainer setup

If you manage Docker through [Portainer](https://www.portainer.io/), use this flow instead of the command line.

**Step 1 — Prepare directories on the host**

SSH into your server and run:

```bash
git clone https://github.com/clickbang101/arr-stack.git
cd arr-stack
./setup.sh --portainer
```

This creates the required directories and sets permissions, then prints all the environment variable values you'll need in Portainer. It does **not** start the stack — Portainer handles that.

**Step 2 — Create a Stack in Portainer**

1. Open Portainer → **Stacks → Add Stack**
2. Give it a name (e.g. `arr-stack`)
3. Choose one of:
   - **Repository** — paste your repo URL and set the Compose path to `docker-compose.yml`
   - **Web editor** — paste the contents of `docker-compose.yml` directly

**Step 3 — Add environment variables**

Scroll down to **Environment variables** and add each of these (use the values printed by `setup.sh --portainer`):

| Variable | Example value |
|----------|--------------|
| `PUID` | `1000` |
| `PGID` | `1000` |
| `TZ` | `Africa/Johannesburg` |
| `MEDIA_PATH` | `/data/media` |
| `DOWNLOADS_PATH` | `/data/downloads` |
| `APPDATA_PATH` | `/data/appdata` |
| `RESTART_POLICY` | `unless-stopped` |
| `PLEX_VERSION` | `docker` |
| `QBITTORRENT_PORT` | `8081` |
| `SONARR_PORT` | `8989` |
| `RADARR_PORT` | `7878` |
| `PROWLARR_PORT` | `9696` |
| `BAZARR_PORT` | `6767` |
| `SEERR_PORT` | `5055` |
| `JACKETT_PORT` | `9117` |
| `FLARESOLVERR_PORT` | `8191` |
| `LAZYLIBRARIAN_PORT` | `8299` |
| `TAUTULLI_PORT` | `8181` |
| `MAINTAINERR_PORT` | `6246` |
| `HOMARR_PORT` | `7575` |
| `NPM_HTTP_PORT` | `80` |
| `NPM_HTTPS_PORT` | `443` |
| `NPM_ADMIN_PORT` | `81` |
| `HOMARR_SECRET_ENCRYPTION_KEY` | output of `openssl rand -hex 32` |
| `SONARR_API_KEY` | from Sonarr → Settings → General (fill in after first boot) |
| `RADARR_API_KEY` | from Radarr → Settings → General (fill in after first boot) |

> Tip: Portainer also accepts an `.env` file upload — click **Load variables from .env file** and upload your `.env.local`.

**Step 4 — Deploy**

Click **Deploy the stack**. Portainer will pull images and start everything. Watch progress under **Containers**.

**Managing the stack in Portainer**

| Task | Where |
|------|-------|
| Start / stop / restart | Stacks → your stack → Editor |
| View logs | Containers → container name → Logs |
| Open a terminal | Containers → container name → Console |
| Update images | Stacks → your stack → pull and redeploy |
| Optional extras | Add `--profile extras` isn't available in Portainer UI — SSH in and run `make extras` |

> **Optional extras (LazyLibrarian):** Portainer doesn't support Compose profiles through its UI. To start it, SSH into the host and run `make extras` from the repo directory.

---

## First-time wiring

After all services are up, you need to connect them together once. Do this in order:

### Step 1 — Change the qBittorrent password

Open qBittorrent → Tools → Options → Web UI → change the password from `adminadmin` to something secure.

### Step 2 — Add indexers in Prowlarr

Open Prowlarr → Indexers → Add Indexer → search for and add the torrent sites you use.

### Step 3 — Connect Prowlarr to Sonarr and Radarr

You need the API keys from each app. Find them at:
- Sonarr: Settings → General → API Key
- Radarr: Settings → General → API Key

Then in Prowlarr → Settings → Apps → Add Application → add both Sonarr and Radarr using those keys. Prowlarr will automatically push your indexers into both apps.

### Step 4 — Connect Sonarr to qBittorrent

Sonarr → Settings → Download Clients → Add → qBittorrent

| Field | Value |
|-------|-------|
| Host | `qbittorrent` |
| Port | `8081` |
| Username | `admin` |
| Password | *(the one you just set)* |

Then: Settings → Media Management → Root Folders → add `/media`

Repeat for Radarr.

### Step 5 — Set up Bazarr (subtitles)

Bazarr → Settings → Sonarr → URL: `http://sonarr:8989` + Sonarr API key  
Bazarr → Settings → Radarr → URL: `http://radarr:7878` + Radarr API key  
Bazarr → Settings → Languages → pick your preferred subtitle languages  
Bazarr → Settings → Providers → add subtitle sources (OpenSubtitles is a good start)

### Step 6 — Set up Seerr

Open Seerr → follow the setup wizard → sign in with your Plex account → connect Sonarr and Radarr. Once done, you (and anyone you share the link with) can request movies and shows through Seerr and they'll download automatically.

### Step 7 — Connect Unpackerr

Unpackerr has no UI. Set `SONARR_API_KEY` and `RADARR_API_KEY` in `.env.local` (from Settings → General in each app), then `make restart` — it'll auto-extract compressed downloads before Sonarr/Radarr import them.

### Step 8 — Set up Homarr (optional dashboard)

Open Homarr → it auto-detects the other containers via the Docker socket mount. Add tiles for the services you want on your dashboard.

### Step 9 — Add your library to Plex

Plex → Settings → Libraries → Add Library → point it at `/media`. Plex will scan and match everything.

---

## Day-to-day commands

```bash
make up       # start everything
make down     # stop everything
make restart  # restart all containers
make logs     # watch live logs (Ctrl+C to stop)
make pull     # download image updates
make ps       # see container status
make extras   # start optional services (LazyLibrarian)
```

---

## Optional services

LazyLibrarian is available but not started by default:

| Service | Purpose | When to use |
|---------|---------|-------------|
| LazyLibrarian | Books & audiobooks | If you want to automate ebook downloads |

Start it with:

```bash
make extras
```

Jackett and FlareSolverr now start by default alongside the core stack (Prowlarr uses FlareSolverr for Cloudflare-protected trackers; Jackett is a fallback indexer proxy for trackers Prowlarr doesn't support).

---

## Updating

Pull the latest versions of all containers:

```bash
make pull
make up
```

Your settings and library are not affected — everything is stored in your app data folder.

---

## Folder layout

```
/data/
├── media/        ← your final library (Plex points here)
│   ├── tv/
│   └── movies/
├── downloads/    ← temporary downloads (cleared after import)
└── appdata/      ← container configs (back this up)
```

Keep `media` and `downloads` on the same drive. This lets Sonarr and Radarr move files instantly without copying them.

---

## Changing your config

All settings are in `.env.local` (created by setup.sh). Edit it and restart:

```bash
nano .env.local
make restart
```

| Setting | What it controls |
|---------|-----------------|
| `MEDIA_PATH` | Where your media library lives |
| `DOWNLOADS_PATH` | Where torrents download to |
| `APPDATA_PATH` | Where container configs are stored |
| `TZ` | Your timezone |
| `PUID` / `PGID` | The user containers run as |
| `RESTART_POLICY` | Whether containers restart on reboot (`unless-stopped` = yes) |

---

## Reverse proxy (optional)

If you want to access your services from outside your home network, put them behind a reverse proxy with HTTPS. [Nginx Proxy Manager](https://nginxproxymanager.com/) is the easiest option.

Safe to expose publicly: **Seerr**, **Plex**  
Keep internal only: **qBittorrent**, Sonarr, Radarr, Prowlarr, Homarr, Nginx Proxy Manager admin UI (unless you add a login)

**Homarr's Docker socket mount** gives that container effectively root-level control over the host (it can start/stop/inspect any container, including ones with other host mounts). Only run Homarr if you trust everything else on this host and understand that trade-off — do not expose it to the internet.

---

## Troubleshooting

**Containers won't start**
```bash
docker compose logs <service-name>
```
Look for missing directories or permission errors.

**Sonarr/Radarr not importing downloads**  
Make sure `DOWNLOADS_PATH` and `MEDIA_PATH` are on the same drive. If they're on different drives, files have to be copied instead of moved, which can cause timeouts.

**Plex can't find my media**  
Run `docker exec plex ls /media` — if it's empty, your `MEDIA_PATH` in `.env.local` is wrong.

**Service shows "unhealthy" on first start**  
Normal. Wait 60 seconds and check again with `make ps`.

**Permission denied errors**  
```bash
sudo chown -R $(id -u):$(id -g) /data
```

**Wrong timezone**  
Edit `TZ` in `.env.local` → `make restart`. Full list of valid values [here](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones).

---

## VPN (recommended for torrenting)

If you're using public trackers, routing qBittorrent through a VPN is strongly recommended. Create a `docker-compose.override.yml` file (not tracked by git) with:

```yaml
services:
  gluetun:
    image: qmcgaw/gluetun
    container_name: gluetun
    cap_add:
      - NET_ADMIN
    environment:
      - VPN_SERVICE_PROVIDER=your_provider
      - OPENVPN_USER=your_username
      - OPENVPN_PASSWORD=your_password
    ports:
      - ${QBITTORRENT_PORT}:${QBITTORRENT_PORT}

  qbittorrent:
    network_mode: "service:gluetun"
    ports: []
    depends_on:
      - gluetun
```

Supported providers: Mullvad, ProtonVPN, NordVPN, and many more. See [Gluetun's wiki](https://github.com/qdm12/gluetun/wiki).

---

## Hardware

| | Minimum | Better |
|-|---------|--------|
| CPU | 2 cores | 4+ cores |
| RAM | 4 GB | 8 GB+ |
| OS disk | Any | SSD |
| Media disk | HDD | HDD or NAS |

Plex hardware transcoding (faster, less CPU) requires a Plex Pass subscription and a GPU. Add this to `docker-compose.override.yml` for Intel iGPU:

```yaml
services:
  plex:
    devices:
      - /dev/dri:/dev/dri
```

---

## Security

- Change the qBittorrent password immediately (default is `adminadmin`)
- Do not expose qBittorrent to the internet
- Use a VPN if you're downloading from public trackers
- `.env.local` is excluded from git — never commit it

---

## FAQ

**Do I need a VPN?** Recommended for public trackers, not strictly required.

**Can this run on a NAS?** Yes — Unraid, TrueNAS SCALE, Synology (with Docker support).

**Why does Plex use a different network mode?** Plex needs "host" networking for local discovery and DLNA. This is normal and expected.

**What's Prowlarr vs Jackett?** Prowlarr is the modern replacement. It syncs indexers directly into Sonarr and Radarr. Jackett is older and only included as a fallback for trackers Prowlarr doesn't support yet.

---

## License

MIT — use responsibly and in accordance with the laws of your jurisdiction.
