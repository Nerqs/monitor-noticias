"""Servidor local (Flask) que alimenta a janela do leitor."""

from __future__ import annotations

import secrets as pysecrets
import webbrowser

from flask import Flask, abort, jsonify, render_template, request

from . import config, db, emailer, fetchers, pipeline, preferences, secrets

API_TOKEN = pysecrets.token_urlsafe(24)


def create_app(token: str = API_TOKEN) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["JSON_AS_ASCII"] = False

    @app.before_request
    def check_token():
        # Impede que outras páginas abertas no navegador mexam no programa.
        if request.path.startswith("/api/") and request.headers.get("X-Token") != token:
            abort(403)

    @app.errorhandler(fetchers.FetchError)
    @app.errorhandler(emailer.EmailError)
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
            theme=cfg.get("appearance", {}).get("theme", "auto"),
            source_types=config.SOURCE_TYPES,
            presets=config.EMAIL_PRESETS,
        )

    @app.get("/email/preview")
    def email_preview():
        if request.args.get("t") != token:
            abort(403)
        _subject, html, _ids = emailer.build_digest(config.load_config(), only_new=request.args.get("all") != "1")
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
        ))

    @app.get("/api/items/<int:item_id>")
    def item_detail(item_id):
        item = db.get_item(item_id)
        if not item:
            abort(404)
        item["has_article"] = bool(item.pop("article_html", None))
        item["related"] = db.related_by_cve(item)
        item["affinity_terms"] = preferences.explain(item, preferences.get_model())
        return jsonify(item)

    @app.get("/api/items/<int:item_id>/article")
    def item_article(item_id):
        item = db.get_item(item_id)
        if not item:
            abort(404)
        if item.get("article_html") and request.args.get("refresh") != "1":
            return jsonify({"html": item["article_html"]})
        cfg = config.load_config()
        with fetchers.make_client(cfg["collection"].get("timeout_seconds", 20)) as client:
            html = fetchers.fetch_article(item["url"], client)
        db.save_article(item_id, html)
        return jsonify({"html": html})

    @app.post("/api/items/<int:item_id>/flag")
    def item_flag(item_id):
        data = body()
        try:
            db.set_flag(item_id, data.get("flag"), data.get("value") or 0)
        except (TypeError, ValueError):
            abort(400)
        if data.get("flag") == "liked":
            preferences.refresh()
            return jsonify({"ok": True, "preferences": preferences.status()})
        return jsonify({"ok": True})

    @app.get("/api/preferences")
    def preferences_status():
        return jsonify(preferences.status())

    @app.post("/api/preferences/reset")
    def preferences_reset():
        conn = db.connect()
        conn.execute("UPDATE items SET liked=0")
        conn.commit()
        preferences.refresh()
        return jsonify(preferences.status())

    @app.post("/api/items/mark-all-read")
    def items_mark_all():
        return jsonify({"marked": db.mark_all_read(body().get("view", "todos"))})

    @app.get("/api/counts")
    def counts():
        return jsonify(db.counts())

    @app.post("/api/open")
    def open_external():
        url = body().get("url", "")
        if not url.startswith(("http://", "https://")):
            abort(400)
        webbrowser.open(url)
        return jsonify({"ok": True})

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
        })

    # --- Fontes -----------------------------------------------------------

    @app.get("/api/sources")
    def sources_list():
        return jsonify({"sources": config.load_sources(), "streaks": db.failure_streaks()})

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
        with fetchers.make_client(cfg["collection"].get("timeout_seconds", 20)) as client:
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
        for section, values in data["config"].items():
            if isinstance(values, dict) and isinstance(current.get(section), dict):
                current[section].update(values)
        config.save_config(current)
        return jsonify({"ok": True})

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
        return jsonify(emailer.send_digest(only_new=not body().get("all")))

    return app
