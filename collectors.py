"""Coleta de métricas e informações do servidor.

Roda dentro de um container com pid/rede do host e o / do host montado
(somente leitura) em HUB_HOST_ROOT (ex.: /host). Métricas do kernel (/proc, /sys)
já são as do host; arquivos (dpkg, apt, /etc, /opt...) são lidos via HUB_HOST_ROOT.
Sem HUB_HOST_ROOT, roda direto no host. Nenhuma função exige root.
"""
import glob
import gzip
import http.client
import json
import logging
import os
import platform
import re
import shutil
import socket
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import psutil

log = logging.getLogger("hub")

INTERVAL = float(os.environ.get("HUB_SAMPLE_INTERVAL", 2))
HISTORY = int(os.environ.get("HUB_HISTORY", 90))  # 90 x 2s = 3 min de gráfico
DOCKER_SOCK = os.environ.get("DOCKER_SOCK", "/var/run/docker.sock")
RAPL = "/sys/class/powercap/intel-rapl:0"
HOST_ROOT = os.environ.get("HUB_HOST_ROOT", "").rstrip("/")


def hp(path):
    """Caminho de um arquivo do host (prefixa HUB_HOST_ROOT quando em container)."""
    return HOST_ROOT + path


def _apt_env():
    """Faz o apt do container ler a configuração, listas e o dpkg do host (sem escrever nada)."""
    if not HOST_ROOT:
        return {}
    conf = "/tmp/apt-host.conf"
    with open(conf, "w") as f:
        f.write(f'''Dir "{HOST_ROOT}/";
Dir::State::status "{HOST_ROOT}/var/lib/dpkg/status";
Dir::Bin::dpkg "/bin/false";
Dir::Cache::pkgcache "";
Dir::Cache::srcpkgcache "";
Debug::NoLocking "true";
''')
    return {"APT_CONFIG": conf}


APT_ENV = _apt_env()
DPKG_ADMIN = [f"--admindir={hp('/var/lib/dpkg')}"] if HOST_ROOT else []


# --------------------------------------------------------------------------- utils
def read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def run(cmd, timeout=30, env=None):
    if not shutil.which(cmd[0]):
        return ""
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "LANG": "C", "LC_ALL": "C", **(env or {})},
        )
        return p.stdout + ("\n" + p.stderr if p.returncode else "")
    except (OSError, subprocess.SubprocessError):
        return ""


def fmt_bytes(n, suffix=""):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}{suffix}" if unit == "B" else f"{n:.1f} {unit}{suffix}"
        n /= 1024


def fmt_duration(secs):
    secs = int(secs or 0)
    d, r = divmod(secs, 86400)
    h, r = divmod(r, 3600)
    m, _ = divmod(r, 60)
    if d:
        return f"{d}d {h}h {m}min"
    if h:
        return f"{h}h {m}min"
    return f"{m}min"


def fmt_ago(ts):
    if not ts:
        return "nunca"
    return "há " + fmt_duration(time.time() - ts) if time.time() - ts >= 60 else "agora há pouco"


def level(pct, warn=60, crit=85):
    if pct is None:
        return "none"
    return "ok" if pct < warn else "warn" if pct < crit else "crit"


def cached(ttl):
    """Cache simples com TTL; chamar com force=True ignora o cache."""
    def deco(fn):
        lock, box = threading.Lock(), {}

        def wrapper(force=False):
            with lock:
                if not force and box and time.time() - box["t"] < ttl:
                    return box["v"]
                v = fn()
                box.update(t=time.time(), v=v)
                return v
        wrapper.__name__ = fn.__name__
        return wrapper
    return deco


# --------------------------------------------------------------------------- estático
def _os_name():
    for line in (read(hp("/etc/os-release"), "") or "").splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip('"')
    return platform.system()


def _cpu_model():
    for line in (read("/proc/cpuinfo", "") or "").splitlines():
        if line.startswith("model name"):
            return re.sub(r"\s+", " ", line.split(":", 1)[1]).replace("(R)", "").replace("(TM)", "").replace(" CPU", "").strip()
    return platform.processor() or "?"


