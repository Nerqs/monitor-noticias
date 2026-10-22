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
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import feedparser
import httpx
from bs4 import BeautifulSoup

from . import netguard

USER_AGENT = "Mozilla/5.0 (compatible; MonitorNoticias/1.0; +https://github.com/Nerqs/monitor-noticias)"
CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE)


class FetchError(Exception):
    pass


# --- Utilidades -----------------------------------------------------------

MAX_DOWNLOAD_MB = 10
RETRY_DELAYS = (2, 5)  # segundos de espera antes da 2ª e da 3ª tentativa
RETRY_STATUS = {429, 500, 502, 503, 504}


class NotModified(Exception):
    """O site respondeu 304: nada mudou desde a última coleta."""


def _guard_request(request: httpx.Request) -> None:
    try:
        netguard.check_url(str(request.url))
    except netguard.BlockedAddress as exc:
        raise FetchError(str(exc))


def make_client(timeout: float = 20, allow_private: bool = False,
                max_mb: float = MAX_DOWNLOAD_MB, retries: bool = True,
                transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """Cliente HTTP da coleta.

    Cada requisição (inclusive cada redirecionamento) passa pelo ``netguard``,
    a não ser que ``allow_private`` libere a rede interna.
    """
    client = httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        max_redirects=8,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"},
        event_hooks={"request": [] if allow_private else [_guard_request]},
        transport=transport,
    )
    client.max_bytes = int(max_mb * 1024 * 1024)
    client.retries = retries
    return client


def _download(client: httpx.Client, url: str, **kwargs) -> httpx.Response:
    """GET com limite de tamanho: corta respostas gigantes antes de lê-las inteiras."""
    limit = getattr(client, "max_bytes", MAX_DOWNLOAD_MB * 1024 * 1024)
    with client.stream("GET", url, **kwargs) as response:
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limit:
            raise FetchError(f"Resposta grande demais ({int(declared) // 1048576} MB; limite {limit // 1048576} MB).")
        chunks, size = [], 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > limit:
                raise FetchError(f"Resposta grande demais (mais de {limit // 1048576} MB). Download interrompido.")
            chunks.append(chunk)
        # Já descompactado por iter_bytes; guardado como conteúdo da resposta.
        response._content = b"".join(chunks)
    return response


def http_get(client: httpx.Client, url: str, cache=None, **kwargs) -> httpx.Response:
    """GET com limite de tamanho, novas tentativas e cache condicional opcional.

    ``cache`` (``db.HttpCache``) guarda ETag/Last-Modified; quando o site responde
    304, levanta ``NotModified``.
    """
    headers = dict(kwargs.pop("headers", None) or {})
    if cache is not None:
        headers.update(cache.conditional_headers(url))
    attempts = 1 + (len(RETRY_DELAYS) if getattr(client, "retries", True) else 0)
    for attempt in range(attempts):
        retry_after = None
        try:
            response = _download(client, url, headers=headers, **kwargs)
        except httpx.TimeoutException:
            error = FetchError("Tempo esgotado: o site demorou demais para responder.")
        except httpx.ConnectError as exc:
            error = FetchError(f"Não foi possível conectar ao site ({exc}).")
        except httpx.TooManyRedirects:
            raise FetchError("Redirecionamentos demais: o endereço fica mandando para outro lugar.")
        except httpx.HTTPError as exc:
            error = FetchError(f"Erro de rede: {exc}")
        else:
            if response.status_code not in RETRY_STATUS:
                break
            error = None
            header = response.headers.get("retry-after", "")
            retry_after = min(int(header), 30) if header.isdigit() else None
        if attempt == attempts - 1:
            if error:
                raise error
            break
        time.sleep(retry_after if retry_after is not None else RETRY_DELAYS[attempt])

    if response.status_code == 304:
        raise NotModified()
    if response.status_code >= 400:
        hints = {
            403: "acesso negado; o site pode estar bloqueando robôs",
            404: "endereço não encontrado; o feed pode ter mudado de lugar",
            429: "muitas requisições; tente mais tarde ou use uma chave de API",
        }
        hint = hints.get(response.status_code) or ("erro no servidor do site" if response.status_code >= 500 else "")
        raise FetchError(f"HTTP {response.status_code}" + (f": {hint}." if hint else "."))
    if cache is not None:
        cache.store(url, response.headers.get("etag"), response.headers.get("last-modified"))
    return response


def check_xml_safety(content: bytes) -> None:
    """Recusa XML com declarações de entidade (base de ataques como "billion laughs")."""
    head = content[:65536].lower()
    if b"<!entity" in head:
        raise FetchError("O feed declara entidades XML (<!ENTITY>), recurso usado em ataques. Feed ignorado.")


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
    response = http_get(client, source["url"], cache=settings.get("http_cache"))
    check_xml_safety(response.content)
    feed = feedparser.parse(response.content)
    if not feed.entries:
        if "html" in response.headers.get("content-type", "") and b"<rss" not in response.content[:2000]:
            raise FetchError("O endereço devolveu uma página HTML, não um feed. O feed pode ter mudado de lugar.")
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
    response = http_get(client, source["url"], cache=settings.get("http_cache"))
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
            "nvd_raw": cve,
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
    response = http_get(client, source["url"], cache=settings.get("http_cache"))
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
    except (FetchError, NotModified):
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
                r = _download(client, candidate)
            except (httpx.HTTPError, FetchError):
                continue
            if r.status_code == 200 and feedparser.parse(r.content).entries:
                found.append({"url": candidate, "title": ""})
                break
    title = soup.title.get_text(strip=True) if soup.title else ""
    for f in found:
        f["title"] = f["title"] or title
    return found


# --- Leitura do artigo completo dentro do programa ------------------------

def fetch_article(url: str, client: httpx.Client, block_images: bool = False) -> str:
    """Baixa a página e extrai só o texto principal (modo leitura), já limpo."""
    from .sanitize import clean_html

    response = http_get(client, url)
    try:
        from readability import Document
    except ImportError:  # app Android: sem lxml, usa a extração simples
        body = simple_main_content(response.text)
    else:
        body = Document(response.text).summary(html_partial=True)
    return clean_html(body, str(response.url), block_images=block_images)


def simple_main_content(page: str) -> str:
    """Extração do texto principal sem lxml: pega o <article> ou o bloco com mais parágrafos."""
    soup = BeautifulSoup(page, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]):
        tag.decompose()
    candidates = soup.find_all("article") or soup.find_all(["main", "div", "section"])
    best, best_len = None, 0
    for node in candidates:
        length = sum(len(p.get_text(" ", strip=True)) for p in node.find_all("p", recursive=True))
        if length > best_len:
            best, best_len = node, length
    return str(best or soup.body or soup)
