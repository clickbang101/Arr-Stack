# ARR Stack — Complete Docker Compose Media Automation Setup

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
  <a href="https://github.com/clickbang101/arr-stack/issues">
    <img src="https://img.shields.io/github/issues/clickbang101/arr-stack?style=for-the-badge" />
  </a>
</p>

A complete Docker Compose media automation stack including **Plex, Sonarr, Radarr, qBittorrent, Prowlarr, Bazarr, Overseerr, Jackett, FlareSolverr, and LazyLibrarian**.

---

## 📚 Table of Contents
- [Prerequisites](#-prerequisites)
- [Configuration](#-configuration)
- [Hardware Recommendations](#-hardware-recommendations)
- [Permissions & Ownership](#-permissions--ownership)
- [Architecture Diagram](#-architecture-diagram)
- [Features](#-features)
- [Included Services](#-included-services)
- [Folder Structure](#-folder-structure)
- [Docker Compose File](#-docker-compose-file)
- [Default Ports](#-default-ports)
- [Auto Updates (Watchtower)](#-auto-update-containers-watchtower)
- [Reverse Proxy Examples](#-reverse-proxy-example-nginx-proxy-manager)
- [Troubleshooting](#-troubleshooting)
- [FAQ](#-faq)
- [Deployment](#-deployment)
- [License](#-license)
- [Support](#-support)

---

## 🧰 Prerequisites

Before deploying this stack, ensure the following:

Docker Compose lets you define and run multi-container apps with a single `docker compose` command.

- Linux host (Ubuntu, Debian, or similar)
- Docker installed  
- Docker Compose installed  
- At least **4 GB RAM** recommended  
- SSD recommended for Plex metadata  
- User with correct PUID/PGID:

```bash
id $USER
```

Verify Docker is working:

```bash
docker --version
docker compose version
```

---

## ⚡ Quick Start

```bash
# 1. Clone and enter directory
cd Arr-Stack

# 2. Create and edit configuration
cp .env .env.local
nano .env.local  # Update paths, timezone, PUID/PGID

# 3. Create required directories
mkdir -p /data/media /data/downloads /data/appdata/{plex,sonarr,radarr,prowlarr,bazarr,overseerr,jackett,lazylibrarian,qbittorrent}

# 4. Deploy the stack
docker compose --env-file .env.local up -d

# 4b. Deploy optional services too (Jackett, FlareSolverr, LazyLibrarian)
docker compose --env-file .env.local --profile extras up -d

# 5. Smoke test (open a couple UIs to confirm)
# Plex:        http://localhost:32400
# Sonarr:      http://localhost:8989
# Radarr:      http://localhost:7878
# qBittorrent: http://localhost:8081
# Prowlarr:    http://localhost:9696
# Bazarr:      http://localhost:6767
# Overseerr:   http://localhost:5055
```

---

## ✅ First-Run Checklist

- Set `PUID`, `PGID`, and `TZ` to match your host
- Update `MEDIA_PATH`, `DOWNLOADS_PATH`, and `APPDATA_PATH`
- Create the required directories
- Start the stack and confirm each UI loads
- Wire Sonarr/Radarr to qBittorrent and Prowlarr

---

## ⚙️ Configuration

### Setup Environment Variables

1. **Copy the example `.env` file:**
   ```bash
   cp .env .env.local
   ```

   The tracked `.env` file is a template only — keep real values in `.env.local`.

2. **Edit `.env.local` with your settings:**
   ```bash
   nano .env.local
   ```

3. **Key variables to customize:**

   | Variable | Default | Description |
   |----------|---------|-------------|
   | `PUID` | `1000` | User ID (from `id $USER`) |
   | `PGID` | `1000` | Group ID (from `id $USER`) |
| `TZ` | `Africa/Johannesburg` | Timezone (see [TZ Database](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones)) |
| `MEDIA_PATH` | `/data/media` | Your media library directory |
| `DOWNLOADS_PATH` | `/data/downloads` | Your downloads directory |
| `APPDATA_PATH` | `/data/appdata` | Application data directory |
   | `RESTART_POLICY` | `unless-stopped` | Container restart behavior |

4. **Create the required directories:**
   ```bash
   mkdir -p ${MEDIA_PATH} ${DOWNLOADS_PATH} ${APPDATA_PATH}/{plex,sonarr,radarr,prowlarr,bazarr,overseerr,jackett,lazylibrarian,qbittorrent}
   ```

5. **Fix permissions:**
   ```bash
   sudo chown -R $(id -u):$(id -g) ${MEDIA_PATH} ${DOWNLOADS_PATH} ${APPDATA_PATH}
   ```

6. **Load the configuration:**
   ```bash
   docker compose --env-file .env.local up -d
   ```

> **Tip:** This repo tracks `.env` as a template. Keep real values in `.env.local` and pass it explicitly with `--env-file`. `/data` is just a convention — any path works.

---

## 🖥 Hardware Recommendations

| Component | Recommended |
|----------|-------------|
| CPU      | Quad-core or better |
| RAM      | 4–8 GB minimum |
| SSD      | For appdata |
| HDD      | For media library |
| Network  | Gigabit LAN |

Plex transcoding benefits from GPU acceleration (QuickSync / NVENC).

---

## 🔐 Permissions & Ownership

All containers use:

```
PUID=1000
PGID=1000
```

Fix permissions if needed:

```bash
   sudo chown -R $USER:$USER /data/appdata
   sudo chown -R $USER:$USER /data
```

---

## 🗺 Architecture Diagram

```mermaid
flowchart LR
    subgraph Downloaders
        QB[qBittorrent] --> DL[Downloads Folder]
    end

    subgraph Indexers
        Prowlarr -->|Sync| Sonarr
        Prowlarr -->|Sync| Radarr
        Jackett --> Sonarr
        Jackett --> Radarr
        FlareSolverr --> Jackett
    end

    Sonarr --> QB
    Radarr --> QB
    Sonarr --> Media
    Radarr --> Media

    Bazarr --> Media
    Overseerr --> Sonarr
    Overseerr --> Radarr

    Plex --> Media[(Media Library)]
```

---

## 🚀 Features

- Fully containerized media automation  
- Persistent storage mapping  
- Easy LinuxServer.io updates  
- Clean folder structure  
- Automated downloading / renaming / sorting  
- Cloudflare bypass via FlareSolverr  
- Overseerr request management  

---

## 📦 Included Services

| Service | Purpose |
|--------|---------|
| **Plex** | Media server |
| **qBittorrent** | Torrent client |
| **Sonarr** | TV shows automation |
| **Radarr** | Movies automation |
| **Prowlarr** | Indexer management |
| **Jackett** | Additional indexers |
| **FlareSolverr** | Cloudflare bypass |
| **Bazarr** | Subtitles |
| **Overseerr** | Media request system |
| **LazyLibrarian** | Books & audiobooks |

---

## 🧩 Optional Services (Profiles)

Some services are marked as optional and use Docker Compose profiles. To include them, run:

```bash
docker compose --env-file .env.local --profile extras up -d
```

Included in `extras`:
- Jackett
- FlareSolverr
- LazyLibrarian

### Profiles FAQ

Optional services start only when you pass `--profile extras`.

---

## 📁 Folder Structure

```
/data/
├── appdata/
│   ├── plex
│   ├── qbittorrent
│   ├── sonarr
│   ├── radarr
│   ├── prowlarr
│   ├── bazarr
│   ├── overseerr
│   ├── jackett
│   └── lazylibrarian
├── media
└── downloads
```

---

## 🧩 Docker Compose File

```yaml
<INSERT YOUR docker-compose.yml HERE>
```

---

## 🌍 Default Ports

| App | Port |
|-----|------|
| Plex | 32400 |
| qBittorrent | 8081 |
| Sonarr | 8989 |
| Radarr | 7878 |
| Prowlarr | 9696 |
| Bazarr | 6767 |
| Overseerr | 5055 |
| Jackett | 9117 |
| FlareSolverr | 8191 |
| LazyLibrarian | 8299 |

---

## 🧰 Convenience Commands

If you prefer short commands, use the included `Makefile`:

```bash
make up
make down
make logs
make pull
make ps
```

---

## 🔄 Auto Update Containers (Watchtower)

```yaml
watchtower:
  image: containrrr/watchtower
  container_name: watchtower
  restart: unless-stopped
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock
  command: --cleanup --schedule "0 4 * * *"
```

---

## 🌐 Reverse Proxy Example (Nginx Proxy Manager)

| App | Port | Notes |
|-----|------|-------|
| Plex | 32400 | Use http://host.docker.internal:32400 |
| Sonarr | 8989 | Works normally |
| Radarr | 7878 | Same as Sonarr |
| Overseerr | 5055 | Great for public users |
| qBittorrent | 8081 | Do **NOT** expose publicly |

Use Let's Encrypt for SSL certificates.

---

## 🔒 Security Note

- Do not expose qBittorrent publicly.
- A VPN is strongly recommended if you are torrenting.

---

## 🧱 Example Overrides

Create `docker-compose.override.yml` for local-only tweaks. This file is ignored by Git.

```yaml
services:
  qbittorrent:
    deploy:
      resources:
        limits:
          memory: 1g
```

---

## 🐛 Troubleshooting

### Port already in use
- Stop the service using the port or change the port in `.env.local`

### Plex can't see media
- Check folder mapping  
- Correct permissions  

### qBittorrent port closed
- Ensure router/firewall forwards 6881 TCP/UDP  

### Sonarr/Radarr not importing
- Permissions issue or wrong folder path  
- Keep `DOWNLOADS_PATH` and `MEDIA_PATH` on the same filesystem to allow hardlinks

### Indexers failing
- Use Jackett + FlareSolverr when behind Cloudflare  

---

## ⚠️ Common Pitfalls

- If you use `.env.local`, pass it explicitly: `docker compose --env-file .env.local up -d`
- Permissions issues are the #1 cause of missing imports or failed downloads
- Plex runs in host network mode, so its port is fixed at 32400 regardless of `PLEX_PORT`
- Some services may show as "unhealthy" during initial startup while UI endpoints warm up

---

## 🌐 Networking Notes

- Plex uses host networking for discovery and DLNA; proxying it can break discovery.
- Most other services can be safely proxied with standard HTTP reverse proxy setups.

---

## 🧭 Recommended Hardlink Layout

For Sonarr/Radarr, keep downloads and media on the same filesystem so hardlinks work:

```
/data
├── downloads
└── media
```

Then set:
- `DOWNLOADS_PATH=/data/downloads`
- `MEDIA_PATH=/data/media`

## ❓ FAQ

### Do I need a VPN for torrents?
Recommended, but optional.

### Can Plex transcode?
Yes — CPU/GPU dependent.

### Can this run on a NAS?
Yes — Unraid, TrueNAS, Synology.

---

## 🛠 Deployment

```bash
git clone https://github.com/clickbang101/arr-stack.git
cd arr-stack
docker compose --env-file .env.local up -d
```

View logs:

```bash
docker compose logs -f
```

---

## 🔄 Updating

Pull the latest images and recreate containers:

```bash
docker compose --env-file .env.local pull
docker compose --env-file .env.local up -d
```

Or with the `Makefile`:

```bash
make pull
make up
```

## 📝 License

MIT License.

---

## 🤝 Support

Open an issue anytime.

If you need help, include relevant logs and your `.env.local` (with any secrets removed).

---

## ⚖️ Legal Note

Use this stack responsibly and comply with local laws and content licensing.