def _host_model():
    junk = ("", "none", "to be filled by o.e.m.", "default string", "system product name")
    vendor = read("/sys/class/dmi/id/sys_vendor", "") or ""
    name = read("/sys/class/dmi/id/product_name", "") or ""
    version = read("/sys/class/dmi/id/product_version", "") or ""
    # alguns fabricantes (ex.: Lenovo) guardam o nome comercial em product_version e um código em product_name
    model = version if vendor.upper().startswith("LENOVO") and version.lower() not in junk else name
    if model.lower() in junk:
        model = ""
    return f"{vendor} {model}".strip() or platform.node()


def _core_ids():
    ids = {}
    for i in range(psutil.cpu_count() or 1):
        cid = read(f"/sys/devices/system/cpu/cpu{i}/topology/core_id")
        ids[i] = int(cid) if cid and cid.isdigit() else i
    return ids


def _env(name):
    """Valor do .env (vazio = usar o que foi detectado na máquina)."""
    return os.environ.get(name, "").strip()


_hostname = read(hp("/etc/hostname")) or socket.gethostname()
_detected_model = _host_model()
STATIC = {
    "hostname": _hostname,
    "os": _os_name(),
    "kernel": platform.release(),
    "arch": platform.machine(),
    # detectados pelos sensores (/proc/cpuinfo e /sys/class/dmi); HUB_CPU_NAME / HUB_HOST_MODEL sobrescrevem
    "cpu_model": _env("HUB_CPU_NAME") or _cpu_model(),
    "host_model": _env("HUB_HOST_MODEL") or _detected_model,
    # topo da página: HUB_TITLE (padrão: hostname) e HUB_SUBTITLE (padrão: modelo do computador)
    "title": _env("HUB_TITLE") or _hostname,
    "subtitle": _env("HUB_SUBTITLE") or _env("HUB_HOST_MODEL") or _detected_model,
    "cores_phys": psutil.cpu_count(logical=False),
    "cores_log": psutil.cpu_count(),
    "user": os.environ.get("HUB_HOST_USER") or __import__("getpass").getuser(),
    "governor": read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
}
CORE_IDS = _core_ids()


# --------------------------------------------------------------------------- sensores
def _temps():
    """Retorna (pacote, {core_id: temp}, [outros sensores])."""
    try:
        data = psutil.sensors_temperatures()
    except Exception:
        return None, {}, []
    pkg, cores, other = None, {}, []
    for chip, entries in data.items():
        for e in entries:
            label = e.label or chip
            if chip in ("coretemp", "k10temp", "zenpower"):
                if label.startswith("Package") or label in ("Tctl", "Tdie"):
                    pkg = {"current": e.current, "high": e.high or 85, "critical": e.critical or 100}
                elif label.startswith("Core "):
                    try:
                        cores[int(label.split()[1])] = e.current
                    except ValueError:
                        pass
            else:
                other.append({"chip": chip, "label": label, "current": e.current})
    if pkg is None and cores:
        pkg = {"current": max(cores.values()), "high": 85, "critical": 100}
    return pkg, cores, other


def _rapl_energy():
    v = read(f"{RAPL}/energy_uj")  # só legível por root, a não ser que liberado (veja README)
    return int(v) if v and v.isdigit() else None


def _physical_disks():
    disks = {}
    for path in glob.glob("/sys/block/*"):
        name = os.path.basename(path)
        if re.match(r"^(loop|ram|zram|dm-|sr|md|fd)", name):
            continue
        size = int(read(f"{path}/size", "0") or 0) * 512
        if not size:
            continue
        rot = read(f"{path}/queue/rotational", "0") == "1"
        kind = "NVMe" if name.startswith("nvme") else ("HD" if rot else "SSD")
        model = read(f"{path}/device/model", "") or ""
        disks[name] = {"name": name, "model": model.strip(), "kind": kind, "size": size}
    return disks


def _parent_disk(device):
    """/dev/mapper/ubuntu--vg-ubuntu--lv -> sda (resolve LVM/partição)."""
    name = os.path.basename(os.path.realpath(hp(device) if device.startswith("/dev/") else device))
    for _ in range(5):
        slaves = f"/sys/class/block/{name}/slaves"
        if os.path.isdir(slaves) and os.listdir(slaves):
            name = sorted(os.listdir(slaves))[0]
            continue
        break
    real = os.path.realpath(f"/sys/class/block/{name}")
    if os.path.exists(os.path.join(real, "partition")):
        name = os.path.basename(os.path.dirname(real))
    return name


