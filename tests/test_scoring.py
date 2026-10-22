from monitor import config, scoring


def cfg():
    return config.load_config()


def test_word_boundaries_avoid_false_positives():
    assert scoring.matches(["rce"], "open source resource") == []
    assert scoring.matches(["rce"], "unauthenticated RCE in router") == ["rce"]
    assert scoring.matches(["cve-"], "fixes CVE-2026-1234") == ["cve-"]
    assert scoring.matches(["node"], "nodes and nodejs") == []
    assert scoring.matches(["node.js"], "Node.js 26 released") == ["node.js"]


def test_kev_is_critical():
    item = {"title": "CVE-2026-1: Fortinet bug", "summary": "", "kev": True, "cves": ["CVE-2026-1"]}
    assert scoring.score_item(item, "seguranca", cfg())["level"] == "critico"


def test_zero_day_security_news_is_critical():
    item = {"title": "Chrome zero-day patched", "summary": "", "cves": []}
    assert scoring.score_item(item, "seguranca", cfg())["level"] == "critico"


def test_my_technology_raises_priority():
    item = {"title": "Vulnerability fixed in MariaDB", "summary": "", "cves": []}
    result = scoring.score_item(item, "seguranca", cfg())
    assert result["matched_tech"] == "mariadb"
    assert result["level"] in ("alto", "critico")


def test_generic_tech_news_is_normal():
    item = {"title": "New phone announced", "summary": "Battery is bigger", "cves": []}
    assert scoring.score_item(item, "tecnologia", cfg())["level"] == "normal"


def test_cvss_9_with_my_tech_is_critical():
    item = {"title": "CVE-2026-9 (CVSS 9.8)", "summary": "Laravel debug mode", "cvss": 9.8, "cves": ["CVE-2026-9"]}
    assert scoring.score_item(item, "seguranca", cfg())["level"] == "critico"
