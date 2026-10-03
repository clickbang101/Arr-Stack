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

A self-hosted media server stack. Plex, the *arr apps, qBittorrent and friends find, download, organise and stream your TV shows and movies.

**Services (12):** Plex · Sonarr · Radarr · Prowlarr · Bazarr · qBittorrent · Seerr · Jackett · FlareSolverr · Unpackerr · Tautulli · Maintainerr

`docker-compose.yml` mirrors the `arr-stack` stack as deployed in Portainer. Homarr and Nginx Proxy Manager run as **separate** Portainer stacks and share the `arr-net` network. They are not part of this file.

---

## Rebuild after a crash (quick path)

All state lives in the app data folder. If you still have it, or have a backup, the stack comes back exactly as it was: same API keys, libraries and settings.

```bash
# 1. Docker installed, user in the docker group
curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker $USER   # then log out/in

# 2. Restore app data (if the disk was lost), e.g.
#    rsync -a backup:/appdata/ /home/supervisor/appdata/

# 3. Get the repo and prepare the host
git clone https://github.com/clickbang101/Arr-Stack.git && cd Arr-Stack
./setup.sh --portainer     # or plain ./setup.sh to start it from the CLI
```

`setup.sh` creates the `arr-net` network, writes `.env` from `.env.example` (only if missing), and creates the app data folders. It never overwrites existing config or data.

Then **either** start from the CLI with `make up`, **or** use Portainer:

1. **Stacks → Add stack**, name `arr-stack`
2. **Repository** → `https://github.com/clickbang101/Arr-Stack`, compose path `docker-compose.yml`
3. **Environment variables → Load variables from .env file** → upload your `.env`
4. **Deploy the stack**

> Fill in `SONARR_API_KEY` / `RADARR_API_KEY` in `.env` (Sonarr/Radarr → Settings → General). They're only needed by Unpackerr. If you restored app data, the keys are the same as before.

---

## Services

| Service | Address | What it does |
|---------|---------|--------------|
| Plex | http://HOST:32400/web | Watch your media |
| Seerr | http://HOST:5055 | Request new movies & shows (Overseerr fork) |
| Sonarr | http://HOST:8989 | Manages TV shows |
| Radarr | http://HOST:7878 | Manages movies |
| qBittorrent | http://HOST:8080 | Downloads torrents |
| Prowlarr | http://HOST:9696 | Indexer manager, syncs into Sonarr/Radarr |
| Bazarr | http://HOST:6767 | Downloads subtitles |
| Jackett | http://HOST:9117 | Fallback indexer proxy |
| FlareSolverr | http://HOST:8191 | Cloudflare bypass for Prowlarr/Jackett |
| Tautulli | http://HOST:8181 | Plex watch stats |
| Maintainerr | http://HOST:6246 | Automated library cleanup rules |
| Unpackerr | (no UI) | Auto-extracts compressed downloads |

---

## Configuration

Everything is in `.env` (copied from `.env.example`). `.env` is git-ignored. Never commit it.

| Setting | What it controls |
|---------|-----------------|
| `PUID` / `PGID` | User the containers run as (`id` on the host) |
| `TZ` | Timezone |
| `APPDATA_PATH` | Container configs and databases. **Back this up.** |
| `MEDIA_PATH` | Final library (Plex, Sonarr, Radarr, Bazarr) |
| `DOWNLOADS_PATH` | Torrent downloads |
| `*_PORT` | Web UI ports |
| `PLEX_CPUS`, `UNPACKERR_CPUS`, `FLARESOLVERR_CPUS` | CPU caps (see below) |
| `FLARESOLVERR_MEM` | FlareSolverr memory cap (see below) |
| `SONARR_API_KEY`, `RADARR_API_KEY` | Used by Unpackerr |

After editing: `make up` (CLI) or **Update the stack** in Portainer.

### CPU limits

The host is a 4-vCPU VM with **no GPU**, so Plex transcodes in software, and one 1080p→720p transcode can use every core. To keep the rest of the stack responsive:

| Container | Default cap | Why |
|-----------|-------------|-----|
| Plex | 3 cores | Software transcoding |
| Unpackerr | 1 core | RAR/ZIP extraction |
| FlareSolverr | 1 core | Headless Chrome per Cloudflare solve |

