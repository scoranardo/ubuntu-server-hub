# Deploy automático pelo GitHub (só para fork privado)

[English](../en/self-hosted-deploy.md) · **Português** · [← README](../../README.pt-BR.md)

Se você mantém a sua versão do hub num repositório **privado** e quer que cada `git push` na `main` atualize o servidor sozinho, dá para usar um *runner self-hosted* do GitHub Actions.

> ⚠️ **Nunca faça isso num repositório público.** Um runner self-hosted executa o código do workflow no seu servidor.
> Num repositório público, qualquer pessoa pode abrir um pull request com código que roda na sua máquina. O próprio GitHub desaconselha runners self-hosted em repositórios públicos.
> Por isso este repositório **não** traz esse workflow ligado: ele está só neste documento.

## 1. Configuração fora da pasta do runner

O `actions/checkout` apaga arquivos não versionados a cada execução, inclusive `.env` e `data/`. Então guarde os dois fora da pasta do runner:

```bash
mkdir -p ~/.config/ubuntu-server-hub ~/hub-data
cp .env.example ~/.config/ubuntu-server-hub/.env
chmod 600 ~/.config/ubuntu-server-hub/.env
nano ~/.config/ubuntu-server-hub/.env      # HUB_DATA_PATH=/home/<você>/hub-data (caminho absoluto)
```

## 2. Runner

No GitHub: seu repositório → **Settings → Actions → Runners → New self-hosted runner → Linux**. Siga os comandos da página numa pasta própria (ex.: `~/actions-runner`) e instale o serviço com **o seu usuário**, não root:

```bash
sudo ./svc.sh install "$USER"
sudo ./svc.sh start
```

O seu usuário precisa estar no grupo `docker`. Lembre que isso equivale a root no host, então o que protege o servidor é o repositório ser privado.

## 3. Workflow

Crie `.github/workflows/deploy.yml` **no seu fork privado**:

```yaml
name: Deploy
on:
  push:
    branches: [main]
  workflow_dispatch:
concurrency:
  group: deploy-hub
  cancel-in-progress: false
permissions:
  contents: read
jobs:
  deploy:
    runs-on: [self-hosted, linux]
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
      - name: Configuração local
        run: install -m 600 "$HOME/.config/ubuntu-server-hub/.env" .env
      - name: Subir
        run: ./install.sh
      - name: Limpar imagens antigas
        if: success()
        run: docker image prune -f
```

Depois do primeiro deploy, confira com `docker compose -p hub ps`.