def _host_mounts():
    """Montagens do host. Em container, lê as do PID 1 do host (pid: host) em vez das do container."""
    if not HOST_ROOT:
        return [(p.device, p.mountpoint, p.fstype) for p in psutil.disk_partitions(all=False)]
    physical = {l.strip() for l in (read("/proc/filesystems", "") or "").splitlines() if l and not l.startswith("nodev")}
    text = read("/proc/1/mounts") or read("/proc/self/mounts", "") or ""
    out = []
    for line in text.splitlines():
        p = line.split()
        if len(p) >= 3 and p[2] in physical and not p[1].startswith(HOST_ROOT + "/"):
            out.append((p[0], p[1].replace("\\040", " "), p[2]))
    return out


def _partitions():
    out, seen = [], set()
    for device, mount, fstype in _host_mounts():
        if fstype in ("squashfs", "tmpfs", "overlay", "iso9660") or mount.startswith(("/snap", "/var/lib/docker", "/boot/efi")):
            continue
        if device in seen:
            continue
        seen.add(device)
        try:
            u = psutil.disk_usage(hp(mount) if HOST_ROOT else mount)
        except OSError:
            continue
        out.append({
            "mount": mount, "device": device, "fstype": fstype,
            "total": u.total, "used": u.used, "free": u.free, "pct": u.percent,
            "disk": _parent_disk(device),
        })
    return out


_users = {"t": 0, "v": {}}


def _usernames():
    """UID -> nome a partir do /etc/passwd do host (o do container não conhece os usuários do host)."""
    if time.time() - _users["t"] > 60:
        names = {}
        for line in (read(hp("/etc/passwd"), "") or "").splitlines():
            p = line.split(":")
            if len(p) > 2 and p[2].isdigit():
                names[int(p[2])] = p[0]
        _users.update(t=time.time(), v=names)
    return _users["v"]


NET_SKIP = re.compile(r"^(lo|veth|docker|br-|virbr|vnet)")


