"""Log em arquivo (data/logs/monitor.log, com rodízio) e relatório para suporte."""

from __future__ import annotations

import logging
import platform
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import __version__, config

# Nada de senha, token ou chave no log nem no relatório copiado.
_SECRET_PATTERNS = [
    (re.compile(r"(bot)\d{6,}:[A-Za-z0-9_-]{20,}"), r"\1***"),
    (re.compile(r"(apiKey|token|password|senha|access_token|refresh_token)([\"'=: ]+)[^\s\"'&,]+", re.I), r"\1\2***"),
    (re.compile(r"(https://[^/\s]*(?:webhook|logic\.azure|discord(?:app)?\.com/api/webhooks)[^\s]*)", re.I),
     "[webhook oculto]"),
    (re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,})\b"), "***"),
]


def redact(text: str) -> str:
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


def log_file() -> Path:
    folder = config.data_dir() / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "monitor.log"


def setup() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    file_handler = RotatingFileHandler(log_file(), maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    file_handler.addFilter(RedactFilter())
    root.addHandler(file_handler)
    if sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        console.addFilter(RedactFilter())
        root.addHandler(console)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def tail(lines: int = 80) -> list[str]:
    path = log_file()
    if not path.exists():
        return []
    with open(path, encoding="utf-8", errors="replace") as fh:
        return [line.rstrip("\n") for line in fh.readlines()[-lines:]]


def support_report() -> str:
    """Texto para colar numa conversa ou issue quando algo der errado."""
    from . import db, diagnostics, health

    run = db.last_run()
    errors = db.errors_for_run(run["id"]) if run else []
    lines = [
        "Relatório do Monitor de notícias",
        f"Versão: {__version__}",
        f"Sistema: {platform.platform()} · Python {platform.python_version()}"
        f"{' · executável' if getattr(sys, 'frozen', False) else ''}",
        f"Pasta de dados: {config.data_dir()}",
        "",
        "Última coleta: " + (f"{run['started_at']} UTC, {run['sources_ok']} OK, {run['sources_failed']} com erro, "
                             f"{run['new_items']} novas" if run else "nenhuma"),
    ]
    for err in errors:
        lines.append(f"  ERRO {err['source_name']}: {err['error']}")
    silent = health.silent_sources()
    for s in silent:
        lines.append(f"  SILENCIOSA {s['source_name']}: nada novo há {s['days'] if s['days'] is not None else '?'} dias")
    lines += ["", "Diagnóstico rápido:"]
    for check in diagnostics.run(quick=True):
        lines.append(f"  [{check['status'].upper()}] {check['name']}: {check['detail']}")
    lines += ["", "Últimas linhas do log:"] + ["  " + line for line in tail(60)]
    return redact("\n".join(lines))
