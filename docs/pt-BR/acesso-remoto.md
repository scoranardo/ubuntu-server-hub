# Acesso remoto privado: Tailscale ou túnel SSH

[English](../en/remote-access.md) · **Português** · [← README](../../README.pt-BR.md)

As duas opções mantêm o hub em `HUB_BIND=127.0.0.1`: nada fica exposto na rede local nem na internet.

## Tailscale

O [Tailscale](https://tailscale.com) cria uma rede privada (*tailnet*) entre os seus dispositivos. O `tailscale serve` publica o hub **só dentro dessa rede**, com HTTPS de verdade.

### No servidor

Baixe e leia o script oficial antes de rodar:

```bash
curl -fsSLo /tmp/tailscale-install.sh https://tailscale.com/install.sh
less /tmp/tailscale-install.sh
sh /tmp/tailscale-install.sh
sudo tailscale up            # mostra um link para autorizar a máquina na sua conta
```

No [painel do Tailscale](https://login.tailscale.com/admin/dns), ative **MagicDNS** e **HTTPS Certificates**. Depois:

```bash
sudo tailscale serve --bg 8084
tailscale serve status       # mostra o endereço, ex.: https://servidor.nome-da-tailnet.ts.net
```

### Nos seus dispositivos

Instale o app do Tailscale (Windows, macOS, Linux, Android, iOS), entre com a mesma conta e abra o endereço mostrado.

Para desligar: `sudo tailscale serve --https=443 off`.

> **Não** use `tailscale funnel`: ele publica na internet aberta, para qualquer pessoa.
> Se outras pessoas usam a sua tailnet, limite quem alcança o servidor pelas *Access controls (ACLs)* do Tailscale, ou ligue também o `HUB_PASSWORD`.

## Túnel SSH

Se você já entra no servidor por SSH, não precisa instalar nada:

```bash
ssh -N -L 8084:127.0.0.1:8084 usuario@ip-do-servidor
```

Enquanto esse comando estiver rodando, abra `http://localhost:8084` no seu computador.

Para não digitar tudo toda vez, adicione ao `~/.ssh/config` do seu computador:

```
Host servidor
    HostName 192.168.1.50          # IP ou nome do servidor
    User usuario
    LocalForward 8084 127.0.0.1:8084
    LocalForward 9898 127.0.0.1:9898   # Backrest, se usar
```

e rode `ssh -N servidor`.

No celular, apps de SSH como Termius (Android/iOS) ou JuiceSSH (Android) têm *port forwarding*: configure a porta local 8084 → `127.0.0.1:8084`.

**Boas práticas de SSH:** entre com chave (`ssh-keygen -t ed25519` e `ssh-copy-id`), desligue a senha (`PasswordAuthentication no` em `/etc/ssh/sshd_config.d/`) e não exponha a porta 22 na internet. Use Tailscale para alcançar o SSH de fora.
