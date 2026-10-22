import hashlib
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from monitor import (backup, config, cveinfo, db, diagnostics, fetchers, health, maintenance, notify, pipeline,
                     scheduler, scoring, secrets, updates, web)

SOURCE = {"id": "src", "name": "Fonte", "category": "seguranca"}


def raw(uid, title, cves=(), summary=""):
    return {"uid": uid, "title": title, "url": f"https://a/{uid}", "summary": summary, "published": None,
            "cves": list(cves), "cvss": None, "kev": False}


# --- Versões e EPSS -----------------------------------------------------------

@pytest.mark.parametrize("version,rng,expected", [
    ("8.2.10", {"version": "*", "start_incl": "8.2.0", "end_excl": "8.2.12"}, True),
    ("8.2.12", {"version": "*", "start_incl": "8.2.0", "end_excl": "8.2.12"}, False),
    ("8.1.5", {"version": "*", "start_incl": "8.2.0", "end_excl": "8.2.12"}, False),
    ("10.11.6", {"version": "10.11.6"}, True),
    ("10.11.6", {"version": "10.11"}, True),
    ("10.11.6", {"version": "10.6"}, False),
    ("20.11.1", {"version": "*", "end_incl": "20.11.1"}, True),
    ("20.11.1", {"version": "*"}, True),
])
def test_in_range(version, rng, expected):
    assert cveinfo.in_range(version, rng) is expected


NVD_RECORD = {
    "id": "CVE-2026-1111", "published": "2026-10-20T10:00:00.000", "vulnStatus": "Analyzed",
    "descriptions": [{"lang": "en", "value": "RCE in PHP before 8.2.12"}],
    "metrics": {"cvssMetricV31": [{"cvssData": {"baseScore": 9.8, "baseSeverity": "CRITICAL",
                                                "vectorString": "CVSS:3.1/AV:N"}}]},
    "references": [{"url": "https://php.net/fix", "tags": ["Patch"]}, {"url": "https://x/poc", "tags": ["Exploit"]}],
    "configurations": [{"nodes": [{"cpeMatch": [{"vulnerable": True, "criteria": "cpe:2.3:a:php:php:*:*:*:*:*:*:*:*",
                                                 "versionStartIncluding": "8.2.0", "versionEndExcluding": "8.2.12"}]}]}],
}


def test_check_versions_and_summary():
    s = cveinfo.summarize(NVD_RECORD)
    assert s["cvss"] == 9.8 and s["has_patch"] and s["has_exploit"]
    assert cveinfo.check_versions(s["ranges"], [{"name": "PHP", "cpe": "php:php", "version": "8.2.10"}])[0] == "afeta"
    assert cveinfo.check_versions(s["ranges"], [{"name": "PHP", "cpe": "php:php", "version": "8.3.1"}])[0] == "nao_afeta"
    assert cveinfo.check_versions(s["ranges"], [{"name": "Node", "cpe": "nodejs:node.js", "version": "20"}])[0] == ""


def test_enrichment_marks_affected_and_epss(monkeypatch):
    cfg = config.load_config()
    cfg["interests"]["versions"] = [{"name": "PHP", "cpe": "php:php", "version": "8.2.10"}]
    config.save_config(cfg)
    pipeline.process_items(SOURCE, [raw("1", "New PHP flaw CVE-2026-1111", ["CVE-2026-1111"])], cfg, None)
    monkeypatch.setattr(cveinfo, "fetch_epss", lambda client, cves: {"CVE-2026-1111": (0.62, 0.99)})
    monkeypatch.setattr(cveinfo, "nvd_record", lambda client, cve, budget=None: NVD_RECORD)
    cveinfo.enrich_pending(cfg, client=None)
    item = db.query_items("todos")[0]
    assert item["version_verdict"] == "afeta" and item["epss"] == 0.62
    assert item["level"] == "critico" and "Afeta sua versão: PHP 8.2.10" in item["reasons"]
    assert item["cvss"] == 9.8
    assert any(i["id"] == item["id"] for i in db.query_items("minhas"))

    # Mudou a versão: recalcula sem internet, usando o cache do NVD.
    db.save_cve_nvd("CVE-2026-1111", json.dumps(NVD_RECORD))
    cfg["interests"]["versions"][0]["version"] = "8.3.0"
    assert cveinfo.rescore_all(cfg) == 1
    assert db.query_items("todos")[0]["version_verdict"] == "nao_afeta"


