"""Sabe se ainda há uma aba aberta no modo navegador.

No modo navegador o programa é um pequeno servidor local; fechar a aba não avisa
o servidor. Por isso a página manda um "sinal de vida" de tempos em tempos e um
"tchau" ao fechar. Sem aba aberta, o programa encerra sozinho.
"""

from __future__ import annotations

import threading
import time

# O Chrome segura os timers de abas em segundo plano por até 1 minuto, então o
# limite precisa ser folgado para não fechar o programa com a aba só escondida.
IDLE_LIMIT = 150.0
# Depois do "tchau", espera um pouco: recarregar a página manda um sinal novo.
BYE_GRACE = 8.0

_lock = threading.Lock()
_last_beat: float | None = None
_bye_at: float | None = None


def beat() -> None:
    global _last_beat, _bye_at
    with _lock:
        _last_beat, _bye_at = time.monotonic(), None


def bye() -> None:
    global _bye_at
    with _lock:
        _bye_at = time.monotonic()


def reset() -> None:
    global _last_beat, _bye_at
    with _lock:
        _last_beat = _bye_at = None


def page_gone(now: float | None = None) -> bool:
    """True quando a última aba foi fechada (ou sumiu sem avisar)."""
    now = time.monotonic() if now is None else now
    with _lock:
        if _last_beat is None:
            return False  # a página ainda nem abriu
        if _bye_at is not None and now - _bye_at >= BYE_GRACE:
            return True
        return now - _last_beat >= IDLE_LIMIT


def wait_until_gone(stop: threading.Event | None = None, poll: float = 1.0) -> None:
    stop = stop or threading.Event()
    while not stop.wait(poll):
        if page_gone():
            return
