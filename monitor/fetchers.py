"""Coletores de cada tipo de fonte.

Cada coletor recebe a fonte (dict do ``sources.yaml``) e devolve uma lista de
notícias "cruas" no formato::

    {"uid", "title", "url", "summary", "published", "cves", "cvss", "kev"}

Qualquer problema vira ``FetchError`` com uma mensagem legível, que vai para
o relatório de erros.
"""

from __future__ import annotations

import calendar
import hashlib
import html
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import feedparser
import httpx
from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (compatible; MonitorNoticias/1.0; +https://github.com/Nerqs/monitor-noticias)"
CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE)


class FetchError(Exception):
    pass


# --- Utilidades -----------------------------------------------------------

def make_client(timeout: float = 20) -> httpx.Client:
    return httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"},
    )


def http_get(client: httpx.Client, url: str, **kwargs) -> httpx.Response:
    try:
        response = client.get(url, **kwargs)
    except httpx.TimeoutException:
        raise FetchError("Tempo esgotado: o site demorou demais para responder.")
    except httpx.ConnectError as exc:
        raise FetchError(f"Não foi possível conectar ao site ({exc}).")
    except httpx.HTTPError as exc:
        raise FetchError(f"Erro de rede: {exc}")
    if response.status_code >= 400:
        hints = {
            403: "acesso negado; o site pode estar bloqueando robôs",
            404: "endereço não encontrado; o feed pode ter mudado de lugar",
            429: "muitas requisições; tente mais tarde ou use uma chave de API",
        }
        hint = hints.get(response.status_code) or ("erro no servidor do site" if response.status_code >= 500 else "")
        raise FetchError(f"HTTP {response.status_code}" + (f": {hint}." if hint else "."))
    return response


def clean_text(value: str | None, limit: int = 600) -> str:
    if not value:
        return ""
    text = BeautifulSoup(value, "html.parser").get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def find_cves(*texts: str) -> list[str]:
    found = []
    for text in texts:
        for match in CVE_RE.findall(text or ""):
            cve = match.upper()
            if cve not in found:
                found.append(cve)
    return found


