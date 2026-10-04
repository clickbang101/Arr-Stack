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
| `DATA_PATH` | Folder holding `media/` and `downloads/`. Sonarr, Radarr, Bazarr and Unpackerr see it as `/data` |
| `MEDIA_PATH` | Final library (`DATA_PATH/media`). Plex sees it as `/media` |
| `DOWNLOADS_PATH` | Torrent downloads (`DATA_PATH/downloads`). qBittorrent sees it as `/downloads` |
| `*_PORT` | Web UI ports |
| `PLEX_CPUS`, `UNPACKERR_CPUS`, `FLARESOLVERR_CPUS` | CPU caps (see below) |
| `FLARESOLVERR_MEM` | FlareSolverr memory cap (see below) |
| `SONARR_API_KEY`, `RADARR_API_KEY` | Used by Unpackerr |

After editing: `make up` (CLI) or **Update the stack** in Portainer.

### CPU limits

The host is a 4-vCPU VM. Without [hardware transcoding](#hardware-transcoding-nvidia), Plex transcodes in software, and one 1080p→720p transcode can use every core. To keep the rest of the stack responsive:

| Container | Default cap | Why |
|-----------|-------------|-----|
| Plex | 3 cores | Software transcoding |
| Unpackerr | 1 core | RAR/ZIP extraction |
| FlareSolverr | 1 core | Headless Chrome per Cloudflare solve |

A capped Plex can still buffer on heavy transcodes. To cut transcode load at the source, set clients to **Original/Maximum** quality, and limit simultaneous transcodes in Plex → Settings → Transcoder. The real fix is hardware transcoding (below), which needs **Plex Pass**.

### Hardware transcoding (NVIDIA)

A GTX 1050 or newer decodes and encodes H.264 and HEVC (including 10-bit) on the GPU, so transcodes barely touch the CPU. Plex only uses it with **Plex Pass**. This setup runs Docker in a Proxmox VM, so the card is passed through to the VM.

**1. BIOS (Proxmox host):** enable **VT-d** (MSI: OC → CPU Features → Intel VT-D Technology). Check on the host with `ls /sys/kernel/iommu_groups | wc -l`. It must be more than 0.

**2. Proxmox host: give the card to vfio instead of nouveau.** Get the IDs with `lspci -nn | grep -i nvidia` (GTX 1050: `10de:1c81`, audio `10de:0fb9`):

```bash
echo "options vfio-pci ids=10de:1c81,10de:0fb9" > /etc/modprobe.d/vfio.conf
printf "blacklist nouveau\nblacklist nvidiafb\n" > /etc/modprobe.d/blacklist-gpu.conf
update-initramfs -u -k all && reboot
lspci -k -s 01:00   # after reboot: "Kernel driver in use: vfio-pci"
```

The kernel command line needs `intel_iommu=on iommu=pt`.

**3. Proxmox VM:** with the VM stopped:

```bash
qm set 100 --hostpci0 0000:01:00 --cpu host
```

`--cpu host` also exposes AVX2 for faster software transcoding.

**4. Inside the VM (Ubuntu):**

```bash
sudo ubuntu-drivers install --gpgpu        # or: sudo apt install nvidia-headless-550-server nvidia-utils-550-server
# NVIDIA Container Toolkit: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html
sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker
nvidia-smi                                  # shows the GPU
```

**5. Compose:** `docker-compose.nvidia.yml` gives Plex the GPU.
- CLI: `make up GPU=nvidia`
- Portainer deploys one file: run `make gpu-config` and paste the output as the stack file.

Check with `docker exec plex nvidia-smi`.

**6. Plex:** Settings → Transcoder → tick **Use hardware acceleration when available** and **Use hardware-accelerated video encoding**, then pick the GPU. While something transcodes, the Plex dashboard shows **(hw)** and `nvidia-smi` lists the `Plex Transcoder` process.

Consumer NVIDIA cards limit the number of simultaneous NVENC encodes (currently 8), which is plenty for a household.

**Intel Quick Sync instead:** pass the iGPU to the VM (or run Docker on bare metal) and add `devices: ["/dev/dri:/dev/dri"]` to Plex in a `docker-compose.override.yml`.

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

Then Options → Downloads → tick **Excluded file names** and enter:

```
*.exe, *.scr, *.lnk, *.bat, *.cmd, *.msi, *.vbs, *.zipx
```

This blocks fake "episodes" that are really Windows programs. See [Fake releases](#fake-releases-malware-disguised-as-episodes). Don't add `*.rar` or `*.zip`: real releases use them and Unpackerr extracts them.

### Step 2 — Add indexers in Prowlarr

Open Prowlarr → Indexers → Add Indexer → search for and add the torrent sites you use.

Don't use **LimeTorrents**. In 2026 it supplied every one of the 42 fake `.exe`/`.scr` episodes that got imported into this library. It is disabled in Prowlarr.

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

Then:
- Settings → Media Management → Root Folders → add `/data/media/tv` (Radarr: `/data/media/movies`)
- Settings → Download Clients → **Remote Path Mappings** → add: Host `qbittorrent`, Remote Path `/downloads/`, Local Path `/data/downloads/`

Repeat for Radarr. The mapping tells Sonarr/Radarr where qBittorrent's files are inside `/data`, so imports are hardlinks. See [Hardlinks](#hardlinks-why-data).

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

Keep `media` and `downloads` inside one folder (`DATA_PATH`) on the same filesystem. See below.

### Hardlinks (why `/data`)

When Sonarr/Radarr import a finished download, they try a **hardlink**: the same file appears in both `downloads/` and `media/` while using disk space once, and the torrent keeps seeding. Hardlinks only work inside a single container mount. With separate `/downloads` and `/media` mounts every import silently becomes a **full copy**, and the downloads folder grows into a second copy of your library (on this server: 1.1 TB).

So Sonarr, Radarr, Bazarr and Unpackerr mount `DATA_PATH` as `/data`, and qBittorrent keeps `/downloads`. A Remote Path Mapping (`/downloads/` → `/data/downloads/`) connects the two.

**Check it's working.** After an import, on the host:

```bash
stat -c '%h %n' "$MEDIA_PATH/tv/<Show>/<file>.mkv"   # 2 or more = hardlinked, 1 = copied
```

### Moving an existing install to `/data`

Do this at a quiet time. Nothing is moved or deleted on disk; only paths inside the apps change.

1. Back up `APPDATA_PATH/sonarr` and `APPDATA_PATH/radarr` (stop the containers, or copy the `.db` files plus their `-wal` and `-shm` files).
2. Add `DATA_PATH` to `.env`, then **Update the stack** in Portainer or run `make up`.
3. **Sonarr:**
   - Settings → Media Management → Root Folders → add `/data/media/tv`.
   - Settings → Download Clients → Remote Path Mappings → add Host `qbittorrent`, Remote `/downloads/`, Local `/data/downloads/`.
   - Series → **Select Series** → select all → **Edit** → Root Folder `/data/media/tv` → when asked to move files, choose **No**. The files are already there; this only changes the path Sonarr stores.
   - Remove the old `/media/tv` root folder.
4. **Radarr:** same steps with `/data/media/movies` (Movies → Select Movies → Edit).
5. Wait for the next import and run the `stat` check above.
6. Once nothing in Sonarr/Radarr uses `/media` any more, remove the `MEDIA_PATH:/media` lines from the sonarr and radarr services. Bazarr keeps working because it also has `/data`.

Files that were already copied stay as copies. Free that space by removing finished torrents in qBittorrent (Remove → also delete files).

---

## Troubleshooting

**`network arr-net declared as external, but could not be found`**
Run `docker network create arr-net` (or `make network` / `./setup.sh`).

**`required variable APPDATA_PATH is missing a value`**
The stack has no `.env` loaded. On the CLI, create `.env`. In Portainer, load it under Environment variables.

**Containers won't start**
`docker logs <name>`. Usually a missing directory or wrong `PUID`/`PGID` ownership.

**Sonarr/Radarr not importing**
Usually the Remote Path Mapping is missing (`qbittorrent`, `/downloads/` → `/data/downloads/`), so the app can't find the file. Also check that qBittorrent's save path is under `/downloads`. See [Hardlinks](#hardlinks-why-data).

**Plex can't find media**
`docker exec plex ls /media`. If it's empty, `MEDIA_PATH` is wrong.

**Everything is slow while someone is watching**
Plex is transcoding. See [CPU limits](#cpu-limits).

**Everything is slow and swap is full**
Check `pgrep -c chromium`. Dozens means FlareSolverr has leaked browsers: `docker restart flaresolverr`. See [Memory and log limits](#memory-and-log-limits).

**Stack update leaves containers stuck in "Created", or one shows "Dead"**
A container that can't be removed (often FlareSolverr after its Chromium leak) stops `docker compose` partway through, so the new containers are never started. Check with `docker ps -a`. Bring everything back with `docker start <name> …` for each `Created` container. Then clear the dead one with `sudo systemctl restart docker` (or reboot the VM), followed by `docker rm -f <name>`. If a `<hash>_<name>` copy is left over, run `docker rename <hash>_<name> <name>` and `docker start <name>`.

**Sonarr queue warning: "No files found are eligible for import"**
Usually a fake release whose only file was blocked by qBittorrent's excluded file names. In Activity → Queue, remove it and tick **Blocklist Release**. Sonarr then searches for a different release.

**Disk filling faster than expected**
Downloads that were copied instead of hardlinked into `/media` take up space twice. Remove finished torrents that are already in Plex (qBittorrent → right-click → Remove → also delete files).

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

### Fake releases (malware disguised as episodes)

Some public indexers serve torrents named like a new episode (`The Boys S05E03 1080p … .exe`) that contain a Windows program instead of a video. Sonarr imports them as if they were the episode. They can't run on this Linux host, but they are malware for any Windows PC that opens them, and Sonarr then treats the episode as downloaded so the real one is never fetched.

**Prevention** (both should already be set):
1. qBittorrent → Options → Downloads → **Excluded file names**: `*.exe, *.scr, *.lnk, *.bat, *.cmd, *.msi, *.vbs, *.zipx`. Matching files are skipped and real files are unaffected. A torrent that is *only* a fake finishes empty, and Sonarr shows "No files found are eligible for import" (remove and blocklist it).
2. Disable the indexer supplying them in Prowlarr (LimeTorrents, 2026).

**Check for them** (on the host):

```bash
cd ~/share && find downloads media -xdev -type f \( -iname "*.exe" -o -iname "*.scr" \)
```

`RARBG_DO_NOT_MIRROR.exe` (99 bytes) is a harmless text placeholder from old RARBG releases. Deleting it is fine.

**Which indexer sent them**. Read-only query of Sonarr's history:

```bash
sqlite3 -readonly "file:$HOME/appdata/sonarr/sonarr.db?mode=ro" \
  "select json_extract(Data,'$.indexer'), count(*) from History where EventType=1 and DownloadId in
   (select DownloadId from History where EventType=3 and (json_extract(Data,'$.importedPath') like '%.exe'
    or json_extract(Data,'$.importedPath') like '%.scr')) group by 1;"
```

**Clean up**. Do these in order, or Sonarr re-downloads the fakes:
1. Disable the offending indexer in Prowlarr, and confirm the qBittorrent exclusions are set.
2. Delete the files:
   ```bash
   cd ~/share && find downloads media -xdev -type f \( -iname "*.exe" -o -iname "*.scr" \) -delete
   ```
   "Permission denied" means the folder is owned by `root` (left over from an older setup). Re-run that one with `sudo rm`.
3. qBittorrent: remove torrents that now show **Missing files**.
4. Sonarr: make sure Settings → Media Management → **Unmonitor Deleted Episodes** is off, then Wanted → Missing → **Search All**.

---

## FAQ

**Do I need a VPN?** Recommended for public trackers, not strictly required.

**Can this run on a NAS?** Yes — Unraid, TrueNAS SCALE, Synology (with Docker support).

**Why does Plex use host networking?** Plex needs "host" networking for local discovery and DLNA. This is normal and expected.

**What's Prowlarr vs Jackett?** Prowlarr is the modern replacement. It syncs indexers directly into Sonarr and Radarr. Jackett is older and only kept as a fallback for trackers Prowlarr doesn't support yet.

---

## License

MIT — use responsibly and in accordance with the laws of your jurisdiction.
