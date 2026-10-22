"""Servidor local (Flask) que alimenta a janela do leitor."""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets as pysecrets
import subprocess
import sys
import webbrowser
from pathlib import Path

from flask import Flask, Response, abort, g, jsonify, render_template, request

from . import __version__
from . import (backup, config, cveinfo, db, diagnostics, emailer, fetchers, health, linkcheck, logsetup,
               maintenance, notify, oauth, pipeline, preferences, presence, scheduler, secrets, selfcheck, updates)
from .sanitize import clean_html

API_TOKEN = pysecrets.token_urlsafe(24)
open_url = webbrowser.open  # o app Android troca por um que usa o navegador do celular
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
CVE_RE = re.compile(r"^CVE-\d{4}-\d{4,7}$", re.IGNORECASE)


def _same(a: str | None, b: str) -> bool:
    return hmac.compare_digest((a or "").encode(), b.encode())


def _open_folder(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - abre a pasta no Explorer
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def create_app(token: str = API_TOKEN) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["JSON_AS_ASCII"] = False

    @app.before_request
    def check_request():
        # Só atende pelo endereço local: bloqueia "DNS rebinding" (um site apontando o
        # próprio domínio para 127.0.0.1 para conversar com o programa).
        host = (request.host or "").rsplit(":", 1)[0].lower()
        if host not in LOCAL_HOSTS and not app.testing and not app.config.get("ALLOW_ANY_HOST"):
            abort(403)
        # Impede que outras páginas abertas no navegador mexam no programa.
        if request.path.startswith("/api/") and not _same(request.headers.get("X-Token"), token):
            abort(403)
        g.nonce = pysecrets.token_urlsafe(16)

    @app.after_request
    def security_headers(response: Response):
        if response.mimetype == "text/html":
            block = config.load_config().get("reader", {}).get("block_remote_images", False)
            images = "'self' data:" if block else "'self' data: https: http:"
            nonce = getattr(g, "nonce", "")
            response.headers["Content-Security-Policy"] = (
                f"default-src 'self'; script-src 'self' 'nonce-{nonce}'; style-src 'self' 'unsafe-inline'; "
                f"img-src {images}; connect-src 'self'; frame-src 'self' about:; object-src 'none'; "
                "base-uri 'self'; form-action 'self'; frame-ancestors 'self'"
            )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        return response

    @app.errorhandler(fetchers.FetchError)
    @app.errorhandler(emailer.EmailError)
    @app.errorhandler(notify.ChatError)
    @app.errorhandler(oauth.OAuthError)
    @app.errorhandler(updates.UpdateError)
    @app.errorhandler(backup.BackupError)
    @app.errorhandler(RuntimeError)
    def _known_error(exc):
        return jsonify({"error": str(exc)}), 400

    def body() -> dict:
        return request.get_json(silent=True) or {}

    # --- Páginas ----------------------------------------------------------

    @app.get("/")
    def index():
        cfg = config.load_config()
        return render_template(
            "index.html",
            token=token,
            nonce=g.nonce,
            version=__version__,
            android=config.is_android(),
            theme=cfg.get("appearance", {}).get("theme", "auto"),
            source_types=config.SOURCE_TYPES,
            presets=config.EMAIL_PRESETS,
        )

    @app.get("/email/preview")
    def email_preview():
        if not _same(request.args.get("t"), token):
            abort(403)
        area = "games" if request.args.get("area") == "games" else "tech"
        _subject, html, _ids = emailer.build_digest(config.load_config(), only_new=request.args.get("all") != "1",
                                                    area=area)
        return html

    # --- Notícias ---------------------------------------------------------

    @app.get("/api/items")
    def items():
        args = request.args
        return jsonify(db.query_items(
            view=args.get("view", "criticos"),
            search=args.get("q", "").strip(),
            unread_only=args.get("unread") == "1",
            source_id=args.get("source", ""),
            days=int(args.get("days") or 0),
            sort=args.get("sort", ""),
            level=args.get("level", ""),
        ))

    @app.get("/api/items/<int:item_id>")
    def item_detail(item_id):
        item = db.get_item(item_id)
        if not item:
            abort(404)
        item["has_article"] = bool(item.pop("article_html", None))
        item["related"] = db.related_by_cve(item)
        item["affinity_terms"] = preferences.explain(item, preferences.model_for(item))
        return jsonify(item)

    @app.get("/api/items/<int:item_id>/article")
    def item_article(item_id):
        item = db.get_item(item_id)
        if not item:
            abort(404)
        cfg = config.load_config()
        block = bool(cfg.get("reader", {}).get("block_remote_images"))
        if item.get("article_html") and request.args.get("refresh") != "1":
            # Limpa de novo: artigos salvos por versões anteriores passam pela lista de permissões.
            return jsonify({"html": clean_html(item["article_html"], item["url"], block_images=block)})
        with pipeline.client_for(cfg["collection"], retries=False) as client:
            html = fetchers.fetch_article(item["url"], client)
        db.save_article(item_id, html)
        return jsonify({"html": clean_html(html, item["url"], block_images=block)})

    @app.post("/api/items/<int:item_id>/flag")
    def item_flag(item_id):
        data = body()
        try:
            db.set_flag(item_id, data.get("flag"), data.get("value") or 0)
        except (TypeError, ValueError):
            abort(400)
        if data.get("flag") == "liked":
            preferences.refresh()
            item = db.get_item(item_id) or {}
            return jsonify({"ok": True, "preferences": preferences.status(config.area_of(item.get("category")))})
        return jsonify({"ok": True})

    @app.get("/api/preferences")
    def preferences_status():
        return jsonify(preferences.status(request.args.get("area", "tech")))

    @app.post("/api/preferences/reset")
    def preferences_reset():
        area = "games" if body().get("area") == "games" else "tech"
        conn = db.connect()
        conn.execute("UPDATE items SET liked=0 WHERE " + ("category='games'" if area == "games" else "category<>'games'"))
        conn.commit()
        preferences.refresh()
        return jsonify(preferences.status(area))

    @app.post("/api/items/mark-all-read")
    def items_mark_all():
        return jsonify({"marked": db.mark_all_read(body().get("view", "todos"))})

    @app.post("/api/heartbeat")
    def heartbeat():
        presence.beat()
        return jsonify({"ok": True})

    @app.post("/api/bye")
    def bye():
        presence.bye()
        return jsonify({"ok": True})

    @app.get("/api/counts")
    def counts():
        return jsonify(db.counts())

    @app.post("/api/open")
    def open_external():
        data = body()
        url = str(data.get("url", ""))
        if not url.startswith(("http://", "https://")):
            abort(400)
        check = linkcheck.assess(url)
        if check["suspicious"] and not data.get("confirm"):
            return jsonify({"ok": False, "needs_confirm": True, **check})
        open_url(url)
        return jsonify({"ok": True, **check})

    @app.post("/api/link-check")
    def link_check():
        return jsonify(linkcheck.assess(str(body().get("url", ""))))

    # --- Página do CVE ----------------------------------------------------

    @app.get("/api/cve/<cve_id>")
    def cve_page(cve_id):
        cve_id = cve_id.upper()
        if not CVE_RE.match(cve_id):
            abort(404)
        cfg = config.load_config()
        settings = cfg.get("collection", {})
        record, nvd_error = None, ""
        with pipeline.client_for(settings, retries=False) as client:
            try:
                record = cveinfo.nvd_record(client, cve_id, cveinfo.NvdBudget(settings.get("nvd_api_key", ""), 1))
            except fetchers.FetchError as exc:
                nvd_error = str(exc)
                cached = db.get_cve_info(cve_id)
                record = json.loads(cached["nvd"]) if cached and cached.get("nvd") else None
            info = db.get_cve_info(cve_id) or {}
            if info.get("epss") is None:
                try:
                    db.save_cve_epss(cveinfo.fetch_epss(client, [cve_id]))
                    info = db.get_cve_info(cve_id) or {}
                except fetchers.FetchError:
                    pass
        summary = cveinfo.summarize(record) if record else None
        versions = cfg.get("interests", {}).get("versions") or []
        verdict = cveinfo.check_versions(summary["ranges"], versions) if summary else ("", "")
        news = db.items_with_cve(cve_id)
        return jsonify({
            "id": cve_id,
            "nvd": summary,
            "nvd_error": nvd_error,
            "epss": info.get("epss"),
            "epss_pct": info.get("epss_pct"),
            "kev": bool(summary and summary.get("kev_added")) or any(n["kev"] for n in news),
            "verdict": verdict[0],
            "verdict_note": verdict[1],
            "news": news,
        })

    # --- Coleta -----------------------------------------------------------

    @app.post("/api/collect")
    def collect():
        started = pipeline.run_in_background(body().get("source_ids"))
        return jsonify({"started": started})

    @app.get("/api/status")
    def status():
        return jsonify({"progress": pipeline.progress.snapshot(), "last_run": db.last_run()})

    @app.get("/api/errors")
    def errors():
        run = db.last_run()
        return jsonify({
            "last_run": run,
            "current": db.errors_for_run(run["id"]) if run else [],
            "history": db.error_history(),
            "streaks": db.failure_streaks(),
            "silent": health.silent_sources(),
        })

    # --- Fontes -----------------------------------------------------------

    @app.get("/api/sources")
    def sources_list():
        return jsonify({"sources": config.load_sources(), "streaks": db.failure_streaks(),
                        "health": db.source_health(),
                        "silent": [s["source_id"] for s in health.silent_sources()]})

    @app.post("/api/sources")
    def sources_create():
        data = body()
        problems = config.validate_source(data)
        if problems:
            return jsonify({"error": " ".join(problems)}), 400
        return jsonify(config.upsert_source(data))

    @app.put("/api/sources/<source_id>")
    def sources_update(source_id):
        if not config.get_source(source_id):
            abort(404)
        data = body()
        problems = config.validate_source(data)
        if problems:
            return jsonify({"error": " ".join(problems)}), 400
        return jsonify(config.upsert_source(data, original_id=source_id))

    @app.delete("/api/sources/<source_id>")
    def sources_delete(source_id):
        config.delete_source(source_id)
        return jsonify({"ok": True})

    @app.post("/api/sources/test")
    def sources_test():
        data = body()
        data.setdefault("id", "teste")
        problems = config.validate_source(data)
        if problems:
            return jsonify({"ok": False, "error": " ".join(problems)})
        return jsonify(pipeline.test_source(data))

    @app.post("/api/sources/discover")
    def sources_discover():
        url = body().get("url", "").strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        cfg = config.load_config()
        with pipeline.client_for(cfg["collection"], retries=False) as client:
            return jsonify({"feeds": fetchers.discover_feeds(url, client)})

    # --- Configurações ----------------------------------------------------

    @app.get("/api/config")
    def config_get():
        return jsonify({"config": config.load_config()})

    @app.get("/api/email/password")
    def email_password_status():
        # Separado de /api/config: o cofre do sistema pode demorar para responder.
        username = config.load_config().get("email", {}).get("username", "")
        return jsonify({
            "has_password": secrets.has_password(username),
            "keyring_available": secrets.keyring_available(),
        })

    @app.put("/api/config")
    def config_put():
        data = body()
        if not isinstance(data.get("config"), dict):
            abort(400)
        current = config.load_config()
        before = json.dumps([current.get("filters"), current.get("interests", {}).get("versions")], sort_keys=True)
        games_before = json.dumps(current.get("games"), sort_keys=True)
        for section, values in data["config"].items():
            if isinstance(values, dict) and isinstance(current.get(section), dict):
                current[section].update(values)
        config.save_config(current)
        after = json.dumps([current.get("filters"), current.get("interests", {}).get("versions")], sort_keys=True)
        rescored = cveinfo.rescore_all(current) if before != after else 0
        if json.dumps(current.get("games"), sort_keys=True) != games_before:
            rescored += pipeline.rescore_games(current)
        return jsonify({"ok": True, "rescored": rescored})

    @app.post("/api/email/password")
    def email_password():
        data = body()
        username = config.load_config().get("email", {}).get("username", "")
        if not username:
            return jsonify({"error": "Salve primeiro o usuário (e-mail) nas configurações."}), 400
        if not secrets.set_password(username, data.get("password", "")):
            return jsonify({"error": "O cofre de senhas do sistema não está disponível. "
                                     f"Defina a variável de ambiente {secrets.ENV_VAR}."}), 400
        return jsonify({"ok": True})

    @app.post("/api/email/test")
    def email_test():
        emailer.send_test()
        return jsonify({"ok": True})

    @app.post("/api/email/send")
    def email_send():
        area = body().get("area")
        return jsonify(emailer.send_digest(only_new=not body().get("all"),
                                           area=area if area in ("tech", "games") else None))

    @app.post("/api/email/oauth/start")
    def oauth_start():
        return jsonify(oauth.start_device_flow())

    @app.get("/api/email/oauth/status")
    def oauth_status():
        return jsonify({**oauth.flow_status(), "connected": oauth.has_refresh_token()})

    @app.post("/api/email/oauth/disconnect")
    def oauth_disconnect():
        oauth.disconnect()
        return jsonify({"ok": True})

    # --- Celular (chat) ---------------------------------------------------

    @app.get("/api/chat/status")
    def chat_status():
        return jsonify({p: notify.has_secret(p) for p in notify.PROVIDERS})

    @app.post("/api/chat/secret")
    def chat_secret():
        data = body()
        notify.save_secret(str(data.get("provider")), str(data.get("value", "")))
        return jsonify({"ok": True})

    @app.post("/api/chat/test")
    def chat_test():
        notify.send_test()
        return jsonify({"ok": True})

    @app.post("/api/chat/discover")
    def chat_discover():
        return jsonify(notify.discover_telegram_chat())

    # --- Saúde do programa ------------------------------------------------

    @app.get("/api/health")
    def health_page():
        cfg = config.load_config()
        return jsonify({
            "version": __version__,
            "selfcheck": selfcheck.last_report(),
            "schedule": scheduler.status(cfg),
            "resident": scheduler.resident_installed(),
            "db": db.stats(),
            "cleanup_at": db.get_meta("cleanup_at"),
            "silent": health.silent_sources(cfg),
            "data_dir": str(config.data_dir()),
            "log_file": str(logsetup.log_file()),
        })

    @app.post("/api/diagnostics")
    def diagnostics_run():
        return jsonify({"checks": diagnostics.run(quick=bool(body().get("quick")))})

    @app.get("/api/report")
    def support_report():
        return jsonify({"text": logsetup.support_report()})

    @app.post("/api/selfcheck")
    def selfcheck_run():
        return jsonify(selfcheck.run())

    @app.post("/api/maintenance")
    def maintenance_run():
        return jsonify(maintenance.run(config.load_config(), vacuum=True))

    @app.post("/api/schedule")
    def schedule_set():
        data = body()
        cfg = config.load_config()
        if data.get("action") == "remove":
            return jsonify({"message": scheduler.uninstall(cfg)})
        if data.get("action") == "resident_on":
            return jsonify({"message": scheduler.install_resident()})
        if data.get("action") == "resident_off":
            return jsonify({"message": scheduler.uninstall_resident()})
        sched = cfg.setdefault("schedule", {})
        for key in ("time", "weekdays_only", "send_email", "alert_missed"):
            if key in data:
                sched[key] = data[key]
        return jsonify({"message": scheduler.install(cfg)})

    @app.post("/api/open-folder")
    def open_folder():
        allowed = {"dados": config.data_dir(), "logs": logsetup.log_file().parent,
                   "atualizacoes": config.data_dir() / "atualizacoes", "backups": config.data_dir() / "backups"}
        folder = allowed.get(str(body().get("folder")))
        if folder is None:
            abort(400)
        if config.is_android():
            return jsonify({"error": "No Android a pasta de dados é privada do app."}), 400
        folder.mkdir(parents=True, exist_ok=True)
        _open_folder(folder)
        return jsonify({"ok": True, "path": str(folder)})

    @app.post("/api/updates/check")
    def updates_check():
        return jsonify(updates.check())

    @app.post("/api/updates/download")
    def updates_download():
        return jsonify(updates.download())

    # --- Backup -----------------------------------------------------------

    @app.post("/api/backup/export")
    def backup_export():
        return jsonify({"path": backup.save_backup_file(), "data": backup.export_data()})

    @app.post("/api/backup/import")
    def backup_import():
        data = body()
        result = backup.import_data(data.get("backup"), replace_config=bool(data.get("replace_config", True)))
        cveinfo.rescore_all(config.load_config())
        return jsonify(result)

    @app.get("/api/opml")
    def opml_export():
        return jsonify({"opml": backup.export_opml()})

    @app.post("/api/opml")
    def opml_import():
        data = body()
        category = data.get("category") if data.get("category") in config.CATEGORIES else "tecnologia"
        return jsonify(backup.import_opml(str(data.get("opml", "")), category))

    return app