def parse_date(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d"):
        try:
            parsed = datetime.fromisoformat(text) if fmt is None else datetime.strptime(text, fmt)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def uid_for(source_id: str, key: str) -> str:
    return hashlib.sha1(f"{source_id}|{key}".encode()).hexdigest()


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    url = re.sub(r"[?&]utm_[^=&]+=[^&]*", "", url)
    return url.rstrip("?&")


# --- RSS / Atom -----------------------------------------------------------

def fetch_rss(source: dict, client: httpx.Client, settings: dict) -> list[dict]:
    response = http_get(client, source["url"])
    feed = feedparser.parse(response.content)
    if not feed.entries:
        if feed.bozo:
            raise FetchError(f"O conteúdo não é um feed RSS/Atom válido ({feed.bozo_exception}).")
        raise FetchError("O feed respondeu, mas não tem nenhuma notícia.")
    items = []
    for entry in feed.entries[: settings.get("max_items_per_source", 40)]:
        title = clean_text(entry.get("title"), 300)
        link = normalize_url(entry.get("link") or "")
        if not title or not link:
            continue
        raw_summary = entry.get("summary") or ""
        if not raw_summary and entry.get("content"):
            raw_summary = entry["content"][0].get("value", "")
        summary = clean_text(raw_summary)
        struct = entry.get("published_parsed") or entry.get("updated_parsed")
        published = datetime.fromtimestamp(calendar.timegm(struct), timezone.utc) if struct else None
        items.append({
            "uid": uid_for(source["id"], entry.get("id") or link),
            "title": title,
            "url": link,
            "summary": summary,
            "published": published,
            "cves": find_cves(title, summary),
            "cvss": None,
            "kev": False,
        })
    return items


# --- CISA KEV -------------------------------------------------------------

def fetch_cisa_kev(source: dict, client: httpx.Client, settings: dict) -> list[dict]:
    response = http_get(client, source["url"])
    try:
        data = response.json()
        vulns = data["vulnerabilities"]
    except (ValueError, KeyError):
        raise FetchError("A resposta do CISA KEV não tem o formato esperado.")
    since = datetime.now(timezone.utc) - timedelta(days=settings.get("days_back", 3))
    items = []
    for v in vulns:
        added = parse_date(v.get("dateAdded"))
        if not added or added < since:
            continue
        cve = v.get("cveID", "")
        ransomware = str(v.get("knownRansomwareCampaignUse", "")).lower() == "known"
        parts = [v.get("shortDescription", "")]
        if v.get("requiredAction"):
            parts.append(f"Ação recomendada: {v['requiredAction']}")
        if v.get("dueDate"):
            parts.append(f"Prazo CISA: {v['dueDate']}.")
        if ransomware:
            parts.append("Usada em campanhas de ransomware.")
        items.append({
            "uid": uid_for(source["id"], cve),
            "title": f"{cve}: {v.get('vulnerabilityName') or (v.get('vendorProject', '') + ' ' + v.get('product', ''))}",
            "url": f"https://nvd.nist.gov/vuln/detail/{cve}",
            "summary": clean_text(" ".join(p for p in parts if p), 900),
            "published": added,
            "cves": [cve] if cve else [],
            "cvss": None,
            "kev": True,
            "extra_text": f"{v.get('vendorProject', '')} {v.get('product', '')}",
        })
    return items


# --- NVD ------------------------------------------------------------------

def _nvd_score(metrics: dict) -> float | None:
    for key in ("cvssMetricV40", "cvssMetricV31", "cvssMetricV30"):
        for metric in metrics.get(key) or []:
            score = (metric.get("cvssData") or {}).get("baseScore")
            if score is not None:
                return float(score)
    return None


def fetch_nvd(source: dict, client: httpx.Client, settings: dict) -> list[dict]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=settings.get("days_back", 3))
    params = {
        "pubStartDate": start.strftime("%Y-%m-%dT%H:%M:%S.000"),
        "pubEndDate": end.strftime("%Y-%m-%dT%H:%M:%S.000"),
        "cvssV3Severity": source.get("severity", "CRITICAL"),
        "resultsPerPage": 200,
    }
    headers = {"apiKey": settings["nvd_api_key"]} if settings.get("nvd_api_key") else {}
    response = http_get(client, source["url"], params=params, headers=headers)
    try:
        vulns = response.json().get("vulnerabilities", [])
    except ValueError:
        raise FetchError("A resposta do NVD não é um JSON válido.")
    items = []
    for wrapper in vulns[: settings.get("max_items_per_source", 40) * 2]:
        cve = wrapper.get("cve", {})
        cve_id = cve.get("id", "")
        if cve.get("vulnStatus") == "Rejected":
            continue
        desc = next((d["value"] for d in cve.get("descriptions", []) if d.get("lang") == "en"), "")
        score = _nvd_score(cve.get("metrics", {}))
        items.append({
            "uid": uid_for(source["id"], cve_id),
            "title": f"{cve_id} (CVSS {score:.1f})" if score is not None else cve_id,
            "url": f"https://nvd.nist.gov/vuln/detail/{cve_id}",
            "summary": clean_text(desc, 900),
            "published": parse_date(cve.get("published")),
            "cves": [cve_id],
            "cvss": score,
            "kev": bool(cve.get("cisaExploitAdd")),
        })
    return items


# --- GitHub Security Advisories -------------------------------------------

def fetch_github_advisories(source: dict, client: httpx.Client, settings: dict) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(days=settings.get("days_back", 3))).strftime("%Y-%m-%d")
    params = {
        "severity": source.get("severity", "critical"),
        "per_page": min(100, settings.get("max_items_per_source", 40)),
        "sort": "published",
        "direction": "desc",
        "published": f">={since}",
        "type": "reviewed",
    }
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if settings.get("github_token"):
        headers["Authorization"] = f"Bearer {settings['github_token']}"
    response = http_get(client, source["url"], params=params, headers=headers)
    try:
        advisories = response.json()
    except ValueError:
        raise FetchError("A resposta do GitHub não é um JSON válido.")
    if not isinstance(advisories, list):
        raise FetchError(f"Resposta inesperada do GitHub: {str(advisories)[:200]}")
    items = []
    for adv in advisories:
        packages = sorted({
            f"{(v.get('package') or {}).get('name')} ({(v.get('package') or {}).get('ecosystem')})"
            for v in adv.get("vulnerabilities") or [] if v.get("package")
        })
        score = (adv.get("cvss") or {}).get("score")
        summary = adv.get("summary") or ""
        pkg_text = ", ".join(packages)
        items.append({
            "uid": uid_for(source["id"], adv.get("ghsa_id", "")),
            "title": f"{pkg_text}: {summary}" if pkg_text else summary,
            "url": adv.get("html_url") or "",
            "summary": clean_text(adv.get("description"), 900),
            "published": parse_date(adv.get("published_at")),
            "cves": [adv["cve_id"]] if adv.get("cve_id") else [],
            "cvss": float(score) if score else None,
            "kev": False,
            "extra_text": pkg_text,
        })
    return items


# --- Raspagem de páginas sem RSS ------------------------------------------

