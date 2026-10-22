"""Executa uma coleta completa: busca as fontes, trata, pontua e grava."""

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config, cveinfo, db, fetchers, preferences, scoring

log = logging.getLogger(__name__)


class Progress:
    """Estado da coleta em andamento, lido pela interface."""

    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        with self._lock:
            self.running = False
            self.total = 0
            self.done = 0
            self.current: list[str] = []
            self.new_items = 0
            self.failed = 0
            self.run_id = None
            self.message = ""

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self.running,
                "total": self.total,
                "done": self.done,
                "current": list(self.current),
                "new_items": self.new_items,
                "failed": self.failed,
                "run_id": self.run_id,
                "message": self.message,
            }

    def update(self, **kwargs):
        with self._lock:
            for key, value in kwargs.items():
                setattr(self, key, value)

    def started(self, name: str):
        with self._lock:
            self.current.append(name)

    def finished(self, name: str, new_items: int, failed: int):
        with self._lock:
            self.done += 1
            if name in self.current:
                self.current.remove(name)
            self.new_items = new_items
            self.failed = failed


progress = Progress()
_run_lock = threading.Lock()


def process_items(source: dict, raw_items: list[dict], cfg: dict, run_id: int | None) -> int:
    """Pontua e grava as notícias de uma fonte. Devolve quantas são novas."""
    new = 0
    fetched_at = db.now_iso()
    model = preferences.get_model()
    for raw in raw_items:
        scored = scoring.score_item(raw, source["category"], cfg)
        if raw.get("nvd_raw") and raw.get("cves"):
            db.save_cve_nvd(raw["cves"][0], json.dumps(raw["nvd_raw"]))
        item = {
            "uid": raw["uid"],
            "source_id": source["id"],
            "source_name": source["name"],
            "category": source["category"],
            "title": raw["title"],
            "url": raw["url"],
            "summary": raw.get("summary", ""),
            "published": db.to_db_time(raw["published"]) if raw.get("published") else fetched_at,
            "fetched_at": fetched_at,
            "cves": ",".join(raw.get("cves") or []),
            "cvss": raw.get("cvss"),
            "kev": int(bool(raw.get("kev"))),
            "run_id": run_id,
            **scored,
            "base_score": scored["score"],
            "base_reasons": scored["reasons"],
        }
        item.update(scoring.finalize(item, cfg))
        item["affinity"] = preferences.affinity(item, model)
        if db.insert_item(item):
            new += 1
    return new


def test_source(source: dict) -> dict:
    """Busca uma fonte sem gravar nada (botão "Testar" da página de fontes)."""
    cfg = config.load_config()
    settings = cfg.get("collection", {})
    with client_for(settings, retries=False) as client:
        try:
            items = fetchers.fetch_source(source, client, settings)
        except fetchers.FetchError as exc:
            return {"ok": False, "error": str(exc)}
    preview = []
    for raw in items[:8]:
        scored = scoring.score_item(raw, source.get("category", "tecnologia"), cfg)
        preview.append({"title": raw["title"], "url": raw["url"], "level": scored["level"]})
    return {"ok": True, "count": len(items), "preview": preview}


def client_for(settings: dict, retries: bool = True):
    return fetchers.make_client(
        settings.get("timeout_seconds", 20),
        allow_private=bool(settings.get("allow_private_networks")),
        max_mb=float(settings.get("max_download_mb") or fetchers.MAX_DOWNLOAD_MB),
        retries=retries,
    )


def summary_message(summary: dict) -> str:
    return f"{summary['new_items']} notícias novas, {summary['sources_failed']} fontes com erro."


def run_collection(source_ids: list[str] | None = None, workers: int = 8) -> dict:
    """Coleta todas as fontes ativas (ou só as indicadas)."""
    if not _run_lock.acquire(blocking=False):
        return {"error": "Já existe uma coleta em andamento."}
    try:
        cfg = config.load_config()
        settings = cfg.get("collection", {})
        sources = [s for s in config.load_sources() if s.get("enabled", True)]
        if source_ids:
            sources = [s for s in sources if s["id"] in source_ids]
        run_id = db.start_run()
        progress.reset()
        progress.update(running=True, total=len(sources), run_id=run_id, message="Coletando…")

        ok = failed = new_total = 0
        fetch_settings = {**settings, "http_cache": db.HttpCache()}
        with client_for(settings) as client:
            def job(source):
                progress.started(source["name"])
                started = time.monotonic()
                try:
                    items = fetchers.fetch_source(source, client, fetch_settings)
                    return source, items, None, False, started
                except fetchers.NotModified:
                    return source, [], None, True, started
                except fetchers.FetchError as exc:
                    return source, [], str(exc), False, started

            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(job, s) for s in sources]
                for future in as_completed(futures):
                    source, items, error, not_modified, started = future.result()
                    duration = int((time.monotonic() - started) * 1000)
                    if error:
                        failed += 1
                        db.add_error(run_id, source, error)
                        db.add_source_run(run_id, source["id"], False, duration)
                        log.warning("%s: %s", source["name"], error)
                    else:
                        ok += 1
                        new = process_items(source, items, cfg, run_id)
                        new_total += new
                        db.add_source_run(run_id, source["id"], True, duration, len(items), new, not_modified)
                    progress.finished(source["name"], new_total, failed)

            progress.update(message="Consultando EPSS e versões afetadas…")
            try:
                enrich = cveinfo.enrich_pending(cfg, client)
                for problem in enrich["errors"][:3]:
                    log.warning("enriquecimento: %s", problem)
            except Exception as exc:  # enriquecimento nunca derruba a coleta
                log.exception("enriquecimento falhou: %s", exc)

        db.finish_run(run_id, ok, failed, new_total)
        summary = {"run_id": run_id, "sources_ok": ok, "sources_failed": failed, "new_items": new_total}
        progress.update(running=False, current=[], message=summary_message(summary))

        after_collection(cfg, summary)

        if cfg.get("email", {}).get("send_after_collect"):
            from . import emailer
            try:
                summary["email"] = emailer.send_digest(cfg)
            except Exception as exc:
                summary["email_error"] = str(exc)
                progress.update(message=summary_message(summary) + f" E-mail não enviado: {exc}")
        return summary
    finally:
        progress.update(running=False)
        _run_lock.release()


def after_collection(cfg: dict, summary: dict) -> None:
    """Tarefas que rodam depois da coleta, sem atrapalhar o resultado dela."""
    from . import maintenance, notify, selfcheck

    for name, task in (
        ("faxina", lambda: maintenance.run_if_due(cfg)),
        ("autoauditoria", lambda: selfcheck.run_if_due(cfg)),
        ("chat", lambda: summary.__setitem__("chat", notify.send_highlights(cfg))),
    ):
        try:
            task()
        except Exception as exc:
            log.warning("%s: %s", name, exc)
            if name == "chat":
                summary["chat_error"] = str(exc)


def run_in_background(source_ids: list[str] | None = None) -> bool:
    if progress.snapshot()["running"] or _run_lock.locked():
        return False
    threading.Thread(target=run_collection, args=(source_ids,), daemon=True).start()
    return True
