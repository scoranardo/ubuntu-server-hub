"""ubuntu-server-hub — dashboard do servidor + lançador de apps (Flask + HTMX + Tailwind)."""
import base64
import hmac
import ipaddress
import json
import logging
import os
import re
import threading
import time
import uuid
from urllib.parse import urlparse

from flask import Flask, abort, jsonify, render_template, request, send_from_directory, url_for

import alertas
import collectors as c

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("HUB_DATA_DIR", os.path.join(BASE, "data"))
APPS_FILE = os.path.join(DATA_DIR, "apps.json")
HIDDEN_FILE = os.path.join(DATA_DIR, "hidden_packages.json")
LAYOUT_FILE = os.path.join(DATA_DIR, "layout.json")
ICONS_DIR = os.path.join(DATA_DIR, "icons")  # ícones enviados pela tela
os.makedirs(ICONS_DIR, exist_ok=True)
avisos = alertas.Alertas(DATA_DIR)  # backup e discos (POST /api/eventos)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024  # 1 MB por requisição (só o upload de ícone usa corpo grande)
app.jinja_env.filters.update(bytes=c.fmt_bytes, duration=c.fmt_duration, ago=c.fmt_ago)


def spark(values, vmax=None, w=100, h=32):
    """Pontos SVG (linha + área) para mini-gráficos de histórico."""
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return {"line": "", "area": ""}
    vmax = vmax or max(vals) or 1
    step = w / max(c.HISTORY - 1, 1)
    offset = c.HISTORY - len(values)
    pts = [((offset + i) * step, h - min(v / vmax, 1) * (h - 2) - 1) for i, v in enumerate(values) if v is not None]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    return {"line": line, "area": f"{pts[0][0]:.1f},{h} {line} {pts[-1][0]:.1f},{h}"}


def asset(filename):
    """URL de arquivo estático com versão (mtime), para o navegador não usar CSS/JS antigo após um deploy."""
    try:
        v = int(os.path.getmtime(os.path.join(app.static_folder, filename)))
    except OSError:
        v = 0
    return url_for("static", filename=filename, v=v)


app.jinja_env.globals.update(level=c.level, spark=spark, asset=asset)

# Atalhos criados na primeira execução (data/apps.json). Começa vazio: cada um cadastra os seus pela tela.
DEFAULT_APPS = []


# --------------------------------------------------------------------------- segurança
_jwks = None
HUB_USER = os.environ.get("HUB_USER", "") or "admin"
HUB_PASSWORD = os.environ.get("HUB_PASSWORD", "")
CF_TEAM = os.environ.get("CF_ACCESS_TEAM_DOMAIN", "")  # ex.: minha-equipe.cloudflareaccess.com
CF_AUD = os.environ.get("CF_ACCESS_AUD", "")
OPEN_PATHS = ("/healthz", "/api/eventos")  # healthcheck do Docker e alertas (a rota de alertas exige o token)


def _is_loopback(host):
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return host == "localhost"


def check_exposure():
    """Não sobe escutando na rede sem nenhum login (senha ou Cloudflare Access)."""
    bind = os.environ.get("HUB_BIND", "127.0.0.1")
    if _is_loopback(bind) or HUB_PASSWORD or (CF_TEAM and CF_AUD):
        return
    if os.environ.get("HUB_ALLOW_NO_AUTH") == "1":
        logging.warning("HUB_BIND=%s sem senha e sem Cloudflare Access: qualquer um na rede abre o painel", bind)
        return
    raise SystemExit(f"HUB_BIND={bind} expõe o painel na rede sem login. Defina HUB_PASSWORD no .env "
                     "(ou CF_ACCESS_TEAM_DOMAIN + CF_ACCESS_AUD), ou use HUB_BIND=127.0.0.1. "
                     "Para ignorar este aviso: HUB_ALLOW_NO_AUTH=1.")


def _basic_auth_ok():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        return False
    try:
        user, _, pwd = base64.b64decode(auth[6:]).decode("utf-8").partition(":")
    except (ValueError, UnicodeDecodeError):
        return False
    ok_user = hmac.compare_digest(user.encode(), HUB_USER.encode())
    ok_pwd = hmac.compare_digest(pwd.encode(), HUB_PASSWORD.encode())
    return ok_user and ok_pwd