# --------------------------------------------------------------------------- amostrador
class Sampler:
    """Thread que coleta tudo a cada INTERVAL segundos e guarda histórico (estilo s-tui)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.snap = {}
        self.hist = {k: deque(maxlen=HISTORY) for k in ("cpu", "temp", "freq", "mem", "power", "rx", "tx")}
        self._prev = None
        self._started = False

    def start(self):
        if self._started:
            return
        self._started = True
        psutil.cpu_percent(percpu=True)
        psutil.cpu_times_percent()
        for p in psutil.process_iter(["cpu_percent"]):
            pass
        self._prev = self._counters()
        time.sleep(0.5)
        self._sample()
        threading.Thread(target=self._loop, name="sampler", daemon=True).start()

    def _loop(self):
        while True:
            time.sleep(INTERVAL)
            try:
                self._sample()
            except Exception:
                log.exception("falha ao coletar métricas")

    def get(self):
        with self.lock:
            return self.snap, {k: list(v) for k, v in self.hist.items()}

    @staticmethod
    def _counters():
        try:
            disk = psutil.disk_io_counters(perdisk=True) or {}
        except Exception:
            disk = {}
        return {
            "t": time.monotonic(),
            "net": psutil.net_io_counters(pernic=True),
            "disk": disk,
            "energy": _rapl_energy(),
        }

    def _sample(self):
        now, prev = self._counters(), self._prev
        self._prev = now
        dt = max(now["t"] - prev["t"], 0.001)

        # CPU ---------------------------------------------------------------
        per = psutil.cpu_percent(percpu=True)
        total = sum(per) / len(per) if per else 0
        times = psutil.cpu_times_percent()._asdict()
        try:
            freqs = psutil.cpu_freq(percpu=True) or []
        except Exception:
            freqs = []
        pkg, core_temps, other_temps = _temps()
        cores = []
        for i, pct in enumerate(per):
            f = freqs[i] if i < len(freqs) else None
            cores.append({
                "idx": i, "pct": pct,
                "freq": f.current if f else None,
                "fmax": f.max if f and f.max else None,
                "temp": core_temps.get(CORE_IDS.get(i, i)),
            })
        cur = [c["freq"] for c in cores if c["freq"]]
        freq_avg = sum(cur) / len(cur) if cur else None
        fmax = max((c["fmax"] for c in cores if c["fmax"]), default=None)

        power = None
        if now["energy"] is not None and prev["energy"] is not None:
            delta = now["energy"] - prev["energy"]
            if delta < 0:
                delta += int(read(f"{RAPL}/max_energy_range_uj", "0") or 0)
            power = delta / 1e6 / dt

        # Memória -----------------------------------------------------------
        vm, sw = psutil.virtual_memory(), psutil.swap_memory()
        cache = getattr(vm, "buffers", 0) + getattr(vm, "cached", 0)
        used_pct = vm.used / vm.total * 100
        mem = {
            "total": vm.total, "used": vm.total - vm.available, "available": vm.available,
            "cache": cache, "pct": vm.percent,
            "used_pct": used_pct,  # barra estilo htop: usado | buffers+cache | livre
            "cache_pct": max(min(cache / vm.total * 100, 100 - used_pct), 0),
            "swap_total": sw.total, "swap_used": sw.used, "swap_pct": sw.percent,
        }

        # Discos ------------------------------------------------------------
        disks = _physical_disks()
        for name, d in disks.items():
            c, p = now["disk"].get(name), prev["disk"].get(name)
            d["read"] = (c.read_bytes - p.read_bytes) / dt if c and p else 0
            d["write"] = (c.write_bytes - p.write_bytes) / dt if c and p else 0
            d["parts"] = []
        for part in _partitions():
            disks.setdefault(part["disk"], {"name": part["disk"], "model": "", "kind": "?", "size": part["total"], "read": 0, "write": 0, "parts": []})
            disks[part["disk"]]["parts"].append(part)
        disks = {k: d for k, d in disks.items() if d["parts"] or d["size"] >= 1 << 30}  # ignora discos minúsculos sem uso


        # Rede --------------------------------------------------------------
        stats = psutil.net_if_stats()
        addrs = psutil.net_if_addrs()
        nets, rx_tot, tx_tot = [], 0, 0
        for nic, c in now["net"].items():
            if NET_SKIP.match(nic) or not stats.get(nic) or not stats[nic].isup:
                continue
            p = prev["net"].get(nic)
            rx = (c.bytes_recv - p.bytes_recv) / dt if p else 0
            tx = (c.bytes_sent - p.bytes_sent) / dt if p else 0
            rx_tot += rx
            tx_tot += tx
            ipv4 = [a.address for a in addrs.get(nic, []) if a.family == socket.AF_INET]
            nets.append({"name": nic, "rx": rx, "tx": tx, "rx_total": c.bytes_recv, "tx_total": c.bytes_sent,
                         "ip": ipv4[0] if ipv4 else "", "speed": stats[nic].speed})

        # Processos (top/htop) ----------------------------------------------
        procs, threads, running = [], 0, 0
        users = _usernames()
        attrs = ["pid", "ppid", "name", "uids", "cpu_percent", "memory_percent", "memory_info", "num_threads", "status", "cmdline"]
        for pr in psutil.process_iter(attrs):
            i = pr.info
            if i["pid"] == 2 or i["ppid"] == 2:  # esconde threads do kernel, como o htop
                continue
            threads += i["num_threads"] or 0
            if i["status"] == psutil.STATUS_RUNNING:
                running += 1
            cmd = " ".join(i["cmdline"] or []) or f"[{i['name']}]"
            procs.append({
                "pid": i["pid"], "user": str(users.get(i["uids"].real, i["uids"].real) if i["uids"] else "?")[:12],
                "name": i["name"],
                "cpu": i["cpu_percent"] or 0.0, "mem": i["memory_percent"] or 0.0,
                "res": i["memory_info"].rss if i["memory_info"] else 0,
                "cmd": cmd[:240], "status": i["status"],
            })

        snap = {
            "ts": time.time(),
            "cpu": {"total": total, "cores": cores, "times": times, "freq_avg": freq_avg, "freq_max": fmax,
                    "pkg_temp": pkg, "power": power, "load": os.getloadavg()},
            "sensors": other_temps,
            "mem": mem,
            "disks": sorted(disks.values(), key=lambda d: d["name"]),
            "net": nets, "rx": rx_tot, "tx": tx_tot,
            "procs": procs, "proc_count": len(procs), "threads": threads, "running": running,
            "uptime": time.time() - psutil.boot_time(), "boot": psutil.boot_time(),
        }
        with self.lock:
            self.snap = snap
            self.hist["cpu"].append(total)
            self.hist["temp"].append(pkg["current"] if pkg else None)
            self.hist["freq"].append(freq_avg)
            self.hist["mem"].append(vm.percent)
            self.hist["power"].append(power)
            self.hist["rx"].append(rx_tot)
            self.hist["tx"].append(tx_tot)


sampler = Sampler()


def top_processes(snap, sort="cpu", limit=15):
    key = {"cpu": "cpu", "mem": "mem"}.get(sort, "cpu")
    return sorted(snap.get("procs", []), key=lambda p: (p[key], p["mem"]), reverse=True)[:limit]


# --------------------------------------------------------------------------- fastfetch
@cached(600)
def package_counts():
    status = read(hp("/var/lib/dpkg/status"), "") or ""
    dpkg = sum(1 for b in status.split("\n\n") if "Status: install ok installed" in b)
    snaps = len(_snap_dirs()) if HOST_ROOT else max(len(run(["snap", "list"]).strip().splitlines()) - 1, 0)
    flat = len([l for l in run(["flatpak", "list", "--app", "--columns=application"]).splitlines() if l.strip()])
    return {"dpkg": dpkg, "snap": snaps, "flatpak": flat}


def _snap_dirs():
    """Snaps instalados no host, pelas pastas em /snap (o comando snap não existe no container)."""
    skip = re.compile(r"^(bin|README|core\d*|snapd|bare|gtk-common-themes|gnome-\d.*)$")
    return sorted(os.path.basename(p) for p in glob.glob(hp("/snap/*"))
                  if os.path.isdir(p) and not skip.match(os.path.basename(p)))


def _sessions():
    try:
        return sorted({u.name for u in psutil.users()})
    except Exception:
        return []


def system_info(snap):
    root = next((p for d in snap.get("disks", []) for p in d["parts"] if p["mount"] == "/"), None)
    main_ip = next((n["ip"] for n in snap.get("net", []) if n["ip"]), "")
    return {
        **STATIC,
        "uptime": snap.get("uptime", 0),
        "boot": datetime.fromtimestamp(snap.get("boot", 0)).strftime("%d/%m/%Y %H:%M"),
        "packages": package_counts(),
        "users": _sessions(),
        "ip": main_ip,
        "mem": snap.get("mem", {}),
        "root": root,
    }


# --------------------------------------------------------------------------- docker
class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout=5):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self._path)
        self.sock = s


def _docker(path, timeout=5):
    conn = _UnixHTTPConnection(DOCKER_SOCK, timeout)
    try:
        conn.request("GET", path)
        r = conn.getresponse()
        body = r.read()
        if r.status >= 400:
            raise RuntimeError(f"Docker API {r.status}: {body[:200]!r}")
        return json.loads(body)
    finally:
        conn.close()


def _container_stats(cid):
    try:
        s = _docker(f"/containers/{cid}/stats?stream=false", timeout=6)
    except Exception:
        return cid, None
    try:
        cpu, pre = s["cpu_stats"], s["precpu_stats"]
        cpu_delta = cpu["cpu_usage"]["total_usage"] - pre["cpu_usage"]["total_usage"]
        sys_delta = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
        online = cpu.get("online_cpus") or len(cpu["cpu_usage"].get("percpu_usage") or []) or 1
        cpu_pct = cpu_delta / sys_delta * online * 100 if sys_delta > 0 else 0.0
        m = s.get("memory_stats", {})
        inactive = m.get("stats", {}).get("inactive_file", m.get("stats", {}).get("total_inactive_file", 0))
        mem = max(m.get("usage", 0) - inactive, 0)
        return cid, {"cpu": cpu_pct, "mem": mem, "limit": m.get("limit", 0)}
    except (KeyError, TypeError, ZeroDivisionError):
        return cid, None


@cached(8)
def docker_containers():
    if not os.path.exists(DOCKER_SOCK):
        return {"error": "Socket do Docker não encontrado", "items": []}
    try:
        raw = _docker("/containers/json?all=1")
    except PermissionError:
        return {"error": "Sem permissão no socket do Docker — adicione o usuário do serviço ao grupo docker", "items": []}
    except Exception as e:
        return {"error": str(e), "items": []}
    running_ids = [c["Id"] for c in raw if c.get("State") == "running"]
    with ThreadPoolExecutor(max_workers=8) as ex:
        stats = dict(ex.map(_container_stats, running_ids))
    items = []
    for c in raw:
        ports = set()
        for p in c.get("Ports", []):
            if p.get("PublicPort"):
                ports.add((p["PublicPort"], f"{p['PublicPort']}→{p['PrivatePort']}/{p['Type']}"))
            else:
                ports.add((p["PrivatePort"], f"{p['PrivatePort']}/{p['Type']}"))
        status = c.get("Status", "")
        health = "healthy" if "(healthy)" in status else "unhealthy" if "(unhealthy)" in status else None
        labels = c.get("Labels") or {}
        items.append({
            "id": c["Id"][:12], "name": (c.get("Names") or ["?"])[0].lstrip("/"),
            "image": c.get("Image", ""), "state": c.get("State", ""), "status": status,
            "health": health, "ports": [p[1] for p in sorted(ports)],
            "project": labels.get("com.docker.compose.project", ""),
            "stats": stats.get(c["Id"]),
        })
    items.sort(key=lambda x: (x["state"] != "running", x["project"] or "~", x["name"]))
    return {"error": None, "items": items,
            "running": sum(1 for i in items if i["state"] == "running"), "total": len(items)}


# --------------------------------------------------------------------------- atualizações
@cached(900)
def updates():
    apt = []
    for line in run(["apt", "list", "--upgradable"], 90, APT_ENV).splitlines():
        m = re.match(r"^(\S+?)/(\S+)\s+(\S+)\s+\S+\s+\[upgradable from: ([^\]]+)\]", line)
        if m:
            apt.append({"name": m[1], "suite": m[2], "new": m[3], "old": m[4], "security": "-security" in m[2]})

    stamp = None
    for p in ("/var/lib/apt/periodic/update-success-stamp", "/var/lib/apt/lists"):
        try:
            stamp = os.path.getmtime(hp(p))
            break
        except OSError:
            pass

    snap = []
    out = run(["snap", "refresh", "--list"], 60)
    lines = out.strip().splitlines()
    if lines and lines[0].startswith("Name"):
        for l in lines[1:]:
            parts = l.split()
            if len(parts) >= 2:
                snap.append({"name": parts[0], "new": parts[1]})

    flatpak = []
    for l in run(["flatpak", "remote-ls", "--updates", "--columns=application,version"], 60).splitlines():
        parts = l.split("\t")
        if parts and parts[0].strip() and "." in parts[0]:
            flatpak.append({"name": parts[0].strip(), "new": parts[1].strip() if len(parts) > 1 else ""})

    reboot_pkgs = (read(hp("/run/reboot-required.pkgs"), "") or "").split()
    return {
        "apt": sorted(apt, key=lambda x: (not x["security"], x["name"])),
        "security": sum(1 for a in apt if a["security"]),
        "snap": snap, "flatpak": flatpak,
        "reboot": os.path.exists(hp("/run/reboot-required")),
        "reboot_pkgs": sorted(set(reboot_pkgs)),
        "lists_updated": stamp, "checked_at": time.time(),
        "has_snap": bool(shutil.which("snap")), "has_flatpak": bool(shutil.which("flatpak")),
        "total": len(apt) + len(snap) + len(flatpak),
    }


# --------------------------------------------------------------------------- instalados
def _apt_history():
    """Pacotes instalados explicitamente (não 'automatic') segundo /var/log/apt/history.log*."""
    def order(path):
        m = re.search(r"\.(\d+)(\.gz)?$", path)
        return -int(m[1]) if m else 0
    res = {}
    for path in sorted(glob.glob(hp("/var/log/apt/history.log*")), key=order):
        try:
            opener = gzip.open if path.endswith(".gz") else open
            with opener(path, "rt", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        for block in text.split("\n\n"):
            date = re.search(r"^Start-Date:\s*(\S+)", block, re.M)
            cmd = re.search(r"^Commandline:\s*(.+)$", block, re.M)
            if cmd and "unattended-upgrade" in cmd[1]:
                continue
            # só conta como "seu" o que veio de um comando com sudo (o instalador do Ubuntu não tem Requested-By)
            human = re.search(r"^Requested-By:", block, re.M) is not None
            for kind in ("Install", "Remove", "Purge"):
                m = re.search(rf"^{kind}:\s*(.+)$", block, re.M)
                if not m:
                    continue
                for entry in re.findall(r"([^\s,()]+?)(?::\S+)?\s\(([^)]*)\)", m[1]):
                    name, info = entry
                    name = name.split(":")[0]
                    if kind == "Install" and human and "automatic" not in info:
                        res[name] = date[1] if date else None
                    elif kind in ("Remove", "Purge"):
                        res.pop(name, None)
    return res


def _baseline():
    """Pacotes que vieram com o sistema: metapacotes do Ubuntu + prioridade required/important/standard."""
    base = set()
    initial = hp("/var/log/installer/initial-status.gz")
    if os.path.exists(initial):
        try:
            with gzip.open(initial, "rt", errors="replace") as f:
                base |= set(re.findall(r"^Package:\s*(\S+)", f.read(), re.M))
        except OSError:
            pass
    metas = [m for m in ("ubuntu-server", "ubuntu-server-minimal", "ubuntu-minimal", "ubuntu-standard",
                         "ubuntu-cloud-minimal", "ubuntu-desktop", "ubuntu-desktop-minimal")
             if run(["dpkg-query", *DPKG_ADMIN, "-W", "-f=${Status}", m]).startswith("install ok installed")]
    if metas:
        out = run(["apt-cache", "depends", "--installed", "--no-suggests", "--no-conflicts", "--no-breaks",
                   "--no-replaces", "--no-enhances", *metas], 60, APT_ENV)
        base |= set(metas)
        base |= {n.strip("<>") for n in re.findall(r"^\s+\|?(?:Depends|Recommends|PreDepends):\s*(\S+)", out, re.M)}
    return base


@cached(600)
def installed():
    fields = "${Package}\t${Version}\t${Priority}\t${Installed-Size}\t${binary:Summary}\n"
    info = {}
    for line in run(["dpkg-query", *DPKG_ADMIN, "-W", f"-f={fields}"], 60).splitlines():
        parts = line.split("\t")
        if len(parts) >= 5:
            info[parts[0]] = {"version": parts[1], "priority": parts[2], "size": int(parts[3] or 0) * 1024, "summary": parts[4]}

    manual = set(run(["apt-mark", "showmanual"], 60, APT_ENV).split())
    history = _apt_history()
    base = _baseline()
    kernel = re.compile(r"^(linux-(image|headers|modules|generic|firmware|virtual|signed)|grub|shim|efibootmgr)")
    names = {n for n in manual if n not in base and info.get(n, {}).get("priority") not in ("required", "important", "standard")
             and not kernel.match(n)}
    names |= {n for n in history if n in manual}

    items = []
    for n in names:
        i = info.get(n)
        if not i:
            continue
        items.append({"key": f"apt:{n}", "name": n, "source": "apt", "version": i["version"],
                      "summary": i["summary"], "size": i["size"], "date": history.get(n)})

    for name in _snap_dirs() if HOST_ROOT else []:
        items.append({"key": f"snap:{name}", "name": name, "source": "snap", "version": "",
                      "summary": "snap", "size": None, "date": None})

    snap_out = run(["snap", "list"]).splitlines()
    for line in snap_out[1:]:
        p = line.split()
        if len(p) < 4:
            continue
        notes = p[5] if len(p) > 5 else ""
        if re.match(r"^(core\d*|snapd|bare|gtk-common-themes|gnome-\d)", p[0]) or notes in ("base", "snapd"):
            continue
        items.append({"key": f"snap:{p[0]}", "name": p[0], "source": "snap", "version": p[1],
                      "summary": f"canal {p[3]}" if len(p) > 3 else "", "size": None, "date": None})

    for line in run(["flatpak", "list", "--app", "--columns=application,version,name"]).splitlines():
        p = line.split("\t")
        if p and p[0].strip():
            items.append({"key": f"flatpak:{p[0]}", "name": p[0], "source": "flatpak",
                          "version": p[1] if len(p) > 1 else "", "summary": p[2] if len(p) > 2 else "", "size": None, "date": None})

    for path in sorted(glob.glob(hp("/usr/local/bin/*"))):
        if os.path.isfile(path) and os.access(path, os.X_OK):
            st = os.stat(path)
            items.append({"key": f"local:{os.path.basename(path)}", "name": os.path.basename(path), "source": "local",
                          "version": "", "summary": "binário em /usr/local/bin", "size": st.st_size,
                          "date": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d")})
    for path in sorted(glob.glob(hp("/opt/*"))):
        if os.path.isdir(path) and not os.path.basename(path).startswith("."):
            items.append({"key": f"opt:{os.path.basename(path)}", "name": os.path.basename(path), "source": "opt",
                          "version": "", "summary": "pasta em /opt", "size": None,
                          "date": datetime.fromtimestamp(os.stat(path).st_mtime).strftime("%Y-%m-%d")})

    items.sort(key=lambda x: (x["date"] or "0000", x["name"]), reverse=True)
    return items


# --------------------------------------------------------------------------- ícones
# Lista de ícones do dashboard-icons (https://github.com/homarr-labs/dashboard-icons), baixada pelo
# servidor e guardada em memória; o navegador carrega as imagens direto do CDN.
ICON_CDN = os.environ.get("HUB_ICON_CDN", "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons").rstrip("/")
# apelidos comuns -> nomes no dashboard-icons (só aparecem se existirem na lista)
ICON_ALIASES = {
    "vscode": ["visual-studio-code", "code-server"],
    "codeserver": ["code-server", "visual-studio-code"],
    "code": ["visual-studio-code", "code-server"],
    "files": ["filebrowser", "files"],
    "arquivos": ["filebrowser", "nextcloud"],
    "financas": ["firefly-iii", "actual-budget"],
    "finance": ["firefly-iii", "actual-budget"],
}
_icons = {"names": None, "t": 0.0}
_icons_lock = threading.Lock()


def _fetch_icon_names():
    req = urllib.request.Request(f"{ICON_CDN}/tree.json", headers={"User-Agent": "ubuntu-server-hub"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.load(r)
    # formato esperado: {"svg": ["jellyfin.svg", ...], "png": [...], ...}; aceita também uma lista simples
    if isinstance(data, dict):
        files = data.get("svg") if isinstance(data.get("svg"), list) else \
            [f for v in data.values() if isinstance(v, list) for f in v]
    else:
        files = data if isinstance(data, list) else []
    return sorted({os.path.splitext(os.path.basename(f))[0] for f in files if isinstance(f, str) and f.endswith(".svg")})


def icon_names():
    """Nomes disponíveis (cache de 24 h; após falha, tenta de novo em 5 min). None se indisponível."""
    with _icons_lock:
        ttl = 86400 if _icons["names"] else 300
        if time.time() - _icons["t"] > ttl:
            try:
                _icons["names"] = _fetch_icon_names() or _icons["names"]
            except Exception as e:
                log.warning("lista de ícones indisponível (%s): %s", ICON_CDN, e)
            _icons["t"] = time.time()
        return _icons["names"]


def icon_url(name):
    return f"{ICON_CDN}/svg/{name}.svg"


def search_icons(q, limit=30):
    slug = re.sub(r"[^a-z0-9]+", "-", (q or "").lower()).strip("-")
    if not slug:
        return [], True
    names = icon_names()
    if not names:  # sem a lista: tenta o nome digitado (o navegador esconde o que não existir)
        guesses = dict.fromkeys([slug, slug.replace("-", "")])
        return [{"name": n, "url": icon_url(n)} for n in guesses if n], False
    compact = slug.replace("-", "")

    def rank(n):
        c = n.replace("-", "")
        if n == slug or c == compact:
            return 0
        if n.startswith(slug) or c.startswith(compact):
            return 1
        return 2 if compact in c else 3

    found = sorted((n for n in names if rank(n) < 3), key=lambda n: (rank(n), len(n), n))
    known = set(names)
    extra = [n for n in ICON_ALIASES.get(compact, []) if n in known]
    found = (extra + [n for n in found if n not in extra])[:limit]
    return [{"name": n, "url": icon_url(n)} for n in found], True


# --------------------------------------------------------------------------- status dos apps
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


_insecure = ssl.create_default_context()
_insecure.check_hostname = False
_insecure.verify_mode = ssl.CERT_NONE  # só para health check interno (certificados self-signed)
_opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=_insecure))


def _check(app):
    url = app.get("check_url")
    if not url:
        return app["id"], None
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ubuntu-server-hub"})
        with _opener.open(req, timeout=3) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception:
        return app["id"], {"up": False, "code": None, "ms": None}
    return app["id"], {"up": code < 500, "code": code, "ms": int((time.monotonic() - t0) * 1000)}


_status_cache = {"t": 0, "v": {}, "key": None}
_status_lock = threading.Lock()


def app_status(apps, ttl=30):
    key = tuple((a["id"], a.get("check_url")) for a in apps)
    with _status_lock:
        if _status_cache["key"] == key and time.time() - _status_cache["t"] < ttl:
            return _status_cache["v"]
        with ThreadPoolExecutor(max_workers=8) as ex:
            v = dict(ex.map(_check, apps))
        _status_cache.update(t=time.time(), v=v, key=key)
        return v
