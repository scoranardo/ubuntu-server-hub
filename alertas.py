"""Alertas enviados por outros serviços do servidor (backup, saúde dos discos).

Os serviços fazem POST em /api/eventos com o token de data/notify_token.
Cada evento tem uma "chave" (ex.: "backup:servidor-hd", "disco:/dev/sda"); o cartão mostra
o último evento de cada chave e marca como "atrasado" o que deveria ter rodado e não rodou.
"""
import hmac
import json
import os
import secrets
import threading
import time
import uuid

NIVEIS = ("ok", "aviso", "erro")
ORDEM = {"erro": 0, "atrasado": 1, "aviso": 2, "ok": 3}
MAX_EVENTOS = 300
EVENTOS_PT = {  # nomes dos eventos do Backrest, para exibir
    "snapshot success": "backup ok", "snapshot error": "backup falhou", "snapshot warning": "backup com avisos",
    "snapshot skipped": "backup pulado (sem mudanças)", "check success": "verificação ok", "check error": "verificação falhou",
    "prune success": "limpeza ok", "prune error": "limpeza falhou", "forget error": "retenção falhou",
}
ESTATISTICAS = ("data_added", "total_files_processed", "total_bytes_processed", "total_duration",
                "files_new", "files_changed")


def _texto(v, limite):
    return str(v if v is not None else "").strip()[:limite]


class Alertas:
    def __init__(self, data_dir):
        self.arquivo = os.path.join(data_dir, "eventos.json")
        self.arquivo_token = os.path.join(data_dir, "notify_token")
        self.lock = threading.Lock()
        self.token = self._token()

    # ------------------------------------------------------------------ token
    def _token(self):
        """Lê o token compartilhado; cria um na primeira execução (só o dono do arquivo lê)."""
        try:
            with open(self.arquivo_token) as f:
                token = f.read().strip()
            if token:
                return token
        except OSError:
            pass
        token = secrets.token_urlsafe(32)
        fd = os.open(self.arquivo_token, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(token + "\n")
        return token

    def autorizado(self, token):
        return bool(token) and hmac.compare_digest(token.encode(), self.token.encode())

    # ------------------------------------------------------------------ armazenamento
    def _carregar(self):
        try:
            with open(self.arquivo) as f:
                dados = json.load(f)
            return dados if isinstance(dados, list) else []
        except (OSError, ValueError):
            return []

    def _salvar(self, eventos):
        tmp = self.arquivo + ".tmp"
        with open(tmp, "w") as f:
            json.dump(eventos[-MAX_EVENTOS:], f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.arquivo)

    # ------------------------------------------------------------------ entrada
    @staticmethod
    def normalizar(d):
        """Valida o JSON recebido e monta o evento guardado. Levanta ValueError se faltar a chave."""
        if not isinstance(d, dict):
            raise ValueError("corpo deve ser um objeto JSON")
        chave = _texto(d.get("chave"), 80)
        if not chave:
            raise ValueError("campo 'chave' é obrigatório")
        evento = _texto(d.get("evento"), 60)
        erro = _texto(d.get("erro"), 4000)
        nivel = d.get("nivel")
        if nivel not in NIVEIS:
            e = evento.lower()
            nivel = ("erro" if erro or "error" in e or "erro" in e
                     else "aviso" if "warning" in e or "aviso" in e else "ok")
        esperado = d.get("esperado_horas")
        esperado = float(esperado) if isinstance(esperado, (int, float)) and 0 < esperado <= 24 * 62 else None
        stats = d.get("estatisticas") if isinstance(d.get("estatisticas"), dict) else {}
        detalhes = {k: stats[k] for k in ESTATISTICAS if isinstance(stats.get(k), (int, float))}
        if d.get("snapshot"):
            detalhes["snapshot"] = _texto(d["snapshot"], 64)
        if d.get("duracao"):
            detalhes["duracao"] = _texto(d["duracao"], 30)
        return {
            "id": uuid.uuid4().hex[:10],
            "ts": time.time(),
            "origem": _texto(d.get("origem"), 40) or "desconhecido",
            "chave": chave,
            "evento": EVENTOS_PT.get(evento, evento),
            "nivel": nivel,
            "titulo": _texto(d.get("titulo"), 120) or chave,
            "mensagem": _texto(d.get("motivo"), 4000) or _texto(d.get("mensagem"), 4000) or erro,
            "esperado_horas": esperado,
            "detalhes": detalhes,
        }

    def registrar(self, dados):
        ev = self.normalizar(dados)
        with self.lock:
            eventos = self._carregar()
            eventos.append(ev)
            self._salvar(eventos)
        return ev

    def resolver(self, chave):
        """Marca o problema de uma chave como resolvido (ex.: alerta de disco já verificado)."""
        with self.lock:
            eventos = self._carregar()
            anterior = next((e for e in reversed(eventos) if e.get("chave") == chave), None)
            if not anterior:
                return
            eventos.append({**anterior, "id": uuid.uuid4().hex[:10], "ts": time.time(), "nivel": "ok",
                            "evento": "resolvido", "mensagem": "marcado como resolvido no hub", "detalhes": {}})
            self._salvar(eventos)

    def remover(self, chave):
        """Esquece uma chave (ex.: plano de backup que deixou de existir)."""
        with self.lock:
            self._salvar([e for e in self._carregar() if e.get("chave") != chave])

    # ------------------------------------------------------------------ cartão
    def estado(self, recentes=15):
        with self.lock:
            eventos = self._carregar()
        agora = time.time()
        ultimo, ultimo_ok = {}, {}
        for e in eventos:
            ultimo[e["chave"]] = e
            if e.get("nivel") == "ok":
                ultimo_ok[e["chave"]] = e
        itens = []
        for chave, e in ultimo.items():
            ok = ultimo_ok.get(chave)
            status = e.get("nivel", "ok")
            limite = e.get("esperado_horas")
            if status == "ok" and limite and agora - e["ts"] > limite * 3600:
                status = "atrasado"
            itens.append({"chave": chave, "titulo": e.get("titulo") or chave, "origem": e.get("origem"),
                          "status": status, "ultimo": e, "ultimo_ok_ts": ok["ts"] if ok else None,
                          "esperado_horas": limite})
        itens.sort(key=lambda i: (ORDEM.get(i["status"], 9), i["chave"]))
        return {"itens": itens, "recentes": list(reversed(eventos[-recentes:])),
                "problemas": sum(1 for i in itens if i["status"] != "ok")}
