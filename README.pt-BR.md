# ubuntu-server-hub

[English](README.md) · **Português (Brasil)**

Painel de uma página e lançador de apps para um **Ubuntu Server** de home lab, feito em **Flask + HTMX + Tailwind** e rodando em Docker.
Clone, rode um script e você tem as métricas ao vivo da **sua** máquina e um grid de atalhos para os serviços que você hospeda.

> Tudo o que aparece na tela é lido da máquina onde o hub está rodando. Nada de nenhum servidor específico vem gravado no código.

| Cartão | O que mostra | De onde vem |
|---|---|---|
| **Aplicações** | seus atalhos (começa **vazio**), com bolinha online/offline | `data/apps.json`, gerenciado pela tela |
| **Processador** | uso por núcleo, usr/sys/iowait, carga, frequência, temperatura, potência (RAPL), gráficos de 3 min | `/proc`, `/sys` (como `htop` / `s-tui`) |
| **Memória** | RAM (usada / cache / livre), swap | `/proc/meminfo` |
| **Armazenamento** | discos, partições, uso, leitura/escrita | `/sys/block`, montagens do host |
| **Rede** | interfaces, IP, tráfego | `/proc/net` |
| **Sistema** | hostname, modelo, SO, kernel, tempo ligado, pacotes, CPU (como `fastfetch`) | `/etc/os-release`, DMI, `/proc/cpuinfo` |
| **Processos** | top 15 por CPU ou memória | `/proc` do host |
| **Atualizações** | `apt` pendente (segurança primeiro) e aviso de reboot | banco do apt/dpkg do host |
| **Backups e discos** | alertas do [Backrest](docs/pt-BR/backrest.md) e do [smartd](docs/pt-BR/smartd.md) | `POST /api/eventos` |
| **Docker** | todos os containers, status/health, portas, projeto do compose, CPU/RAM | socket do Docker |
| **Instalados por você** | pacotes apt instalados manualmente, snaps, `/usr/local/bin`, `/opt` | dpkg, histórico do apt |

Os blocos podem ser movidos e redimensionados (**▦ Layout**). No celular, tudo vira uma coluna.

---

## Sumário

