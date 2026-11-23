# ubuntu-server-hub

**English** · [Português (Brasil)](README.pt-BR.md)

A single-page dashboard and app launcher for a home-lab **Ubuntu Server**, built with **Flask + HTMX + Tailwind** and running in Docker.
Clone it, run one script, and you get live metrics for *your* machine plus a grid of shortcuts to the services you self-host.

> The interface is in Brazilian Portuguese. Everything it shows is read from the machine it runs on, and nothing about any particular server is baked in.

| Card | What it shows | Source |
|---|---|---|
| **Applications** | your shortcuts (starts **empty**), with an online/offline dot per app | `data/apps.json`, managed from the UI |
| **CPU** | per-core usage, usr/sys/iowait, load, frequency, temperature, power (RAPL), 3-min graphs | `/proc`, `/sys` (like `htop` / `s-tui`) |
| **Memory** | RAM (used / cache / free), swap | `/proc/meminfo` |
| **Storage** | disks, partitions, usage, read/write speed | `/sys/block`, host mounts |
| **Network** | interfaces, IP, traffic | `/proc/net` |
| **System** | hostname, model, OS, kernel, uptime, packages, CPU (like `fastfetch`) | `/etc/os-release`, DMI, `/proc/cpuinfo` |
| **Processes** | top 15 by CPU or memory | `/proc` of the host |
| **Updates** | pending `apt` upgrades (security first), reboot-required notice | host's apt/dpkg database |
| **Backups & disks** | alerts sent by [Backrest](docs/en/backrest.md) and [smartd](docs/en/smartd.md) | `POST /api/eventos` |
| **Docker** | all containers, status/health, ports, compose project, CPU/RAM | Docker socket |
| **Installed by you** | apt packages you installed manually, snaps, `/usr/local/bin`, `/opt` | dpkg, apt history |

Blocks can be reordered and resized (**▦ Layout**), and on a phone everything collapses into one column.

---

## Contents

- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Configuration (`.env`)](#configuration-env)
- [Accessing it from other devices](#accessing-it-from-other-devices)
- [Shortcuts (Applications card)](#shortcuts-applications-card)
- [Backup and disk alerts](#backup-and-disk-alerts)
- [Optional extras](#optional-extras)
- [Security model](#security-model)
- [Day-to-day](#day-to-day)
- [Development](#development)
- [Project layout](#project-layout)

---

## Requirements

| Required | Why |
|---|---|
| **Ubuntu Server** 22.04, 24.04 or 26.04 (x86_64 or arm64) | the container image uses the **same Ubuntu version** as the host so that its `apt` can read the host's package database |
| **Docker Engine + Compose plugin** | the hub runs as one container |
| a regular user in the **`docker`** group | `install.sh` refuses to run as root |
| `git`, `curl` | to clone and to check the hub is up |

Install Docker using the official apt repository (see [docs.docker.com/engine/install/ubuntu](https://docs.docker.com/engine/install/ubuntu/)), then:

```bash
sudo usermod -aG docker "$USER"   # then log out and back in
docker run --rm hello-world       # check it works without sudo
```

| Optional | Enables | Guide |
|---|---|---|
| [Backrest](https://github.com/garethgeorge/backrest) (restic web UI) | backup status in **Backups & disks** | [docs/en/backrest.md](docs/en/backrest.md) |
| `smartmontools` (smartd) | disk-health alerts in **Backups & disks** | [docs/en/smartd.md](docs/en/smartd.md) |
| Cloudflare Tunnel + Access | HTTPS access from anywhere, with login | [docs/en/cloudflare.md](docs/en/cloudflare.md) |
| Tailscale or SSH | private access without opening ports | [docs/en/remote-access.md](docs/en/remote-access.md) |
| `drivetemp` kernel module, RAPL permission | disk temperatures, CPU power in watts | [Optional extras](#optional-extras) |

`htop`, `s-tui` and `fastfetch` are **not** needed: the hub reads the same kernel data they do.

## Quick start

```bash
git clone https://github.com/scoranardo/ubuntu-server-hub.git
cd ubuntu-server-hub
./install.sh
```

`install.sh`:

1. creates `.env` from `.env.example` (mode `600`) if it does not exist yet;
2. fills in your Ubuntu version, UID/GID, username and the Docker socket's group;
3. creates `./data` (shortcuts, layout, alerts, notification token);
4. runs `docker compose up -d --build` and waits for `/healthz`.

It is safe to run again at any time, for example after `git pull`. Your `.env` and `data/` are kept.

By default the hub listens on **`127.0.0.1:8084` only**. On the server itself, run `curl -s http://127.0.0.1:8084/healthz`.
To open it from your laptop or phone, see [Accessing it from other devices](#accessing-it-from-other-devices).

## Configuration (`.env`)

All local settings go in `.env`, which is listed in `.gitignore`. Edit it, then run `./install.sh` again (or `docker compose up -d`).

| Variable | Default | |
|---|---|---|
| `HUB_TITLE` | the machine's hostname | big name in the top-left corner |
| `HUB_SUBTITLE` | computer model from DMI | small line under the name |
| `HUB_HOST_MODEL` | detected | overrides the model in the **System** card |
| `HUB_CPU_NAME` | detected from `/proc/cpuinfo` | overrides the CPU name |
| `HUB_BIND` | `127.0.0.1` | address to listen on. `0.0.0.0` = whole LAN (**requires** `HUB_PASSWORD`) |
| `HUB_PORT` | `8084` | port |
| `HUB_USER` / `HUB_PASSWORD` | `admin` / empty | optional login (HTTP Basic) |
| `HUB_ALLOW_NO_AUTH` | empty | `1` lets `HUB_BIND` be non-local **without** a password (not recommended) |
| `CF_ACCESS_TEAM_DOMAIN` / `CF_ACCESS_AUD` | empty | validate the Cloudflare Access JWT ([guide](docs/en/cloudflare.md)) |
| `HUB_ICON_CDN` | dashboard-icons on jsDelivr | icon source (`/tree.json` + `/svg/<name>.svg`) |
| `HUB_DATA_PATH` | `./data` | where the hub keeps its data |
| `UBUNTU_VERSION`, `HUB_UID`, `HUB_GID`, `HUB_HOST_USER`, `DOCKER_GID` | set by `install.sh` | do not edit by hand |

Hostname, model and CPU are **detected automatically**. You only need these variables if you want a different label.
Some vendors fill DMI with junk such as "To be filled by O.E.M.". In that case the hub falls back to the hostname, and `HUB_SUBTITLE` fixes it.

## Accessing it from other devices

Choose one. They are listed from most to least recommended:

| Option | Exposure | Login | Guide |
|---|---|---|---|
| **Tailscale** (`tailscale serve`) | only your devices, HTTPS | your Tailscale account | [remote-access.md](docs/en/remote-access.md#tailscale) |
| **Cloudflare Tunnel + Access** | internet, HTTPS, no open ports | e-mail / SSO at Cloudflare | [cloudflare.md](docs/en/cloudflare.md) |
| **SSH tunnel** | only you, nothing to install | your SSH key | [remote-access.md](docs/en/remote-access.md#ssh-tunnel) |
| **LAN** (`HUB_BIND=0.0.0.0` + `HUB_PASSWORD`) | everyone on your network, plain HTTP | password | below |

LAN mode:

```bash
openssl rand -base64 24     # generate a strong password
nano .env                   # HUB_BIND=0.0.0.0 and HUB_PASSWORD=<password>
./install.sh
```

Then open `http://<server-ip>:8084`. Without HTTPS the password crosses your LAN in clear text, so use this only on a network you trust.
If the server runs `ufw`, also run `sudo ufw allow from 192.168.0.0/16 to any port 8084 proto tcp` (adjust it to your subnet).
**Never** forward port 8084 on your router.

## Shortcuts (Applications card)

The list starts **empty**. Use **+ New** (`+ Novo`) to add a shortcut and **✎ Organize** (`✎ Organizar`) to move, edit or delete them.
They are stored in `data/apps.json`:

```json
[
  {"id": "a1b2c3d4", "name": "Jellyfin", "url": "https://jellyfin.example.com", "icon": "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/jellyfin.svg",
   "description": "Media", "group": "Media", "check_url": "http://127.0.0.1:8096"}
]
```

- **Icon:** search by name in the form (icons come from [dashboard-icons](https://github.com/homarr-labs/dashboard-icons)). You can also type an emoji, paste an image URL, or upload a PNG/JPG/GIF/WEBP/ICO/SVG of up to 512 KB, which is stored in `data/icons/`.
- **Internal URL (status):** the green/red dot checks this address. Use the local address (`http://127.0.0.1:<port>`): a public address behind a login page always answers, even when the app is down.
- Keyboard: `/` focuses search, `Enter` opens the first match, `Esc` clears it.

## Backup and disk alerts

The **Backups & disks** card shows the latest event for each *key* and lists problems first. An item that should have reported within `esperado_horas` and did not is marked **late** (`atrasado`).
**✓** marks an error as resolved, and **✕** forgets the key.

Other programs on the server send events with `POST /api/eventos` and the `X-Hub-Token` header.
The token is created on first start in `data/notify_token` (mode `600`). Treat it like a password.

The easiest way to send one is the bundled `scripts/hub-notify`:

```bash
sudo install -m 755 scripts/hub-notify /usr/local/bin/hub-notify
echo "HUB_TOKEN_FILE=$PWD/data/notify_token" | sudo tee /etc/hub-notify.env
hub-notify test:manual ok "Test" "it works"
```

<details>
<summary>Raw HTTP API</summary>

```bash
printf 'X-Hub-Token: %s\n' "$(cat data/notify_token)" | \
  curl -s -XPOST http://127.0.0.1:8084/api/eventos -H @- -H 'Content-Type: application/json' \
  -d '{"chave": "test:manual", "titulo": "Test", "nivel": "ok", "evento": "it works"}'
```

| Field | |
|---|---|
| `chave` (required) | item id, e.g. `backup:photos`, `disco:/dev/sda` |
| `nivel` | `ok`, `aviso` (warning) or `erro` (error). If omitted, it is guessed from `evento`/`erro` |
| `titulo`, `evento`, `mensagem`, `erro`, `motivo`, `origem` | texts shown on the card |
| `esperado_horas` | expected interval in hours. If the next event does not arrive in time, the item becomes *late* |
| `estatisticas`, `snapshot`, `duracao` | backup details (restic summary fields: `data_added`, `total_bytes_processed`, …) |

The last 300 events are kept in `data/eventos.json`. This route bypasses the password and the Cloudflare JWT check, because it is called by local scripts, but it always requires the token.
</details>

Step-by-step guides: **[Backrest](docs/en/backrest.md)** · **[smartd](docs/en/smartd.md)**.

## Optional extras

**CPU power (RAPL, Intel):** by default the kernel only lets root read the energy counter. To allow everyone to read it:

```bash
echo 'z /sys/class/powercap/intel-rapl:0/energy_uj 0444 - - -' | sudo tee /etc/tmpfiles.d/rapl.conf
sudo systemd-tmpfiles --create /etc/tmpfiles.d/rapl.conf
```

The counter is restricted because of the *Platypus* side-channel attack. On a single-user home server the risk is low, but the choice is yours.

**Disk temperatures:**

```bash
sudo modprobe drivetemp && echo drivetemp | sudo tee /etc/modules-load.d/drivetemp.conf
```

## Security model

Read this before exposing the hub anywhere.

- **The container is privileged in practice.** It mounts `/var/run/docker.sock`, and anyone who can talk to the Docker socket can become root on the host. It also sees the host's processes (`pid: host`), network (`network_mode: host`) and the whole filesystem **read-only** (`/:/host:ro`).
  To reduce the risk, the process runs as an unprivileged user with your UID, the root filesystem is `read_only`, all capabilities are dropped and `no-new-privileges` is set.
- **So treat the dashboard as an admin console.** It listens on `127.0.0.1` by default and refuses to start on any other address unless a password or Cloudflare Access is configured.
- **What the page reveals:** process command lines, usernames, IPs, installed software and container names. Only show it to people you would give a shell.
- **Your data stays out of git:** `.env` and `data/` (shortcuts, token, alerts, layout) are in `.gitignore` and `.dockerignore`. If you keep your fork public, run `git status` before every commit and never use `git add -f` on those paths.
- **No telemetry.** The only outbound requests are the icon list (`HUB_ICON_CDN`, once a day) and the status checks to the URLs you configure. Your browser loads the icon images from the CDN.

Found a vulnerability? Please open a private security advisory on GitHub (**Security → Report a vulnerability**) instead of a public issue.

## Day-to-day

```bash
docker compose ps                 # status
docker compose logs -f hub        # logs
git pull && ./install.sh          # update
docker compose down               # stop (data/ is kept)
```

## Development

The CSS is precompiled in `static/app.css`. If you change Tailwind classes in the templates, rebuild it:

```bash
npx @tailwindcss/cli@4 -i assets/app.css -o static/app.css --minify
```

To run without Docker, reading the machine you are on:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python app.py      # http://127.0.0.1:8084
```

Want automatic deploys from your own fork? See [docs/en/self-hosted-deploy.md](docs/en/self-hosted-deploy.md). It is only safe for **private** forks.

## Project layout

```
app.py              Flask routes (page, HTMX partials, shortcuts CRUD, alerts, optional login)
alertas.py          alerts received on /api/eventos: token, storage and card state
collectors.py       metrics thread (every 2 s), Docker via socket, host apt/dpkg via /host
templates/          index.html + partials/ (each card is an HTMX-refreshed partial)
static/             compiled app.css and htmx.min.js 2.0.4 (served locally)
assets/app.css      Tailwind source
scripts/            hub-notify (send alerts), smartd-hub-notify (smartd → hub)
install.sh          creates .env, builds and starts the container
Dockerfile          ubuntu:<host version> + venv + gunicorn
compose.yaml        "hub" container (host network/pid, host / read-only)
.env.example        every setting, documented
docs/en, docs/pt-BR step-by-step guides
```

## License

[MIT](LICENSE)
