"""Um programa aberto por vez.

Quem abre primeiro anota em ``instancia.json`` (na pasta de dados) o endereço
local e o token. Se o programa for aberto de novo (pelo atalho, ou pela coleta
agendada), ele pede para o que já está aberto mostrar a janela ou coletar, e
fecha. Assim, com o programa escondido na bandeja, clicar no atalho traz a
janela de volta.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import urllib.request

from . import config

log = logging.getLogger(__name__)
FILE = "instancia.json"


def _path():
    return config.data_dir() / FILE


def register(url: str, token: str) -> None:
    path = _path()
    data = json.dumps({"pid": os.getpid(), "url": url, "token": token})
    try:
        # Só o próprio usuário lê: o token dá acesso ao programa aberto.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
    except OSError as exc:
        log.warning("não foi possível anotar a instância aberta: %s", exc)
        return
    atexit.register(unregister)


def unregister() -> None:
    try:
        info = json.loads(_path().read_text(encoding="utf-8"))
        if info.get("pid") == os.getpid():
            _path().unlink()
    except (OSError, ValueError):
        pass


def forward(action: str, timeout: float = 3.0, **data) -> bool:
    """Pede ao programa já aberto para fazer ``action``. False se não há nenhum aberto."""
    try:
        info = json.loads(_path().read_text(encoding="utf-8"))
        url, token = str(info["url"]), str(info["token"])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    if not url.startswith("http://127.0.0.1:"):
        return False
    request = urllib.request.Request(
        url.rstrip("/") + "/api/app", data=json.dumps({"action": action, **data}).encode(),
        headers={"Content-Type": "application/json", "X-Token": token}, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # endereço local: nunca pelo proxy
    try:
        with opener.open(request, timeout=timeout) as response:
            return bool(json.loads(response.read() or b"{}").get("ok"))
    except Exception:  # anotação velha (o programa fechou sem apagar) ou outro programa na porta
        return False
