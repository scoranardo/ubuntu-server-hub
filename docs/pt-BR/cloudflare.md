# Cloudflare Tunnel + Access: hub na internet, com login

[English](../en/cloudflare.md) · **Português** · [← README](../../README.pt-BR.md)

Com isso, o hub fica em `https://hub.seu-dominio.com`, com HTTPS, **sem abrir nenhuma porta no roteador** e com login pelo Cloudflare antes de qualquer pessoa ver a página.

```
navegador ──► Cloudflare (Access: login) ──► túnel ──► cloudflared no servidor ──► 127.0.0.1:8084 (hub)
```

**Você precisa de:** um domínio com o DNS no Cloudflare (plano grátis serve) e o Zero Trust ativado (plano Free, até 50 usuários).

> Os menus do painel do Cloudflare mudam de nome de tempos em tempos. Se não achar algo, procure pelo nome em inglês (*Tunnels*, *Access → Applications*).

## Ordem importa: crie o Access **antes** do endereço público

Se você publicar o endereço antes de criar o login, o hub fica aberto para a internet nesse intervalo. Então siga esta ordem: 1 → 2 → 3.

## 1. Aplicação no Access (o login)

Zero Trust → **Access → Applications → Add an application → Self-hosted**:

- **Application name:** `hub`
- **Domain:** `hub` · `seu-dominio.com`. Se já existir uma aplicação para `*.seu-dominio.com`, ela já protege o hub.
- **Session duration:** ex.: 24 h
- **Policy:** nome `só eu`, Action **Allow**, Include → **Emails** → o seu e-mail. Use **Emails ending in** para liberar um domínio inteiro, ou adicione outros e-mails.
- **Login methods:** *One-time PIN* (código por e-mail) já funciona. Google/GitHub são opcionais.

## 2. Túnel e `cloudflared`

Zero Trust → **Networks → Tunnels → Create a tunnel → Cloudflared**, dê um nome (ex.: `servidor`) e escolha **Debian / Ubuntu**.
A página mostra os comandos para instalar o `cloudflared` e um `sudo cloudflared service install <TOKEN>`. Rode-os no servidor.

> O `<TOKEN>` dá acesso ao seu túnel. Não cole em chat, issue ou commit. Se vazar, gere outro no painel.

Confira:

```bash
systemctl status cloudflared --no-pager
```

O túnel deve aparecer como **Healthy** no painel.

## 3. Endereço público

No túnel → **Public hostname → Add a public hostname**:

- **Subdomain:** `hub` · **Domain:** `seu-dominio.com`
- **Service:** `HTTP` → `localhost:8084`

Teste numa **aba anônima**: `https://hub.seu-dominio.com` deve pedir o login do Access antes de mostrar o painel.

O hub continua com `HUB_BIND=127.0.0.1`, porque o `cloudflared` roda no próprio servidor e fala com `localhost`.
Se o seu `cloudflared` roda em Docker, ele precisa de `network_mode: host` para alcançar `localhost:8084`.

## 4. (Recomendado) O hub confere o login também

Defesa em profundidade: o hub valida o token (JWT) que o Access envia em cada requisição. Assim, mesmo que alguém consiga falar direto com a porta 8084, recebe **403**.

1. **Team domain:** Zero Trust → **Settings → Custom pages** (ou *Team name and domain*). Vai ser algo como `minha-equipe.cloudflareaccess.com`.
2. **AUD:** Access → Applications → `hub` → **Configure → Overview → Application Audience (AUD) Tag**.
3. No `.env`:

   ```
   CF_ACCESS_TEAM_DOMAIN=minha-equipe.cloudflareaccess.com
   CF_ACCESS_AUD=<a tag AUD, um texto longo em hexadecimal>
   ```

4. `./install.sh`

Teste no servidor: `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8084/` deve responder `403`, e `/healthz` continua `200`.
A partir daí, você só entra pelo endereço do Cloudflare. Para acessar sem internet, tire as duas variáveis do `.env`.

Com o Access ligado, **não precisa** de `HUB_PASSWORD`, senão você faria login duas vezes.

## Problemas comuns

| Sintoma | Causa provável |
|---|---|
| 502 / *Bad gateway* | hub fora do ar (`docker compose ps`) ou o Public hostname aponta para a porta errada |
| 403 no hub depois do login | `CF_ACCESS_AUD` ou `CF_ACCESS_TEAM_DOMAIN` errados no `.env` |
| a página abre sem pedir login | a aplicação do Access não cobre esse subdomínio: confira o passo 1 |
