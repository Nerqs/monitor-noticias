"""Saúde das fontes: taxa de sucesso, tempo de resposta e fontes silenciosas.

Uma fonte "silenciosa" responde normalmente, mas não traz nada novo há dias.
É o jeito mais comum de um feed quebrar (mudou de endereço e o antigo ficou
parado, o seletor passou a pegar sempre os mesmos itens...), e não aparece
como erro.
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import config, db

# Fontes que naturalmente passam dias sem novidade.
QUIET_TYPES_DAYS = {"cisa_kev": 21, "nvd": 10, "github_advisories": 10}


def _age_days(value: str | None) -> float | None:
    if not value:
        return None
    moment = datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - moment).total_seconds() / 86400


def silent_limit(source: dict, cfg: dict) -> int:
    if source.get("silent_days"):
        return int(source["silent_days"])
    default = int(cfg.get("health", {}).get("silent_days", 7))
    return max(default, QUIET_TYPES_DAYS.get(source.get("type"), 0))


def silent_sources(cfg: dict | None = None) -> list[dict]:
    cfg = cfg or config.load_config()
    health = db.source_health()
    found = []
    for source in config.load_sources():
        if not source.get("enabled", True):
            continue
        limit = silent_limit(source, cfg)
        h = health.get(source["id"], {})
        if not db.last_success(source["id"]):
            continue  # nunca funcionou: já aparece como erro
        tracked = _age_days(db.first_seen(source["id"]))
        if tracked is None or tracked < limit:
            continue  # ainda não deu tempo de saber
        age = _age_days(h.get("last_new"))
        if age is None or age >= limit:
            found.append({
                "source_id": source["id"],
                "source_name": source["name"],
                "url": source.get("url"),
                "days": None if age is None else int(age),
                "limit": limit,
            })
    return found