@app.before_request
def guard():
    """Login opcional (senha e/ou JWT do Cloudflare Access) + bloqueio de POST vindo de outro site."""
    if request.path in OPEN_PATHS:
        return
    if request.method == "POST":
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc != request.host:
            abort(403)
    if HUB_PASSWORD and not _basic_auth_ok():
        return "Login necessário", 401, {"WWW-Authenticate": 'Basic realm="hub", charset="UTF-8"'}
    if not CF_TEAM or not CF_AUD:
        return
    token = request.headers.get("Cf-Access-Jwt-Assertion") or request.cookies.get("CF_Authorization")
    if not token:
        abort(403)
    import jwt  # PyJWT[crypto]
    global _jwks
    if _jwks is None:
        _jwks = jwt.PyJWKClient(f"https://{CF_TEAM}/cdn-cgi/access/certs", cache_keys=True)
    try:
        key = _jwks.get_signing_key_from_jwt(token).key
        jwt.decode(token, key=key, audience=CF_AUD, algorithms=["RS256"], issuer=f"https://{CF_TEAM}")
    except Exception:
        abort(403)


@app.after_request
def security_headers(r):
    r.headers.setdefault("X-Content-Type-Options", "nosniff")
    r.headers.setdefault("X-Frame-Options", "DENY")
    r.headers.setdefault("Referrer-Policy", "no-referrer")
    return r


check_exposure()


# --------------------------------------------------------------------------- dados
_lock = threading.Lock()


def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_apps():
    apps = _load(APPS_FILE, None)
    if apps is None:
        apps = [{"id": uuid.uuid4().hex[:8], "check_url": "", **a} for a in DEFAULT_APPS]
        _save(APPS_FILE, apps)
    return apps


def grouped(apps):
    groups = {}
    for a in apps:
        groups.setdefault(a.get("group") or "Apps", []).append(a)
    return groups


def _app_from_form(existing=None):
    f = request.form
    name = f.get("name", "").strip()[:60]
    url = f.get("url", "").strip()
    if not name or not re.match(r"^https?://", url):
        abort(400, "Nome e URL (http/https) são obrigatórios")
    check = f.get("check_url", "").strip()
    if check and not re.match(r"^https?://", check):
        check = ""
    a = existing or {"id": uuid.uuid4().hex[:8]}
    a.update(name=name, url=url, icon=f.get("icon", "").strip()[:300],
             description=f.get("description", "").strip()[:120],
             group=f.get("group", "").strip()[:40] or "Apps", check_url=check)
    return a


def render_apps():
    apps = load_apps()
    return render_template("partials/apps.html", groups=grouped(apps), status=c.app_status(apps),
                           group_names=sorted(grouped(apps)))


# --------------------------------------------------------------------------- layout dos blocos
# Ordem e largura (1, 2 ou 3 colunas) de cada bloco abaixo de Aplicações, que fica fixo no topo.
PANELS = [  # (id, nome, largura padrão)
    ("cpu", "Processador", 2), ("mem", "Memória", 1),
    ("disks", "Armazenamento", 1), ("net", "Rede", 1), ("sys", "Sistema", 1),
    ("procs", "Processos", 2), ("updates", "Atualizações", 1),
    ("backups", "Backups e discos", 2), ("docker", "Docker", 3),
    ("installed", "Instalados por você", 2), ("keys", "Atalhos de teclado", 1),
]
PANEL_NAMES = {pid: name for pid, name, _ in PANELS}
SPAN = {1: "lg:col-span-1", 2: "lg:col-span-2", 3: "lg:col-span-3"}
app.jinja_env.globals.update(PANEL_NAMES=PANEL_NAMES, SPAN=SPAN)


def default_layout():
    return [{"id": pid, "size": size} for pid, _, size in PANELS]


