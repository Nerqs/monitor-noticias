import httpx
import pytest

from monitor import backup, config, fetchers, linkcheck, logsetup, netguard, web
from monitor.sanitize import clean_html


def test_sanitize_allowlist():
    dirty = """<div onclick="x()"><p style="color:red">Texto <a href="javascript:alert(1)">ruim</a>
      <a href="/rel">bom</a></p><script>alert(1)</script><iframe src="https://x"></iframe>
      <img src="data:image/png;base64,AAA"><img src="https://img/a.png" onerror="x()" width="100">
      <svg><script>1</script></svg><form><input></form><custom-tag>fica o texto</custom-tag></div>"""
    html = clean_html(dirty, "https://site.com/artigo")
    assert "script" not in html and "iframe" not in html and "onclick" not in html and "onerror" not in html
    assert "javascript:" not in html and "data:image" not in html and "style=" not in html and "<form" not in html
    assert 'href="https://site.com/rel"' in html and 'src="https://img/a.png"' in html
    assert "fica o texto" in html and "<custom-tag" not in html
    assert 'referrerpolicy="no-referrer"' in html


def test_sanitize_blocks_images():
    html = clean_html('<p><img src="https://track.er/p.gif" alt="foto"></p>', block_images=True)
    assert "<img" not in html and "imagem bloqueada: foto" in html


@pytest.mark.parametrize("url", ["http://127.0.0.1:5000/api", "http://localhost/", "http://192.168.0.1/",
                                 "http://10.1.2.3/", "http://[::1]/", "http://169.254.169.254/latest",
                                 "http://roteador.local/", "file:///etc/passwd"])
def test_netguard_blocks_internal(url):
    with pytest.raises(netguard.BlockedAddress):
        netguard.check_url(url)


def test_netguard_allows_public_ip():
    netguard.check_url("https://8.8.8.8/")


def test_client_blocks_redirect_to_internal():
    def handler(request):
        return httpx.Response(302, headers={"location": "http://127.0.0.1:8080/api/config"})

    client = fetchers.make_client(transport=httpx.MockTransport(handler))
    with pytest.raises(fetchers.FetchError, match="rede interna"):
        fetchers.http_get(client, "https://8.8.8.8/feed")


def _mock_client(handler, **kw):
    return fetchers.make_client(allow_private=True, transport=httpx.MockTransport(handler), **kw)


def test_download_size_limit():
    client = _mock_client(lambda r: httpx.Response(200, content=b"x" * 3_000_000), max_mb=1)
    with pytest.raises(fetchers.FetchError, match="grande demais"):
        fetchers.http_get(client, "https://a/feed")


def test_xml_entities_rejected():
    bomb = b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]><rss><channel></channel></rss>'
    client = _mock_client(lambda r: httpx.Response(200, content=bomb))
    with pytest.raises(fetchers.FetchError, match="ENTITY"):
        fetchers.fetch_rss({"id": "x", "url": "https://a/feed"}, client, {})


def test_retry_then_success():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503) if len(calls) < 3 else httpx.Response(200, text="ok")

    assert fetchers.http_get(_mock_client(handler), "https://a/").text == "ok"
    assert len(calls) == 3


def test_conditional_get_not_modified():
    from monitor import db

    seen = []

    def handler(request):
        seen.append(dict(request.headers))
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, text="x", headers={"etag": '"v1"'})

    cache = db.HttpCache()
    client = _mock_client(handler)
    fetchers.http_get(client, "https://a/feed", cache=cache)
    with pytest.raises(fetchers.NotModified):
        fetchers.http_get(client, "https://a/feed", cache=cache)


def test_host_header_and_csp():
    client = web.create_app("t").test_client()
    assert client.get("/", headers={"Host": "evil.example:5000"}).status_code == 403
    resp = client.get("/", headers={"Host": "127.0.0.1:5000"})
    assert resp.status_code == 200
    csp = resp.headers["Content-Security-Policy"]
    nonce = csp.split("'nonce-")[1].split("'")[0]
    assert f'nonce="{nonce}"' in resp.get_data(as_text=True)
    assert "object-src 'none'" in csp and resp.headers["X-Content-Type-Options"] == "nosniff"


def test_csp_blocks_remote_images_when_configured():
    cfg = config.load_config()
    cfg["reader"]["block_remote_images"] = True
    config.save_config(cfg)
    csp = web.create_app("t").test_client().get("/").headers["Content-Security-Policy"]
    assert "img-src 'self' data:;" in csp


def test_open_suspicious_link_needs_confirm(monkeypatch):
    opened = []
    monkeypatch.setattr(web.webbrowser, "open", opened.append)
    client = web.create_app("t").test_client()
    h = {"X-Token": "t"}
    res = client.post("/api/open", json={"url": "https://xn--pple-43d.com/login"}, headers=h).json
    assert res["needs_confirm"] and not opened
    client.post("/api/open", json={"url": "https://xn--pple-43d.com/login", "confirm": True}, headers=h)
    client.post("/api/open", json={"url": "https://github.com/x"}, headers=h)
    assert len(opened) == 2


def test_linkcheck():
    assert linkcheck.assess("https://rnicrosoft.com/")["suspicious"]
    assert linkcheck.assess("https://user@github.com/")["suspicious"]
    assert not linkcheck.assess("https://nvd.nist.gov/vuln/detail/CVE-2026-1")["suspicious"]


def test_redaction():
    text = ("token=abc123 https://api.telegram.org/bot123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw/send "
            "https://discord.com/api/webhooks/1/abc ghp_abcdefghijklmnopqrstuvwxyz123")
    out = logsetup.redact(text)
    assert "abc123" not in out and "AAHdq" not in out and "webhooks/1" not in out and "ghp_" not in out


def test_backup_excludes_secrets():
    cfg = config.load_config()
    cfg["collection"]["github_token"] = "segredo"
    config.save_config(cfg)
    assert "segredo" not in str(backup.export_data())
