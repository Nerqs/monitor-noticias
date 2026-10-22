"""Faxina do banco: apaga o que é velho e compacta o arquivo de vez em quando."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from . import db

log = logging.getLogger(__name__)


def _due(key: str, every: timedelta) -> bool:
    last = db.get_meta(key)
    if not last:
        return True
    try:
        moment = datetime.fromisoformat(last)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - moment >= every


def run(cfg: dict, vacuum: bool = False) -> dict:
    settings = cfg.get("maintenance", {})
    before = db.stats()["bytes"]
    result = db.cleanup(int(settings.get("keep_days", 90)), int(settings.get("article_days", 30)))
    now = datetime.now(timezone.utc).isoformat()
    db.set_meta("cleanup_at", now)
    if vacuum or result["items"] > 500 or _due("vacuum_at", timedelta(days=30)):
        db.vacuum()
        db.set_meta("vacuum_at", now)
    result["freed_bytes"] = max(0, before - db.stats()["bytes"])
    log.info("faxina: %s", result)
    return result


def run_if_due(cfg: dict) -> dict | None:
    if not cfg.get("maintenance", {}).get("enabled", True):
        return None
    if not _due("cleanup_at", timedelta(days=1)):
        return None
    return run(cfg)