def load_layout():
    """Layout salvo, ignorando blocos que não existem mais e acrescentando os novos no fim."""
    saved = _load(LAYOUT_FILE, None)
    if not isinstance(saved, list):
        return default_layout()
    defaults = {p["id"]: p for p in default_layout()}
    out, seen = [], set()
    for p in saved:
        pid = p.get("id") if isinstance(p, dict) else None
        if pid in defaults and pid not in seen:
            size = p.get("size")
            out.append({"id": pid, "size": size if size in (1, 2, 3) else defaults[pid]["size"]})
            seen.add(pid)
    return out + [p for pid, p in defaults.items() if pid not in seen]


@app.post("/layout")
def layout_save():
    items = []
    for part in request.form.get("order", "").split(",")[:50]:
        pid, _, size = part.partition(":")
        if pid in PANEL_NAMES and size in ("1", "2", "3"):
            items.append({"id": pid, "size": int(size)})
    if not items:
        abort(400)
    with _lock:
        _save(LAYOUT_FILE, items)
    return "", 204


@app.post("/layout/reset")
def layout_reset():
    with _lock:
        try:
            os.remove(LAYOUT_FILE)
        except FileNotFoundError:
            pass
    return "", 204


# --------------------------------------------------------------------------- páginas
def live_context():
    snap, hist = c.sampler.get()
    return {"s": snap, "h": hist, "sys": c.system_info(snap)}


@app.get("/")
def index():
    apps = load_apps()
    return render_template("index.html", **live_context(), oob=False, groups=grouped(apps),
                           status={}, initial=True, group_names=sorted(grouped(apps)), layout=load_layout(),
                           procs=c.top_processes(c.sampler.get()[0]), sort="cpu")


@app.get("/partials/live")
def live():
    return render_template("partials/live.html", **live_context(), oob=True)


@app.get("/partials/processes")
def processes():
    sort = request.args.get("sort", "cpu")
    snap, _ = c.sampler.get()
    return render_template("partials/processes.html", s=snap, procs=c.top_processes(snap, sort), sort=sort)


@app.get("/partials/docker")
def docker():
    return render_template("partials/docker.html", d=c.docker_containers())


@app.route("/partials/updates", methods=["GET", "POST"])
def updates():
    return render_template("partials/updates.html", u=c.updates(force=request.method == "POST"))


@app.get("/partials/installed")
def installed():
    hidden = set(_load(HIDDEN_FILE, []))
    items = [i for i in c.installed() if i["key"] not in hidden]
    return render_template("partials/installed.html", items=items, hidden=len(hidden))


@app.post("/installed/hide")
def hide_package():
    key = request.form.get("key", "")[:200]
    with _lock:
        hidden = set(_load(HIDDEN_FILE, []))
        hidden.add(key)
        _save(HIDDEN_FILE, sorted(hidden))
    return ""


@app.post("/installed/unhide-all")
def unhide_all():
    with _lock:
        _save(HIDDEN_FILE, [])
    return installed()


# --------------------------------------------------------------------------- atalhos
@app.get("/partials/apps")
def apps_partial():
    return render_apps()


@app.get("/apps/form")
@app.get("/apps/<aid>/form")
def app_form(aid=None):
    apps = load_apps()
    a = next((x for x in apps if x["id"] == aid), None) if aid else None
    return render_template("partials/app_form.html", a=a, group_names=sorted(grouped(apps)))


@app.get("/icons/search")
def icons_search():
    q = request.args.get("icon_q", request.args.get("q", ""))[:60]
    icons, indexed = c.search_icons(q)
    return render_template("partials/icon_results.html", icons=icons, indexed=indexed, q=q)


ICON_TYPES = {  # extensão -> (content-type, assinatura esperada no início do arquivo)
    "png": ("image/png", (b"\x89PNG",)),
    "jpg": ("image/jpeg", (b"\xff\xd8\xff",)),
    "gif": ("image/gif", (b"GIF8",)),
    "webp": ("image/webp", (b"RIFF",)),
    "ico": ("image/x-icon", (b"\x00\x00\x01\x00",)),
    "svg": ("image/svg+xml", ()),
}