def test_filters_mute_and_highlight():
    cfg = config.load_config()
    cfg["filters"]["rules"] = [{"term": "iphone", "action": "mute", "source": ""},
                               {"term": "laravel", "action": "highlight", "source": "src"}]
    pipeline.process_items(SOURCE, [raw("1", "Novo iPhone lançado"), raw("2", "Laravel 12 sai hoje")], cfg, None)
    titles = [i["title"] for i in db.query_items("todos")]
    assert titles == ["Laravel 12 sai hoje"]
    assert [i["title"] for i in db.query_items("silenciadas")] == ["Novo iPhone lançado"]
    laravel = db.query_items("todos")[0]
    assert "Destaque seu: laravel" in laravel["reasons"]
    assert db.counts()["todos"] == 1


def test_finalize_epss_levels():
    base = {"base_score": 45, "base_reasons": "x"}
    assert scoring.finalize({**base, "epss": 0.7}, {})["level"] == "critico"
    assert scoring.finalize({**base, "epss": 0.2}, {})["score"] == 60
    assert scoring.finalize({**base, "epss": 0.01}, {})["score"] == 45


# --- Busca --------------------------------------------------------------------

def test_full_text_search():
    pipeline.process_items(SOURCE, [raw("1", "Falha de execução remota no PHPMailer", summary="atualize já"),
                                    raw("2", "Lançamento do Node.js 24"),
                                    raw("3", "PHP 8.4 traz novidades", ["CVE-2026-2222"])], config.load_config(), None)
    assert db.fts_available()
    titles = lambda q: {i["title"] for i in db.query_items("todos", search=q)}
    assert titles("execucao") == {"Falha de execução remota no PHPMailer"}  # sem acento
    assert titles("php") == {"Falha de execução remota no PHPMailer", "PHP 8.4 traz novidades"}  # prefixo
    assert titles("php -phpmailer") == {"PHP 8.4 traz novidades"}
    assert titles("CVE-2026-2222") == {"PHP 8.4 traz novidades"}
    assert db.query_items("todos", search="php", sort="relevancia")
    assert db.query_items("todos", search='"; DROP TABLE items; --') == []


# --- Saúde, fontes silenciosas, faxina, agendamento --------------------------------

def test_source_health_and_silent(monkeypatch):
    config.save_sources([{"id": "a", "name": "A", "type": "rss", "url": "https://a", "category": "tecnologia",
                          "enabled": True}])
    conn = db.connect()
    old = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("INSERT INTO source_runs (source_id, ok, duration_ms, at) VALUES ('a', 1, 800, ?)", (old,))
    db.add_source_run(1, "a", True, 1200)
    db.add_source_run(2, "a", False, 3000)
    h = db.source_health()["a"]
    assert h["runs"] == 3 and h["success_rate"] == 67 and h["median_ms"] in (800, 1200)
    silent = health.silent_sources()
    assert silent and silent[0]["source_id"] == "a" and silent[0]["days"] is None
    pipeline.process_items({"id": "a", "name": "A", "category": "tecnologia"}, [raw("n", "novidade")],
                           config.load_config(), None)
    assert health.silent_sources() == []