- [Requisitos](#requisitos)
- [Instalação rápida](#instalação-rápida)
- [Configuração (`.env`)](#configuração-env)
- [Acessar de outros dispositivos](#acessar-de-outros-dispositivos)
- [Atalhos (cartão Aplicações)](#atalhos-cartão-aplicações)
- [Alertas de backup e discos](#alertas-de-backup-e-discos)
- [Opcionais](#opcionais)
- [Segurança](#segurança)
- [Dia a dia](#dia-a-dia)
- [Desenvolvimento](#desenvolvimento)
- [Estrutura](#estrutura)

---

## Requisitos

| Obrigatório | Por quê |
|---|---|
| **Ubuntu Server** 22.04, 24.04 ou 26.04 (x86_64 ou arm64) | a imagem usa a **mesma versão do Ubuntu** do host, para o `apt` do container conseguir ler o banco de pacotes do host |
| **Docker Engine + plugin Compose** | o hub é um container |
| um usuário comum no grupo **`docker`** | o `install.sh` não roda como root |
| `git`, `curl` | para clonar e conferir se o hub subiu |

Instale o Docker pelo repositório apt oficial ([docs.docker.com/engine/install/ubuntu](https://docs.docker.com/engine/install/ubuntu/)) e depois:

```bash
sudo usermod -aG docker "$USER"   # depois saia e entre de novo na sessão
docker run --rm hello-world       # confere que funciona sem sudo
```

| Opcional | Para quê | Tutorial |
|---|---|---|
| [Backrest](https://github.com/garethgeorge/backrest) (interface web do restic) | status dos backups em **Backups e discos** | [docs/pt-BR/backrest.md](docs/pt-BR/backrest.md) |
| `smartmontools` (smartd) | alertas de saúde dos discos em **Backups e discos** | [docs/pt-BR/smartd.md](docs/pt-BR/smartd.md) |
| Cloudflare Tunnel + Access | acesso de qualquer lugar por HTTPS, com login | [docs/pt-BR/cloudflare.md](docs/pt-BR/cloudflare.md) |
| Tailscale ou SSH | acesso privado sem abrir portas | [docs/pt-BR/acesso-remoto.md](docs/pt-BR/acesso-remoto.md) |
| módulo `drivetemp`, permissão do RAPL | temperatura dos discos, potência da CPU em watts | [Opcionais](#opcionais) |

`htop`, `s-tui` e `fastfetch` **não** são necessários: o hub lê os mesmos dados do kernel que eles leem.

## Instalação rápida

```bash
git clone https://github.com/scoranardo/ubuntu-server-hub.git
cd ubuntu-server-hub
./install.sh
```

O `install.sh`:

1. cria o `.env` a partir do `.env.example` (permissão `600`), se ainda não existir;
2. preenche a versão do Ubuntu, o seu UID/GID, o seu usuário e o grupo do socket do Docker;
3. cria `./data` (atalhos, layout, alertas, token de notificação);
4. roda `docker compose up -d --build` e espera o `/healthz` responder.

Pode rodar de novo quando quiser (ex.: depois de um `git pull`). O seu `.env` e a pasta `data/` são mantidos.

Por padrão o hub escuta **só em `127.0.0.1:8084`**. No próprio servidor, teste com `curl -s http://127.0.0.1:8084/healthz`.
Para abrir no notebook ou no celular, veja [Acessar de outros dispositivos](#acessar-de-outros-dispositivos).

## Configuração (`.env`)

Toda configuração local fica no `.env`, que está no `.gitignore`. Edite e rode `./install.sh` de novo (ou `docker compose up -d`).

| Variável | Padrão | |
|---|---|---|
| `HUB_TITLE` | hostname da máquina | nome grande no canto superior esquerdo |
| `HUB_SUBTITLE` | modelo do computador (DMI) | linha pequena embaixo do nome |
| `HUB_HOST_MODEL` | detectado | sobrescreve o modelo no cartão **Sistema** |
| `HUB_CPU_NAME` | detectado em `/proc/cpuinfo` | sobrescreve o nome do processador |
| `HUB_BIND` | `127.0.0.1` | endereço em que escuta. `0.0.0.0` = rede toda (**exige** `HUB_PASSWORD`) |
| `HUB_PORT` | `8084` | porta |
| `HUB_USER` / `HUB_PASSWORD` | `admin` / vazio | login opcional (HTTP Basic) |
| `HUB_ALLOW_NO_AUTH` | vazio | `1` permite `HUB_BIND` fora da máquina **sem** senha (não recomendado) |
| `CF_ACCESS_TEAM_DOMAIN` / `CF_ACCESS_AUD` | vazio | valida o JWT do Cloudflare Access ([tutorial](docs/pt-BR/cloudflare.md)) |
| `HUB_ICON_CDN` | dashboard-icons no jsDelivr | fonte dos ícones (`/tree.json` + `/svg/<nome>.svg`) |
| `HUB_DATA_PATH` | `./data` | onde o hub guarda os dados |
| `UBUNTU_VERSION`, `HUB_UID`, `HUB_GID`, `HUB_HOST_USER`, `DOCKER_GID` | preenchidos pelo `install.sh` | não edite à mão |

O hostname, o modelo e o processador são **detectados automaticamente**. Só use essas variáveis se quiser um nome diferente.
Alguns fabricantes deixam lixo no DMI, como "To be filled by O.E.M.". Nesse caso o hub mostra o hostname, e você pode corrigir com `HUB_SUBTITLE`.

## Acessar de outros dispositivos

Escolha uma opção. Elas estão em ordem de recomendação:

| Opção | Quem alcança | Login | Tutorial |
|---|---|---|---|
| **Tailscale** (`tailscale serve`) | só os seus dispositivos, HTTPS | sua conta Tailscale | [acesso-remoto.md](docs/pt-BR/acesso-remoto.md#tailscale) |
| **Cloudflare Tunnel + Access** | internet, HTTPS, sem abrir portas | e-mail / SSO no Cloudflare | [cloudflare.md](docs/pt-BR/cloudflare.md) |
| **Túnel SSH** | só você, nada para instalar | sua chave SSH | [acesso-remoto.md](docs/pt-BR/acesso-remoto.md#túnel-ssh) |
| **Rede local** (`HUB_BIND=0.0.0.0` + `HUB_PASSWORD`) | todos na sua rede, HTTP sem criptografia | senha | abaixo |

Modo rede local:

```bash
openssl rand -base64 24     # gera uma senha forte
nano .env                   # HUB_BIND=0.0.0.0 e HUB_PASSWORD=<senha>
./install.sh
```

Depois abra `http://<ip-do-servidor>:8084`. Sem HTTPS, a senha passa legível pela rede, então use isso só numa rede em que você confia.
Se o servidor usa `ufw`, rode também `sudo ufw allow from 192.168.0.0/16 to any port 8084 proto tcp` (ajuste para a sua sub-rede).
**Nunca** redirecione a porta 8084 no roteador.

## Atalhos (cartão Aplicações)

A lista começa **vazia**. **+ Novo** cria um atalho e **✎ Organizar** liga os botões de mover, editar e remover.
Os atalhos ficam em `data/apps.json`:

```json
[
  {"id": "a1b2c3d4", "name": "Jellyfin", "url": "https://jellyfin.exemplo.com", "icon": "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/jellyfin.svg",
   "description": "Mídia", "group": "Mídia", "check_url": "http://127.0.0.1:8096"}
]
```

- **Ícone:** busque pelo nome no formulário (os ícones são do [dashboard-icons](https://github.com/homarr-labs/dashboard-icons)). Também dá para digitar um emoji, colar a URL de uma imagem ou **⬆ Enviar** uma imagem sua em PNG/JPG/GIF/WEBP/ICO/SVG de até 512 KB, que fica em `data/icons/`.
- **URL interna (status):** a bolinha verde/vermelha consulta esse endereço. Use o endereço local (`http://127.0.0.1:<porta>`): um endereço público atrás de login sempre responde, mesmo com o app fora do ar.
- Teclado: `/` busca, `Enter` abre o primeiro resultado, `Esc` limpa a busca.

## Alertas de backup e discos

O cartão **Backups e discos** mostra o último evento de cada *chave*, com os problemas primeiro. Um item que deveria avisar dentro de `esperado_horas` e não avisou fica **atrasado**.
**✓** marca um erro como resolvido, e **✕** esquece a chave.

Outros programas do servidor enviam eventos com `POST /api/eventos` e o cabeçalho `X-Hub-Token`.
O token é criado na primeira execução em `data/notify_token` (permissão `600`). Trate-o como uma senha.

O jeito mais fácil de enviar é o `scripts/hub-notify` que vem no projeto:

```bash
sudo install -m 755 scripts/hub-notify /usr/local/bin/hub-notify
echo "HUB_TOKEN_FILE=$PWD/data/notify_token" | sudo tee /etc/hub-notify.env
hub-notify teste:manual ok "Teste" "funcionou"
```

<details>
<summary>API HTTP direta</summary>

```bash
printf 'X-Hub-Token: %s\n' "$(cat data/notify_token)" | \
  curl -s -XPOST http://127.0.0.1:8084/api/eventos -H @- -H 'Content-Type: application/json' \
  -d '{"chave": "teste:manual", "titulo": "Teste", "nivel": "ok", "evento": "funcionou"}'
```

| Campo | |
|---|---|
| `chave` (obrigatório) | identifica o item, ex.: `backup:fotos`, `disco:/dev/sda` |
| `nivel` | `ok`, `aviso` ou `erro`. Sem ele, o nível é deduzido de `evento`/`erro` |
| `titulo`, `evento`, `mensagem`, `erro`, `motivo`, `origem` | textos exibidos |
| `esperado_horas` | intervalo esperado em horas. Se o próximo evento não chegar no prazo, o item fica *atrasado* |
| `estatisticas`, `snapshot`, `duracao` | detalhes do backup (campos do resumo do restic: `data_added`, `total_bytes_processed`, …) |

Os últimos 300 eventos ficam em `data/eventos.json`. Essa rota não passa pela senha nem pela validação do Cloudflare, porque é chamada por scripts locais, mas sempre exige o token.
</details>

Tutoriais passo a passo: **[Backrest](docs/pt-BR/backrest.md)** · **[smartd](docs/pt-BR/smartd.md)**.

## Opcionais

**Potência da CPU (RAPL, Intel):** por padrão o kernel só deixa o root ler o contador de energia. Para liberar a leitura para todos:

```bash
echo 'z /sys/class/powercap/intel-rapl:0/energy_uj 0444 - - -' | sudo tee /etc/tmpfiles.d/rapl.conf
sudo systemd-tmpfiles --create /etc/tmpfiles.d/rapl.conf
```

O contador é restrito por causa do ataque de canal lateral *Platypus*. Num servidor caseiro de um usuário só o risco é baixo, mas a decisão é sua.

**Temperatura dos discos:**

```bash
sudo modprobe drivetemp && echo drivetemp | sudo tee /etc/modules-load.d/drivetemp.conf
```

## Segurança

Leia isto antes de expor o hub em qualquer lugar.

- **Na prática, o container é privilegiado.** Ele monta o `/var/run/docker.sock`, e quem fala com o socket do Docker consegue virar root no host. Ele também enxerga os processos (`pid: host`), a rede (`network_mode: host`) e o disco inteiro do host **somente leitura** (`/:/host:ro`).
  Para reduzir o risco, o processo roda como um usuário sem privilégios com o seu UID, o sistema de arquivos do container é `read_only`, todas as capabilities são removidas e `no-new-privileges` está ligado.
- **Por isso, trate o painel como um console de administração.** Ele escuta em `127.0.0.1` por padrão e se recusa a subir em outro endereço sem senha ou Cloudflare Access.
- **O que a página revela:** linhas de comando dos processos, usuários, IPs, programas instalados e nomes de containers. Só mostre para quem você daria um shell.
- **Seus dados ficam fora do git:** `.env` e `data/` (atalhos, token, alertas, layout) estão no `.gitignore` e no `.dockerignore`. Se o seu fork for público, rode `git status` antes de cada commit e nunca use `git add -f` nesses caminhos.
- **Sem telemetria.** As únicas requisições para fora são a lista de ícones (`HUB_ICON_CDN`, uma vez por dia) e a checagem de status das URLs que você cadastrar. O navegador carrega as imagens dos ícones direto do CDN.

Achou uma vulnerabilidade? Abra um *security advisory* privado no GitHub (**Security → Report a vulnerability**) em vez de uma issue pública.

## Dia a dia

```bash
docker compose ps                 # status
docker compose logs -f hub        # logs
git pull && ./install.sh          # atualizar
docker compose down               # parar (a pasta data/ é mantida)
```

## Desenvolvimento

O CSS já vem compilado em `static/app.css`. Se mudar classes Tailwind nos templates, recompile:

```bash
npx @tailwindcss/cli@4 -i assets/app.css -o static/app.css --minify
```

Para rodar sem Docker, lendo a própria máquina:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python app.py      # http://127.0.0.1:8084
```

Quer deploy automático a partir do seu fork? Veja [docs/pt-BR/deploy-self-hosted.md](docs/pt-BR/deploy-self-hosted.md). Isso só é seguro em fork **privado**.

## Estrutura

```
app.py              rotas Flask (página, partials HTMX, CRUD de atalhos, alertas, login opcional)
alertas.py          alertas recebidos em /api/eventos: token, armazenamento e estado do cartão
collectors.py       coleta: thread de métricas a cada 2 s, Docker via socket, apt/dpkg do host via /host
templates/          index.html + partials/ (cada cartão é um partial atualizado pelo HTMX)
static/             app.css (compilado) e htmx.min.js 2.0.4 (servido localmente)
assets/app.css      fonte do Tailwind
scripts/            hub-notify (envia alertas), smartd-hub-notify (smartd → hub)
install.sh          cria o .env, monta a imagem e sobe o container
Dockerfile          ubuntu:<versão do host> + venv + gunicorn
compose.yaml        container "hub" (rede/pid do host, / do host somente leitura)
.env.example        todas as configurações, comentadas
docs/en, docs/pt-BR tutoriais passo a passo
```

## Licença

[MIT](LICENSE)
