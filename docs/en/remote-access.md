# Private remote access: Tailscale or SSH tunnel

**English** · [Português](../pt-BR/acesso-remoto.md) · [← README](../../README.md)

Both options keep the hub on `HUB_BIND=127.0.0.1`, so nothing is exposed on your LAN or the internet.

## Tailscale

[Tailscale](https://tailscale.com) builds a private network (a *tailnet*) between your devices. `tailscale serve` publishes the hub **only inside that network**, with real HTTPS.

### On the server

Download and read the official script before running it:

```bash
curl -fsSLo /tmp/tailscale-install.sh https://tailscale.com/install.sh
less /tmp/tailscale-install.sh
sh /tmp/tailscale-install.sh
sudo tailscale up            # prints a link to authorize the machine in your account
```

In the [Tailscale admin console](https://login.tailscale.com/admin/dns), enable **MagicDNS** and **HTTPS Certificates**. Then:

```bash
sudo tailscale serve --bg 8084
tailscale serve status       # shows the address, e.g. https://server.your-tailnet.ts.net
```

### On your devices

Install the Tailscale app (Windows, macOS, Linux, Android, iOS), sign in with the same account and open the address shown above.

To turn it off: `sudo tailscale serve --https=443 off`.

> Do **not** use `tailscale funnel`: it publishes to the open internet, for anyone.
> If other people share your tailnet, restrict who can reach the server with Tailscale *Access controls (ACLs)*, or also set `HUB_PASSWORD`.

## SSH tunnel

If you already log in to the server over SSH, there is nothing to install:

```bash
ssh -N -L 8084:127.0.0.1:8084 user@server-ip
```

While that command runs, open `http://localhost:8084` on your computer.

To avoid typing it every time, add this to `~/.ssh/config` on your computer:

```
Host server
    HostName 192.168.1.50          # server IP or name
    User user
    LocalForward 8084 127.0.0.1:8084
    LocalForward 9898 127.0.0.1:9898   # Backrest, if you use it
```

and run `ssh -N server`.

On a phone, SSH apps such as Termius (Android/iOS) or JuiceSSH (Android) support *port forwarding*: map local port 8084 → `127.0.0.1:8084`.

**SSH good practice:** log in with a key (`ssh-keygen -t ed25519` and `ssh-copy-id`), disable passwords (`PasswordAuthentication no` in `/etc/ssh/sshd_config.d/`) and do not expose port 22 to the internet. Use Tailscale to reach SSH from outside.