def test_collection_records_source_runs_and_not_modified(monkeypatch):
    config.save_sources([
        {"id": "ok", "name": "OK", "type": "rss", "url": "https://ok", "category": "seguranca", "enabled": True},
        {"id": "same", "name": "Igual", "type": "rss", "url": "https://same", "category": "seguranca", "enabled": True},
    ])

    def fake_fetch(source, client, settings):
        assert settings["http_cache"] is not None
        if source["id"] == "same":
            raise fetchers.NotModified()
        return [raw("x", "Notícia")]

    monkeypatch.setattr(pipeline.fetchers, "fetch_source", fake_fetch)
    summary = pipeline.run_collection()
    assert summary["sources_ok"] == 2 and summary["sources_failed"] == 0
    rows = {r["source_id"]: r for r in db.connect().execute("SELECT * FROM source_runs")}
    assert rows["same"]["not_modified"] == 1 and rows["ok"]["new_items"] == 1


def test_cleanup_keeps_starred_and_rated():
    pipeline.process_items(SOURCE, [raw("1", "velha"), raw("2", "favorita"), raw("3", "curtida"), raw("4", "nova")],
                           config.load_config(), None)
    conn = db.connect()
    conn.execute("UPDATE items SET fetched_at=datetime('now','-200 days') WHERE uid IN ('1','2','3')")
    conn.execute("UPDATE items SET starred=1 WHERE uid='2'")
    conn.execute("UPDATE items SET liked=1 WHERE uid='3'")
    conn.commit()
    result = maintenance.run(config.load_config())
    assert result["items"] == 1
    assert {i["title"] for i in db.query_items("todos")} == {"favorita", "curtida", "nova"}
    assert maintenance.run_if_due(config.load_config()) is None  # já rodou hoje


def test_schedule_missed(monkeypatch):
    cfg = config.load_config()
    cfg["schedule"].update(installed=True, time="07:00", weekdays_only=False)
    monkeypatch.setattr(scheduler, "is_installed", lambda: True)
    now = datetime(2026, 10, 22, 9, 0).astimezone()
    assert scheduler.status(cfg, now)["missed"] is True
    run_id = db.start_run()
    db.finish_run(run_id, 1, 0, 0)
    assert scheduler.status(cfg, datetime.now().astimezone().replace(hour=23, minute=59))["missed"] is False
    early = datetime.now().astimezone().replace(hour=6, minute=0)
    assert scheduler.status(cfg, early)["missed"] is False


def test_program_command_points_to_run_py():
    cmd = scheduler.program_command("--so-coletar")
    assert cmd[-1] == "--so-coletar" and cmd[-2].endswith("run.py")


def test_quick_diagnostics_and_report():
    checks = diagnostics.run(quick=True)
    names = {c["name"] for c in checks}
    assert {"Python", "Bibliotecas", "Banco de dados", "Pasta de dados"} <= names
    assert all(c["status"] in ("ok", "aviso", "erro") for c in checks)
    client = web.create_app("t").test_client()
    report = client.get("/api/report", headers={"X-Token": "t"}).json["text"]
    assert "Relatório do Monitor" in report and "Diagnóstico rápido" in report


def test_health_endpoint():
    data = web.create_app("t").test_client().get("/api/health", headers={"X-Token": "t"}).json
    assert data["schedule"]["installed"] is False and "db" in data


# --- Backup e OPML ------------------------------------------------------------------

def test_backup_roundtrip(data_dir, monkeypatch):
    pipeline.process_items(SOURCE, [raw("1", "Gostei disso"), raw("2", "Favorita")], config.load_config(), None)
    conn = db.connect()
    conn.execute("UPDATE items SET liked=1 WHERE uid='1'")
    conn.execute("UPDATE items SET starred=1 WHERE uid='2'")
    conn.commit()
    config.upsert_source({"name": "Meu Blog", "type": "rss", "url": "https://meu/feed", "category": "tecnologia"})
    data = backup.export_data()
    assert len(data["items"]) == 2

    db.close()
    for f in data_dir.iterdir():
        if f.is_file():
            f.unlink()
    result = backup.import_data(json.loads(json.dumps(data)))
    assert result["items"] == 2 and result["sources"] >= 1
    assert config.get_source("meu-blog")
    assert db.query_items("favoritos")[0]["title"] == "Favorita"
    assert db.connect().execute("SELECT liked FROM items WHERE uid='1'").fetchone()[0] == 1


