# Backrest: backups that report to the hub

**English** · [Português](../pt-BR/backrest.md) · [← README](../../README.md)

[Backrest](https://github.com/garethgeorge/backrest) is a web UI for [restic](https://restic.net), which makes encrypted, deduplicated, versioned backups.
This guide installs Backrest, creates a first backup and connects it to the hub's **Backups & disks** card.

Three concepts first:

| | |
|---|---|
| **Repository** | where backups are stored: an external drive, another server, or a bucket (Backblaze B2, S3, rclone…). It is protected by a **password** |
| **Plan** | *what* to back up (folders), *when* (schedule) and *how long* to keep it (retention) |
| **Snapshot** | a point-in-time copy of the folders. You restore from a snapshot |

## 1. Install

The official script installs the binary in `/usr/local/bin` and creates a systemd service that runs as **your user** and listens on `127.0.0.1:9898` only.
For safety, download and read the script before running it:

```bash
curl -fsSLo /tmp/backrest-install.sh https://raw.githubusercontent.com/garethgeorge/backrest/main/install.sh
less /tmp/backrest-install.sh
bash /tmp/backrest-install.sh
```

> To back up files owned by other users or by the system (e.g. `/etc`, `/var/lib/docker/volumes`), Backrest must run as root.
> In that case, use `bash /tmp/backrest-install.sh -- --root`. If you only back up your own folders, keep the default (your user).

Check:

```bash
systemctl status 'backrest*' --no-pager
curl -sI http://127.0.0.1:9898 | head -1
```

## 2. Open the UI

Like the hub, Backrest only listens on the server itself. From your computer, open an SSH tunnel:

```bash
ssh -N -L 9898:127.0.0.1:9898 user@server-ip
```

and browse to `http://localhost:9898`. Tailscale or Cloudflare also work: see [remote-access.md](remote-access.md) and [cloudflare.md](cloudflare.md), using port 9898 instead of 8084.

On first launch, Backrest asks for an **instance name** (e.g. the hostname) and a **username and password** for the UI. Create both.

## 3. Create the repository

**Add Repo**:

- **Repo name:** e.g. `external-drive`
- **Repository URI:** where to store backups. Examples:
  - local or external disk, mounted: `/mnt/backup/restic`
  - Backblaze B2: `b2:bucket-name:/server`
  - SFTP to another machine: `sftp:user@other-host:/backups/server`
- **Password:** generate a strong one (`openssl rand -base64 32`).

> ⚠️ **Store the repository password OUTSIDE the server**, in a password manager. If the server dies and the password is lost, nobody can open the backups, including you.

For cloud storage, fill in the required environment variables (e.g. `B2_ACCOUNT_ID` and `B2_ACCOUNT_KEY`) in the repository form.

## 4. Create the plan

**Add Plan**:

- **Plan name:** e.g. `documents`. The hub uses this name in the alert.
- **Repository:** the one you just created.
- **Paths:** the folders, e.g. `/home/user/documents`, `/home/user/stack`.
- **Excludes:** what to skip, e.g. `**/node_modules`, `**/.cache`, `*.tmp`.
- **Schedule:** e.g. every day at 3 AM, which is `0 3 * * *` in cron.
- **Retention:** e.g. keep 7 daily, 4 weekly and 6 monthly.

Save it and click **Backup Now** to run the first backup.

## 5. Report to the hub

### 5.1 Install `hub-notify` (once)

From the hub folder:

```bash
sudo install -m 755 scripts/hub-notify /usr/local/bin/hub-notify
echo "HUB_TOKEN_FILE=$PWD/data/notify_token" | sudo tee /etc/hub-notify.env
hub-notify test:backrest ok "Test" "hub-notify works"
```

A "Test" item should appear in **Backups & disks**. Remove it with **✕** afterwards.

> `hub-notify` must be able to read `data/notify_token`. That file belongs to your user (mode `600`), and Backrest runs either as your user or as root, so it can read it in both cases.

### 5.2 Add hooks to the plan

Open the plan → **Hooks** → **Add Hook** → type **Command**. Create three hooks.

**Success.** Condition `CONDITION_SNAPSHOT_SUCCESS`:

```bash
/usr/local/bin/hub-notify "backup:{{ .Plan.Id }}" ok "Backup {{ .Plan.Id }}" "backup ok" 26
```

`26` is `esperado_horas` (expected hours): if 26 h pass without a successful backup (a daily schedule plus 2 h of slack), the item turns **late** in the hub.
For a weekly plan, use `170`.

**Warning.** Condition `CONDITION_SNAPSHOT_WARNING`:

```bash
/usr/local/bin/hub-notify "backup:{{ .Plan.Id }}" aviso "Backup {{ .Plan.Id }}" "backup finished with warnings (some files could not be read)"
```

**Error.** Conditions `CONDITION_SNAPSHOT_ERROR` and `CONDITION_ANY_ERROR`:

```bash
/usr/local/bin/hub-notify "backup:{{ .Plan.Id }}" erro "Backup {{ .Plan.Id }}" {{ .ShellEscape .Error }}
```

If your Backrest version rejects the `{{ .ShellEscape .Error }}` template, replace it with fixed text such as `"failed, check Backrest"`.
The available variables are listed in the Backrest docs (*Hooks* and *Command hook examples*) at [garethgeorge.github.io/backrest](https://garethgeorge.github.io/backrest/).

The levels are `ok`, `aviso` (warning) and `erro` (error). Save the plan and run **Backup Now**: `Backup documents` should appear as **ok**.

### Backrest in Docker?

If Backrest runs in a container, the hook runs **inside the container**, which does not have `hub-notify`.
The simplest option is the native install above. To keep the container:

1. add `network_mode: host` to the service, so it can reach `127.0.0.1:8084`;
2. mount the token read-only: `- /path/to/hub/data/notify_token:/hub_token:ro`;
3. check that the image has `curl`: `docker exec backrest which curl`;
4. use a hook like this:

```bash
curl -fsS -X POST http://127.0.0.1:8084/api/eventos -H "X-Hub-Token: $(cat /hub_token)" -H 'Content-Type: application/json' -d '{"chave": "backup:{{ .Plan.Id }}", "nivel": "ok", "titulo": "Backup {{ .Plan.Id }}", "evento": "backup ok", "esperado_horas": 26}'
```

## 6. Test a restore (really)

A backup you have never restored is not a backup. Once a month:

1. in Backrest, open the plan → **Snapshots**, pick one and click **Browse**;
2. select a folder → **Restore to path** → e.g. `/tmp/restore-test`;
3. check the files, then delete the folder.

The 3-2-1 rule: **3** copies, on **2** different media, **1** off-site (cloud or a friend's house). One local repository plus one in the cloud is enough.
