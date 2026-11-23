# Backrest: backups com alerta no hub

[English](../en/backrest.md) · **Português** · [← README](../../README.pt-BR.md)

O [Backrest](https://github.com/garethgeorge/backrest) é uma interface web para o [restic](https://restic.net), que faz backups criptografados, com desduplicação e versões.
Este tutorial instala o Backrest, cria o primeiro backup e liga o cartão **Backups e discos** do hub.

Antes de começar, três conceitos:

| | |
|---|---|
| **Repositório** | onde os backups ficam guardados: um HD externo, outro servidor, ou um bucket (Backblaze B2, S3, rclone…). É protegido por uma **senha** |
| **Plano** | *o que* copiar (pastas), *quando* (agenda) e *por quanto tempo* manter (retenção) |
| **Snapshot** | uma "foto" das pastas num momento. Você restaura a partir de um snapshot |

## 1. Instalar

O script oficial instala o binário em `/usr/local/bin` e cria um serviço systemd que roda como **o seu usuário**, escutando só em `127.0.0.1:9898`.
Por segurança, baixe e leia o script antes de executar:

```bash
curl -fsSLo /tmp/backrest-install.sh https://raw.githubusercontent.com/garethgeorge/backrest/main/install.sh
less /tmp/backrest-install.sh
bash /tmp/backrest-install.sh
```

> Para copiar arquivos de outros usuários ou do sistema (ex.: `/etc`, `/var/lib/docker/volumes`), o Backrest precisa rodar como root.
> Nesse caso, use `bash /tmp/backrest-install.sh -- --root`. Se só vai copiar pastas suas, prefira o padrão (seu usuário).

Confira:

```bash
systemctl status 'backrest*' --no-pager
curl -sI http://127.0.0.1:9898 | head -1
```

## 2. Abrir a interface

Assim como o hub, o Backrest escuta só na própria máquina. Do seu computador, abra um túnel SSH:

```bash
ssh -N -L 9898:127.0.0.1:9898 usuario@ip-do-servidor
```

e acesse `http://localhost:9898`. Também dá para usar Tailscale ou Cloudflare: veja [acesso-remoto.md](acesso-remoto.md) e [cloudflare.md](cloudflare.md), trocando a porta 8084 por 9898.

No primeiro acesso, o Backrest pede um **nome da instância** (ex.: o hostname) e um **usuário e senha** para a interface. Crie os dois.

## 3. Criar o repositório

**Add Repo**:

- **Repo name:** ex.: `hd-externo`
- **Repository URI:** onde guardar. Exemplos:
  - disco local/externo montado: `/mnt/backup/restic`
  - Backblaze B2: `b2:nome-do-bucket:/servidor`
  - SFTP em outra máquina: `sftp:usuario@outra-maquina:/backups/servidor`
- **Password:** gere uma senha forte (`openssl rand -base64 32`).

> ⚠️ **Guarde a senha do repositório FORA do servidor** (gerenciador de senhas). Se o servidor morrer e você perder a senha, os backups não podem ser abertos por ninguém, nem por você.

Para nuvem, preencha as variáveis de ambiente pedidas (ex.: `B2_ACCOUNT_ID` e `B2_ACCOUNT_KEY`) no próprio formulário do repositório.

## 4. Criar o plano

**Add Plan**:

- **Plan name:** ex.: `documentos`. O hub usa esse nome no alerta.
- **Repository:** o que você criou.
- **Paths:** as pastas, ex.: `/home/usuario/documentos`, `/home/usuario/stack`.
- **Excludes:** o que pular, ex.: `**/node_modules`, `**/.cache`, `*.tmp`.
- **Schedule:** ex.: todo dia às 3h, que em cron é `0 3 * * *`.
- **Retention:** ex.: manter 7 diários, 4 semanais e 6 mensais.

Salve e clique em **Backup Now** para rodar o primeiro.

## 5. Avisar o hub

### 5.1 Instalar o `hub-notify` (uma vez)

Na pasta do hub:

```bash
sudo install -m 755 scripts/hub-notify /usr/local/bin/hub-notify
echo "HUB_TOKEN_FILE=$PWD/data/notify_token" | sudo tee /etc/hub-notify.env
hub-notify teste:backrest ok "Teste" "hub-notify funcionando"
```

O item "Teste" deve aparecer no cartão **Backups e discos**. Depois, remova-o com **✕**.

> O `hub-notify` precisa ler o `data/notify_token`. Esse arquivo é do seu usuário (permissão `600`), e o Backrest roda como seu usuário ou como root. Nos dois casos ele consegue ler.

### 5.2 Criar os hooks no plano

Abra o plano → **Hooks** → **Add Hook** → tipo **Command**. Crie três hooks.

**Sucesso.** Condition `CONDITION_SNAPSHOT_SUCCESS`:

```bash
/usr/local/bin/hub-notify "backup:{{ .Plan.Id }}" ok "Backup {{ .Plan.Id }}" "backup ok" 26
```

O `26` é o `esperado_horas`: se passar 26 h sem um backup ok (agenda diária + 2 h de folga), o item fica **atrasado** no hub.
Para um plano semanal, use `170`.

**Aviso.** Condition `CONDITION_SNAPSHOT_WARNING`:

```bash
/usr/local/bin/hub-notify "backup:{{ .Plan.Id }}" aviso "Backup {{ .Plan.Id }}" "backup com avisos (alguns arquivos não puderam ser lidos)"
```

**Erro.** Conditions `CONDITION_SNAPSHOT_ERROR` e `CONDITION_ANY_ERROR`:

```bash
/usr/local/bin/hub-notify "backup:{{ .Plan.Id }}" erro "Backup {{ .Plan.Id }}" {{ .ShellEscape .Error }}
```

Se a sua versão do Backrest reclamar do template `{{ .ShellEscape .Error }}`, troque por um texto fixo, como `"falhou, veja o Backrest"`.
As variáveis disponíveis estão na documentação do Backrest (*Hooks* e *Command hook examples*), em [garethgeorge.github.io/backrest](https://garethgeorge.github.io/backrest/).

Salve o plano e rode **Backup Now**. O item `Backup documentos` deve aparecer como **ok**.

### Backrest em Docker?

Se o seu Backrest roda em container, o hook roda **dentro do container**, que não tem o `hub-notify`.
O caminho mais simples é usar a instalação nativa acima. Se quiser manter o container:

1. adicione `network_mode: host` ao serviço (para alcançar `127.0.0.1:8084`);
2. monte o token só para leitura: `- /caminho/do/hub/data/notify_token:/hub_token:ro`;
3. confira se a imagem tem `curl`: `docker exec backrest which curl`;
4. use um hook assim:

```bash
curl -fsS -X POST http://127.0.0.1:8084/api/eventos -H "X-Hub-Token: $(cat /hub_token)" -H 'Content-Type: application/json' -d '{"chave": "backup:{{ .Plan.Id }}", "nivel": "ok", "titulo": "Backup {{ .Plan.Id }}", "evento": "backup ok", "esperado_horas": 26}'
```

## 6. Teste a restauração (sério)

Um backup que nunca foi restaurado não é um backup. Uma vez por mês:

1. no Backrest, abra o plano → **Snapshots**, escolha um e clique em **Browse**;
2. selecione uma pasta → **Restore to path** → por exemplo, `/tmp/teste-restore`;
3. confira os arquivos e apague a pasta depois.

Regra 3-2-1: **3** cópias, em **2** mídias diferentes, **1** fora de casa (nuvem ou casa de alguém). Um repositório local mais um na nuvem já cumpre a regra.
