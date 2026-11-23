# Cloudflare Tunnel + Access: the hub on the internet, behind a login

**English** · [Português](../pt-BR/cloudflare.md) · [← README](../../README.md)

With this setup the hub is reachable at `https://hub.your-domain.com` over HTTPS, **without opening any port on your router**, and Cloudflare asks for a login before anyone sees the page.

```
browser ──► Cloudflare (Access: login) ──► tunnel ──► cloudflared on the server ──► 127.0.0.1:8084 (hub)
```

**You need:** a domain whose DNS is on Cloudflare (the free plan works) and Zero Trust enabled (the Free plan covers up to 50 users).

> Cloudflare renames dashboard menus from time to time. If you cannot find something, search for the English name (*Tunnels*, *Access → Applications*).

## Order matters: create Access **before** the public hostname

If you publish the hostname before creating the login, the hub is open to the internet in between. So follow this order: 1 → 2 → 3.

## 1. Access application (the login)

Zero Trust → **Access → Applications → Add an application → Self-hosted**:

- **Application name:** `hub`
- **Domain:** `hub` · `your-domain.com`. If an application for `*.your-domain.com` already exists, it already covers the hub.
- **Session duration:** e.g. 24 h
- **Policy:** name `only me`, Action **Allow**, Include → **Emails** → your e-mail. Use **Emails ending in** to allow a whole domain, or add more e-mails.
- **Login methods:** *One-time PIN* (a code sent by e-mail) works out of the box. Google/GitHub are optional.

## 2. Tunnel and `cloudflared`

Zero Trust → **Networks → Tunnels → Create a tunnel → Cloudflared**, give it a name (e.g. `server`) and choose **Debian / Ubuntu**.
The page shows the commands that install `cloudflared` and a `sudo cloudflared service install <TOKEN>` command. Run them on the server.

> The `<TOKEN>` grants access to your tunnel. Do not paste it into chats, issues or commits. If it leaks, rotate it in the dashboard.

Check:

```bash
systemctl status cloudflared --no-pager
```

The tunnel should show as **Healthy** in the dashboard.

## 3. Public hostname

In the tunnel → **Public hostname → Add a public hostname**:

- **Subdomain:** `hub` · **Domain:** `your-domain.com`
- **Service:** `HTTP` → `localhost:8084`

Test it in a **private window**: `https://hub.your-domain.com` should ask for the Access login before showing the dashboard.

Keep the hub on `HUB_BIND=127.0.0.1`: `cloudflared` runs on the server itself and talks to `localhost`.
If your `cloudflared` runs in Docker, it needs `network_mode: host` to reach `localhost:8084`.

## 4. (Recommended) The hub checks the login too

As defense in depth, the hub validates the token (JWT) that Access sends with every request. Even if someone reaches port 8084 directly, they get **403**.

1. **Team domain:** Zero Trust → **Settings → Custom pages** (or *Team name and domain*). It looks like `my-team.cloudflareaccess.com`.
2. **AUD:** Access → Applications → `hub` → **Configure → Overview → Application Audience (AUD) Tag**.
3. In `.env`:

   ```
   CF_ACCESS_TEAM_DOMAIN=my-team.cloudflareaccess.com
   CF_ACCESS_AUD=<the AUD tag, a long hex string>
   ```

4. `./install.sh`

Test it on the server: `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8084/` should print `403`, while `/healthz` still returns `200`.
From then on you can only get in through the Cloudflare hostname. For offline access, remove both variables from `.env`.

With Access enabled, you **do not need** `HUB_PASSWORD`, which would mean logging in twice.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| 502 / *Bad gateway* | the hub is down (`docker compose ps`), or the public hostname points to the wrong port |
| 403 from the hub after logging in | wrong `CF_ACCESS_AUD` or `CF_ACCESS_TEAM_DOMAIN` in `.env` |
| the page opens without a login | the Access application does not cover this subdomain: check step 1 |