def test_opml_import_export():
    opml = """<?xml version="1.0"?><opml version="2.0"><body><outline text="Tech">
      <outline text="Blog A" xmlUrl="https://a.com/feed"/><outline text="Ruim" xmlUrl="javascript:x"/>
    </outline></body></opml>"""
    assert backup.import_opml(opml) == {"added": 1, "skipped": 1}
    assert backup.import_opml(opml)["added"] == 0
    assert 'xmlUrl="https://a.com/feed"' in backup.export_opml()
    with pytest.raises(backup.BackupError):
        backup.import_opml('<!DOCTYPE x [<!ENTITY a "b">]><opml/>')
    with pytest.raises(backup.BackupError):
        backup.import_data({"qualquer": 1})


# --- Celular (chat) -----------------------------------------------------------------

def test_telegram_highlights(monkeypatch):
    sent = []

    def handler(request):
        sent.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(pipeline, "client_for",
                        lambda settings, retries=True: fetchers.make_client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(secrets, "get_secret", lambda name: "123:ABC" if name == "telegram_token" else None)
    cfg = config.load_config()
    cfg["chat"].update(enabled=True, provider="telegram", telegram_chat_id="42")
    pipeline.process_items(SOURCE, [raw("1", "Zero-day in PHP exploited <script>")], cfg, None)
    assert notify.send_highlights(cfg) == {"sent": 1}
    url, payload = sent[0]
    assert url.endswith("/bot123:ABC/sendMessage") and payload["chat_id"] == "42"
    assert "&lt;script&gt;" in payload["text"]
    assert notify.send_highlights(cfg) == {"sent": 0}  # não repete


def test_discord_and_teams_payloads(monkeypatch):
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(204)

    monkeypatch.setattr(pipeline, "client_for",
                        lambda settings, retries=True: fetchers.make_client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(secrets, "get_secret", lambda name: "https://hooks.example/abc")
    item = {"title": "T", "source_name": "S", "level": "critico", "url": "https://x", "kev": 1}
    cfg = config.load_config()
    cfg["chat"]["provider"] = "discord"
    notify.send(cfg, "Título", [item])
    cfg["chat"]["provider"] = "teams"
    notify.send(cfg, "Título", [item])
    assert "**[CRÍTICO]** T" in sent[0]["content"]
    assert sent[1]["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"


# --- Atualização verificada ------------------------------------------------------------

def test_update_download_verifies_hash(monkeypatch):
    package = b"conteudo do pacote"
    good = hashlib.sha256(package).hexdigest()
    name = updates.asset_name()

    def make(sums):
        def handler(request):
            path = request.url.path
            if path.endswith("/latest"):
                return httpx.Response(200, json={"tag_name": "v9.9.9", "assets": [
                    {"name": name, "url": "https://api.github.com/assets/1"},
                    {"name": "SHA256SUMS.txt", "url": "https://api.github.com/assets/2"}]})
            if path.endswith("/1"):
                return httpx.Response(200, content=package)
            return httpx.Response(200, text=f"{sums}  {name}\n")
        return handler

    for sums, ok in ((good, True), ("0" * 64, False)):
        transport = httpx.MockTransport(make(sums))
        monkeypatch.setattr(pipeline, "client_for",
                            lambda settings, retries=True, t=transport: fetchers.make_client(transport=t))
        if ok:
            result = updates.download()
            assert result["sha256"] == good and result["version"] == "9.9.9"
        else:
            with pytest.raises(updates.UpdateError, match="não confere"):
                updates.download()
    assert updates.parse_sums(f"{good} *{name}") == {name: good}
