from datetime import datetime, timezone

from monitor import config, db, emailer, pipeline, web


def add_items():
    source = {"id": "src", "name": "Fonte", "category": "seguranca"}
    raw = [
        {"uid": "1", "title": "Zero-day in PHP exploited", "url": "https://a/1", "summary": "",
         "published": datetime.now(timezone.utc), "cves": ["CVE-2026-1"], "cvss": None, "kev": False},
        {"uid": "2", "title": "Another post on CVE-2026-1", "url": "https://a/2", "summary": "",
         "published": None, "cves": ["CVE-2026-1"], "cvss": None, "kev": False},
        {"uid": "3", "title": "Conference recap", "url": "https://a/3", "summary": "",
         "published": None, "cves": [], "cvss": None, "kev": False},
    ]
    return pipeline.process_items(source, raw, config.load_config(), None)


def test_dedupe():
    assert add_items() == 3
    assert add_items() == 0


def test_views_and_related():
    add_items()
    crit = db.query_items("criticos")
    assert crit[0]["title"].startswith("Zero-day")
    assert any(i["title"].startswith("Zero-day") for i in db.query_items("minhas"))
    related = db.related_by_cve(db.get_item(crit[0]["id"]))
    assert [r["url"] for r in related] == ["https://a/2"]


def test_api_requires_token():
    app = web.create_app("segredo").test_client()
    assert app.get("/api/items").status_code == 403
    assert app.get("/api/items", headers={"X-Token": "segredo"}).status_code == 200


def test_source_crud_via_api():
    client = web.create_app("t").test_client()
    h = {"X-Token": "t"}
    bad = client.post("/api/sources", json={"name": "", "url": "x", "type": "rss", "category": "seguranca"}, headers=h)
    assert bad.status_code == 400
    new = client.post("/api/sources", json={"name": "Meu Blog", "url": "https://blog/feed", "type": "rss",
                                            "category": "tecnologia", "language": "pt", "enabled": True}, headers=h).json
    assert new["id"] == "meu-blog"
    client.put("/api/sources/meu-blog", json={**new, "enabled": False}, headers=h)
    assert config.get_source("meu-blog")["enabled"] is False
    client.delete("/api/sources/meu-blog", headers=h)
    assert config.get_source("meu-blog") is None


def test_collection_records_errors(monkeypatch):
    config.save_sources([
        {"id": "ok", "name": "OK", "type": "rss", "url": "https://ok", "category": "seguranca", "enabled": True},
        {"id": "bad", "name": "Ruim", "type": "rss", "url": "https://bad", "category": "seguranca", "enabled": True},
    ])

    def fake_fetch(source, client, settings):
        if source["id"] == "bad":
            raise pipeline.fetchers.FetchError("HTTP 404: endereço não encontrado.")
        return [{"uid": "x", "title": "Notícia", "url": "https://ok/1", "summary": "", "published": None,
                 "cves": [], "cvss": None, "kev": False}]

    monkeypatch.setattr(pipeline.fetchers, "fetch_source", fake_fetch)
    summary = pipeline.run_collection()
    assert summary["new_items"] == 1 and summary["sources_failed"] == 1
    errors = db.errors_for_run(summary["run_id"])
    assert errors[0]["source_id"] == "bad"
    assert db.failure_streaks() == {"bad": 1}


def test_email_digest_and_send(monkeypatch):
    add_items()
    cfg = config.load_config()
    cfg["email"].update({"to_addresses": ["eu@exemplo.com"], "username": ""})
    subject, html, ids = emailer.build_digest(cfg)
    assert "CRÍTICO" in html and len(ids) == 3

    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def ehlo(self): pass
        def starttls(self, context=None): pass
        def login(self, u, p): pass
        def send_message(self, msg): sent.append(msg)

    monkeypatch.setattr(emailer.smtplib, "SMTP", FakeSMTP)
    result = emailer.send_digest(cfg)
    assert result["sent"] and sent[0]["To"] == "eu@exemplo.com"
    assert emailer.send_digest(cfg)["sent"] is False  # nada novo na segunda vez


def test_config_merge_keeps_new_defaults(data_dir):
    cfg = config.load_config()
    del cfg["appearance"]
    config.save_config(cfg)
    assert config.load_config()["appearance"]["theme"] == "auto"


def test_index_sections_and_tabs():
    import re

    html = web.create_app("t").test_client().get("/").get_data(as_text=True)
    assert '<div class="nav-label">Tecnologia</div>' in html
    assert "Leitura</div>" not in html
    for page in ("config", "saude"):
        body = html.split(f'id="page-{page}"', 1)[1].split("</main>", 1)[0]
        tabs = set(re.findall(r'role="tab" data-tab="([a-z]+)"', body))
        cards = re.findall(r'<div class="card[^"]*"( data-tab="([a-z]+)")?>', body)
        # Todo cartão diz em que aba fica, e toda aba usada existe na barra.
        assert cards and all(tab for _, tab in cards)
        assert {tab for _, tab in cards} == tabs
