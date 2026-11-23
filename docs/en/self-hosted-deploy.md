# Automatic deploys from GitHub (private forks only)

**English** · [Português](../pt-BR/deploy-self-hosted.md) · [← README](../../README.md)

If you keep your copy of the hub in a **private** repository and want every `git push` to `main` to update the server, you can use a GitHub Actions *self-hosted runner*.

> ⚠️ **Never do this on a public repository.** A self-hosted runner executes the workflow's code on your server.
> On a public repository, anyone can open a pull request with code that runs on your machine. GitHub itself recommends against self-hosted runners on public repositories.
> That is why this repository does **not** ship this workflow enabled: it only lives in this document.

## 1. Keep the config outside the runner folder

`actions/checkout` deletes untracked files on every run, including `.env` and `data/`. Keep both outside the runner folder:

```bash
mkdir -p ~/.config/ubuntu-server-hub ~/hub-data
cp .env.example ~/.config/ubuntu-server-hub/.env
chmod 600 ~/.config/ubuntu-server-hub/.env
nano ~/.config/ubuntu-server-hub/.env      # HUB_DATA_PATH=/home/<you>/hub-data (absolute path)
```

## 2. Runner

On GitHub: your repository → **Settings → Actions → Runners → New self-hosted runner → Linux**. Follow the page's commands in a dedicated folder (e.g. `~/actions-runner`) and install the service as **your user**, not root:

```bash
sudo ./svc.sh install "$USER"
sudo ./svc.sh start
```

Your user must be in the `docker` group. Remember that this is equivalent to root on the host, so what protects the server is the repository being private.

## 3. Workflow

Create `.github/workflows/deploy.yml` **in your private fork**:

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
      - name: Local config
        run: install -m 600 "$HOME/.config/ubuntu-server-hub/.env" .env
      - name: Deploy
        run: ./install.sh
      - name: Prune old images
        if: success()
        run: docker image prune -f
```

After the first deploy, check it with `docker compose -p hub ps`.