def fetch_scrape(source: dict, client: httpx.Client, settings: dict) -> list[dict]:
    response = http_get(client, source["url"])
    soup = BeautifulSoup(response.text, "html.parser")
    try:
        blocks = soup.select(source["item_selector"])
    except Exception as exc:
        raise FetchError(f"Seletor de item inválido: {exc}")
    if not blocks:
        raise FetchError(
            f"Nenhum elemento encontrado com o seletor '{source['item_selector']}'. "
            "O layout do site pode ter mudado."
        )
    items = []
    for block in blocks[: settings.get("max_items_per_source", 40)]:
        title_el = block.select_one(source["title_selector"])
        if not title_el:
            continue
        link_sel = source.get("link_selector")
        link_el = block.select_one(link_sel) if link_sel else None
        link_el = link_el or (title_el if title_el.name == "a" else title_el.find("a")) or block.find("a")
        if not link_el or not link_el.get("href"):
            continue
        url = normalize_url(urljoin(source["url"], link_el["href"]))
        summary_el = block.select_one(source["summary_selector"]) if source.get("summary_selector") else None
        title = clean_text(title_el.get_text(" "), 300)
        summary = clean_text(summary_el.get_text(" ") if summary_el else "")
        time_el = block.find("time")
        items.append({
            "uid": uid_for(source["id"], url),
            "title": title,
            "url": url,
            "summary": summary,
            "published": parse_date(time_el.get("datetime")) if time_el and time_el.get("datetime") else None,
            "cves": find_cves(title, summary),
            "cvss": None,
            "kev": False,
        })
    if not items:
        raise FetchError("Os itens foram encontrados, mas nenhum tinha título e link com os seletores informados.")
    return items


FETCHERS = {
    "rss": fetch_rss,
    "cisa_kev": fetch_cisa_kev,
    "nvd": fetch_nvd,
    "github_advisories": fetch_github_advisories,
    "scrape": fetch_scrape,
}


def fetch_source(source: dict, client: httpx.Client, settings: dict) -> list[dict]:
    fetcher = FETCHERS.get(source.get("type"))
    if not fetcher:
        raise FetchError(f"Tipo de fonte desconhecido: {source.get('type')}")
    try:
        return fetcher(source, client, settings)
    except FetchError:
        raise
    except Exception as exc:  # erro inesperado também vai para o relatório
        raise FetchError(f"Erro inesperado ({type(exc).__name__}): {exc}")


# --- Descoberta de feed a partir do endereço do site ----------------------

def discover_feeds(url: str, client: httpx.Client) -> list[dict]:
    """Procura feeds RSS/Atom anunciados na página (para cadastrar sites novos)."""
    response = http_get(client, url)
    content_type = response.headers.get("content-type", "")
    if any(t in content_type for t in ("xml", "rss", "atom")):
        feed = feedparser.parse(response.content)
        if feed.entries:
            return [{"url": str(response.url), "title": feed.feed.get("title") or url}]
    soup = BeautifulSoup(response.text, "html.parser")
    found = []
    for link in soup.find_all("link", rel=lambda r: r and "alternate" in r):
        kind = (link.get("type") or "").lower()
        if ("rss" in kind or "atom" in kind) and link.get("href"):
            found.append({"url": urljoin(str(response.url), link["href"]), "title": link.get("title") or ""})
    if not found:
        for guess in ("feed", "rss", "feed.xml", "rss.xml", "atom.xml"):
            candidate = urljoin(str(response.url).rstrip("/") + "/", guess)
            try:
                r = client.get(candidate)
            except httpx.HTTPError:
                continue
            if r.status_code == 200 and feedparser.parse(r.content).entries:
                found.append({"url": candidate, "title": ""})
                break
    title = soup.title.get_text(strip=True) if soup.title else ""
    for f in found:
        f["title"] = f["title"] or title
    return found


# --- Leitura do artigo completo dentro do programa ------------------------

def fetch_article(url: str, client: httpx.Client) -> str:
    """Baixa a página e extrai só o texto principal (modo leitura)."""
    from readability import Document

    response = http_get(client, url)
    doc = Document(response.text)
    body = doc.summary(html_partial=True)
    soup = BeautifulSoup(body, "html.parser")
    for tag in soup(["script", "style", "iframe", "form", "noscript", "object", "embed"]):
        tag.decompose()
    base = str(response.url)
    for tag in soup.find_all(True):
        for attr in list(tag.attrs):
            if attr.lower().startswith("on"):
                del tag[attr]
        for attr in ("href", "src"):
            if tag.get(attr):
                tag[attr] = urljoin(base, tag[attr])
        if tag.name == "a":
            tag["target"] = "_blank"
            tag["rel"] = "noopener noreferrer"
    return str(soup)