def _icon_kind(data, filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    ext = "jpg" if ext == "jpeg" else ext
    if ext not in ICON_TYPES:
        return None
    if ext == "svg":
        head = data[:4096].lstrip().lower()
        return ext if b"<svg" in head and b"<script" not in data.lower() else None
    if ext == "webp" and data[8:12] != b"WEBP":
        return None
    return ext if data.startswith(ICON_TYPES[ext][1]) else None


@app.post("/icons/upload")
def icon_upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(error="Nenhum arquivo enviado"), 400
    data = f.read()
    if len(data) > 512 * 1024:
        return jsonify(error="Imagem muito grande (máx. 512 KB)"), 400
    ext = _icon_kind(data, f.filename)
    if not ext:
        return jsonify(error="Use PNG, JPG, GIF, WEBP, ICO ou SVG"), 400
    name = f"{uuid.uuid4().hex[:12]}.{ext}"
    with open(os.path.join(ICONS_DIR, name), "wb") as out:
        out.write(data)
    _cleanup_icons()
    return jsonify(url=url_for("icon_file", name=name))


@app.get("/icons/file/<name>")
def icon_file(name):
    if not re.fullmatch(r"[0-9a-f]{12}\.(png|jpg|gif|webp|ico|svg)", name):
        abort(404)
    r = send_from_directory(ICONS_DIR, name, mimetype=ICON_TYPES[name.rsplit(".", 1)[1]][0], max_age=86400 * 30)
    # SVG aberto direto na aba não pode executar nada
    r.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
    r.headers["X-Content-Type-Options"] = "nosniff"
    return r


def _cleanup_icons():
    """Apaga ícones enviados há mais de 1 dia que nenhum atalho usa."""
    used = {os.path.basename(a.get("icon", "")) for a in load_apps() if a.get("icon", "").startswith("/icons/file/")}
    for name in os.listdir(ICONS_DIR):
        path = os.path.join(ICONS_DIR, name)
        try:
            if name not in used and time.time() - os.path.getmtime(path) > 86400:
                os.remove(path)
        except OSError:
            pass


@app.post("/apps")
def app_create():
    with _lock:
        apps = load_apps()
        apps.append(_app_from_form())
        _save(APPS_FILE, apps)
    return render_apps()


@app.post("/apps/<aid>")
def app_update(aid):
    with _lock:
        apps = load_apps()
        a = next((x for x in apps if x["id"] == aid), None)
        if not a:
            abort(404)
        _app_from_form(a)
        _save(APPS_FILE, apps)
    return render_apps()


@app.post("/apps/<aid>/delete")
def app_delete(aid):
    with _lock:
        apps = [x for x in load_apps() if x["id"] != aid]
        _save(APPS_FILE, apps)
    return render_apps()


@app.post("/apps/<aid>/move/<direction>")
def app_move(aid, direction):
    with _lock:
        apps = load_apps()
        i = next((n for n, x in enumerate(apps) if x["id"] == aid), None)
        j = (i - 1 if direction == "up" else i + 1) if i is not None else None
        if j is not None and 0 <= j < len(apps):
            apps[i], apps[j] = apps[j], apps[i]
            _save(APPS_FILE, apps)
    return render_apps()


# --------------------------------------------------------------------------- alertas (backup, discos)
@app.post("/api/eventos")
def api_eventos():
    if not avisos.autorizado(request.headers.get("X-Hub-Token", "")):
        return jsonify(error="token inválido"), 401
    try:
        ev = avisos.registrar(request.get_json(force=True, silent=True))
    except ValueError as e:
        return jsonify(error=str(e)), 400
    logging.info("evento %s %s: %s", ev["chave"], ev["nivel"], ev["evento"] or ev["titulo"])
    return jsonify(ok=True, id=ev["id"])


@app.get("/partials/backups")
def backups_partial():
    return render_template("partials/backups.html", a=avisos.estado())


@app.post("/alertas/resolver")
def alerta_resolver():
    avisos.resolver(request.form.get("chave", "")[:80])
    return backups_partial()


@app.post("/alertas/remover")
def alerta_remover():
    avisos.remover(request.form.get("chave", "")[:80])
    return backups_partial()


@app.get("/healthz")
def healthz():
    return {"ok": True}


c.sampler.start()

if __name__ == "__main__":
    app.run(host=os.environ.get("HUB_BIND", "127.0.0.1"), port=int(os.environ.get("HUB_PORT", 8084)), debug=False)
