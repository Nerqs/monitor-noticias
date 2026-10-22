import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from monitor import fetchers

FIXTURES = Path(__file__).parent / "fixtures"
SETTINGS = {"days_back": 3, "max_items_per_source": 40}


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_rss_parsing():
    xml = (FIXTURES / "feed.xml").read_bytes()
    client = client_for(lambda req: httpx.Response(200, content=xml))
    items = fetchers.fetch_rss({"id": "x", "url": "https://exemplo.com/feed"}, client, SETTINGS)
    assert len(items) == 2
    first = items[0]
    assert first["url"] == "https://exemplo.com/laravel-zero-day"  # utm removido
    assert first["cves"] == ["CVE-2026-12345"]
    assert "remote code execution" in first["summary"]
    assert "<b>" not in first["summary"]
    assert first["published"].year == 2026


def test_http_error_message():
    client = client_for(lambda req: httpx.Response(404))
    with pytest.raises(fetchers.FetchError, match="404"):
        fetchers.fetch_rss({"id": "x", "url": "https://exemplo.com/feed"}, client, SETTINGS)


def test_invalid_feed():
    client = client_for(lambda req: httpx.Response(200, text="<html><body>oi</body></html>"))
    with pytest.raises(fetchers.FetchError):
        fetchers.fetch_rss({"id": "x", "url": "https://exemplo.com/feed"}, client, SETTINGS)


def test_timeout_message():
    def handler(req):
        raise httpx.ReadTimeout("timeout", request=req)
    with pytest.raises(fetchers.FetchError, match="Tempo esgotado"):
        fetchers.fetch_rss({"id": "x", "url": "https://exemplo.com/feed"}, client_for(handler), SETTINGS)


def test_cisa_kev_filters_by_date():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    old = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    data = {"vulnerabilities": [
        {"cveID": "CVE-2026-1111", "vendorProject": "Acme", "product": "Router", "vulnerabilityName": "Acme Router RCE",
         "dateAdded": today, "shortDescription": "Bad bug.", "requiredAction": "Patch.", "dueDate": today,
         "knownRansomwareCampaignUse": "Known"},
        {"cveID": "CVE-2020-0001", "dateAdded": old, "vulnerabilityName": "Old"},
    ]}
    client = client_for(lambda req: httpx.Response(200, json=data))
    items = fetchers.fetch_cisa_kev({"id": "kev", "url": "https://cisa/kev.json"}, client, SETTINGS)
    assert len(items) == 1
    assert items[0]["kev"] is True
    assert "ransomware" in items[0]["summary"]


def test_nvd_parsing_and_params():
    seen = {}
    data = {"vulnerabilities": [{"cve": {
        "id": "CVE-2026-2222", "published": "2026-10-21T10:15:00.000",
        "descriptions": [{"lang": "en", "value": "SQL injection in PHP app."}],
        "metrics": {"cvssMetricV31": [{"cvssData": {"baseScore": 9.8}}]},
    }}]}

    def handler(req):
        seen.update(dict(req.url.params))
        seen["apiKey"] = req.headers.get("apiKey")
        return httpx.Response(200, json=data)

    items = fetchers.fetch_nvd({"id": "nvd", "url": "https://nvd/api"}, client_for(handler),
                               {**SETTINGS, "nvd_api_key": "abc"})
    assert seen["cvssV3Severity"] == "CRITICAL"
    assert seen["apiKey"] == "abc"
    assert items[0]["cvss"] == 9.8
    assert items[0]["title"] == "CVE-2026-2222 (CVSS 9.8)"


def test_github_advisories():
    data = [{"ghsa_id": "GHSA-1", "cve_id": "CVE-2026-3333", "summary": "Prototype pollution",
             "description": "Details", "html_url": "https://github.com/advisories/GHSA-1",
             "published_at": "2026-10-21T00:00:00Z", "cvss": {"score": 9.1},
             "vulnerabilities": [{"package": {"ecosystem": "npm", "name": "lodash"}}]}]
    client = client_for(lambda req: httpx.Response(200, json=data))
    items = fetchers.fetch_github_advisories({"id": "gh", "url": "https://api.github.com/advisories"}, client, SETTINGS)
    assert items[0]["title"] == "lodash (npm): Prototype pollution"
    assert items[0]["cves"] == ["CVE-2026-3333"]


def test_scrape():
    html = (FIXTURES / "page.html").read_text()
    client = client_for(lambda req: httpx.Response(200, text=html))
    source = {"id": "s", "url": "https://blog.exemplo.com/", "item_selector": "article.post",
              "title_selector": "h2", "summary_selector": "p"}
    items = fetchers.fetch_scrape(source, client, SETTINGS)
    assert [i["url"] for i in items] == ["https://blog.exemplo.com/p/1", "https://blog.exemplo.com/p/2"]
    assert items[0]["summary"] == "Resumo um"
    assert items[0]["published"] is not None


def test_scrape_selector_not_found():
    client = client_for(lambda req: httpx.Response(200, text="<html></html>"))
    source = {"id": "s", "url": "https://x/", "item_selector": ".nada", "title_selector": "h2"}
    with pytest.raises(fetchers.FetchError, match="layout"):
        fetchers.fetch_scrape(source, client, SETTINGS)


def test_discover_feed_from_link_tag():
    html = (FIXTURES / "page.html").read_text()
    client = client_for(lambda req: httpx.Response(200, text=html, headers={"content-type": "text/html"}))
    feeds = fetchers.discover_feeds("https://blog.exemplo.com/", client)
    assert feeds == [{"url": "https://blog.exemplo.com/feed.xml", "title": "Blog feed"}]


def test_article_extraction_strips_scripts():
    html = "<html><body><script>alert(1)</script><article><h1>T</h1>" + "<p onclick='x()'>Texto longo do artigo. </p>" * 20 + \
           "<a href='/rel'>link</a></article></body></html>"
    client = client_for(lambda req: httpx.Response(200, text=html))
    out = fetchers.fetch_article("https://site.com/a", client)
    assert "<script" not in out and "onclick" not in out
    assert "Texto longo" in out
