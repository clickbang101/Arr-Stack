# ARR Stack -- Docker Compose Setup

```{=html}
<p align="center">
```
`<a href="https://github.com/clickbang101/arr-stack">`{=html}
`<img src="https://img.shields.io/badge/Repo-ARR%20Stack-blue?style=for-the-badge" />`{=html}
`</a>`{=html}

`<a href="https://github.com/clickbang101/arr-stack/blob/main/LICENSE">`{=html}
`<img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" />`{=html}
`</a>`{=html}

`<img src="https://img.shields.io/badge/Docker-Ready-blue?style=for-the-badge" />`{=html}

`<img src="https://img.shields.io/badge/Maintained-Yes-brightgreen?style=for-the-badge" />`{=html}

`<a href="https://github.com/clickbang101/arr-stack/stargazers">`{=html}
`<img src="https://img.shields.io/github/stars/clickbang101/arr-stack?style=for-the-badge" />`{=html}
`</a>`{=html}

`<a href="https://github.com/clickbang101/arr-stack/issues">`{=html}
`<img src="https://img.shields.io/github/issues/clickbang101/arr-stack?style=for-the-badge" />`{=html}
`</a>`{=html}

```{=html}
</p>
```
A complete media automation stack powered by Docker Compose ---
including Plex, Sonarr, Radarr, qBittorrent, Prowlarr, Bazarr,
Overseerr, Jackett, FlareSolverr, and LazyLibrarian.

------------------------------------------------------------------------

## 🚀 Features

-   Fully containerized media ecosystem\
-   Persistent storage mapping\
-   Easy updates with LinuxServer.io images\
-   Clean folder structure\
-   Automatic media downloading, sorting & metadata enrichment

------------------------------------------------------------------------

## 📦 Included Services

  Service             Purpose
  ------------------- ------------------------------
  **Plex**            Media server for movies & TV
  **qBittorrent**     Torrent client with Web UI
  **Sonarr**          TV automation
  **Radarr**          Movie automation
  **Prowlarr**        Indexer manager
  **Bazarr**          Subtitle manager
  **Overseerr**       Request manager for Plex
  **Jackett**         Extra indexer support
  **FlareSolverr**    Cloudflare bypass helper
  **LazyLibrarian**   Ebook & audiobook automation

------------------------------------------------------------------------

## 📁 Folder Structure

    /home/supervisor/
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
    └── share/
        ├── media
        └── downloads

------------------------------------------------------------------------

## 🧩 Docker Compose File

Paste your exact working compose file here:

    <INSERT docker-compose.yml HERE>

------------------------------------------------------------------------

## 🌍 Default Ports

  App             Port
  --------------- -------
  Plex            32400
  qBittorrent     8080
  Sonarr          8989
  Radarr          7878
  Prowlarr        9696
  Bazarr          6767
  Overseerr       5055
  Jackett         9117
  FlareSolverr    8191
  LazyLibrarian   5299

------------------------------------------------------------------------

## 🛠 Deployment

1.  Install Docker + Docker Compose\

2.  Clone the repo:

        git clone https://github.com/clickbang101/arr-stack.git
        cd arr-stack

3.  Start the stack:

        docker compose up -d

4.  View logs:

        docker compose logs -f

------------------------------------------------------------------------

## 📝 License

This project is licensed under the **MIT License**.

------------------------------------------------------------------------

## 🤝 Support

Feel free to open issues or suggestions to improve the stack!