A capped Plex can still buffer on heavy transcodes. To cut transcode load at the source, set clients to **Original/Maximum** quality, and limit simultaneous transcodes in Plex → Settings → Transcoder. A real fix is GPU passthrough plus a Plex Pass. Add the device in a `docker-compose.override.yml`:

```yaml
services:
  plex:
    devices:
      - /dev/dri:/dev/dri
```

### Memory and log limits

**FlareSolverr** leaks Chromium processes over days. On this host it once reached 77 open browsers, filled the 4 GB swap and slowed everything down. It is capped at `FLARESOLVERR_MEM` (default `1g`, no swap). When it hits the cap the kernel kills the leaked browsers inside the container, and the rest of the host is unaffected. If indexer searches through FlareSolverr start failing, `docker restart flaresolverr` clears it.

**Logs**: Docker's default logging never rotates. Every service keeps at most 3 × 10 MB of logs (the `x-logging` block at the top of `docker-compose.yml`). The cap only applies to containers created after the change, so it takes effect on the next stack update.

---

## Backups

`APPDATA_PATH` is the only thing you need to rebuild the stack. Media can be re-downloaded. Stop the stack first so the SQLite databases are consistent:

```bash
make down
sudo tar czf appdata-$(date +%F).tgz -C /home/supervisor appdata
make up
```

Also keep a copy of your `.env` somewhere safe outside the repo.

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
| Port | `8080` |
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

Unpackerr has no UI. Set `SONARR_API_KEY` and `RADARR_API_KEY` in `.env` (from Settings → General in each app), then `make restart` — it'll auto-extract compressed downloads before Sonarr/Radarr import them.

### Step 8 — Add your library to Plex

Plex → Settings → Libraries → Add Library → point it at `/media`. Plex will scan and match everything.

---

## Day-to-day commands

```bash
make up       # create network if needed, start everything
make down     # stop everything
make restart  # restart all containers
make logs     # follow logs (Ctrl+C to stop)
make update   # pull new images and recreate changed containers
make ps       # container status
```

In Portainer: **Stacks → arr-stack → Pull and redeploy** does the same as `make update`.

---

## Folder layout

```
/home/supervisor/
├── appdata/            ← container configs (back this up)
└── share/
    ├── media/          ← final library (Plex points here)
    │   ├── tv/
    │   └── movies/
    └── downloads/      ← in-progress downloads
```

Keep `media` and `downloads` on the same filesystem so Sonarr/Radarr can move files instantly instead of copying.

---

## Troubleshooting

**`network arr-net declared as external, but could not be found`**
Run `docker network create arr-net` (or `make network` / `./setup.sh`).

**`required variable APPDATA_PATH is missing a value`**
The stack has no `.env` loaded. On the CLI, create `.env`. In Portainer, load it under Environment variables.

**Containers won't start**
`docker logs <name>`. Usually a missing directory or wrong `PUID`/`PGID` ownership.

**Sonarr/Radarr not importing**
Check that `DOWNLOADS_PATH` and `MEDIA_PATH` are on the same drive, and that qBittorrent's save path is under `/downloads`.

**Plex can't find media**
`docker exec plex ls /media`. If it's empty, `MEDIA_PATH` is wrong.

**Everything is slow while someone is watching**
Plex is transcoding. See [CPU limits](#cpu-limits).

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

## Security

- Change the qBittorrent password immediately (newer images print a temporary one in `docker logs qbittorrent`)
- Don't expose qBittorrent, Sonarr, Radarr, Prowlarr or Jackett to the internet. Seerr and Plex are fine behind a reverse proxy with HTTPS.
- Use a VPN if you're downloading from public trackers (above)
- Keep `.env` out of git. It holds API keys.

---

## FAQ

**Do I need a VPN?** Recommended for public trackers, not strictly required.

**Can this run on a NAS?** Yes — Unraid, TrueNAS SCALE, Synology (with Docker support).

**Why does Plex use host networking?** Plex needs "host" networking for local discovery and DLNA. This is normal and expected.

**What's Prowlarr vs Jackett?** Prowlarr is the modern replacement. It syncs indexers directly into Sonarr and Radarr. Jackett is older and only kept as a fallback for trackers Prowlarr doesn't support yet.

---

## License

MIT — use responsibly and in accordance with the laws of your jurisdiction.
