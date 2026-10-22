"""Enriquecimento das notícias com dados de CVE.

- **EPSS** (FIRST.org): probabilidade de a falha ser explorada nos próximos 30 dias.
- **NVD**: descrição oficial, CVSS, referências (correção, exploit) e as faixas
  de versão afetadas, usadas para dizer se a *sua* versão é vulnerável.

Tudo fica em cache no banco (tabela ``cve_info``) para não repetir consultas.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone

import httpx

from . import db, fetchers, scoring

log = logging.getLogger(__name__)

EPSS_URL = "https://api.first.org/data/v1/epss"
NVD_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
CVE_ID = re.compile(r"^CVE-\d{4}-\d{4,7}$")

# Produtos comuns e o nome deles no CPE do NVD ("fornecedor:produto").
KNOWN_PRODUCTS = {
    "PHP": "php:php",
    "MariaDB": "mariadb:mariadb",
    "MySQL": "oracle:mysql",
    "Laravel": "laravel:framework",
    "Node.js": "nodejs:node.js",
    "nginx": "f5:nginx",
    "Apache HTTP Server": "apache:http_server",
    "OpenSSL": "openssl:openssl",
    "PostgreSQL": "postgresql:postgresql",
    "WordPress": "wordpress:wordpress",
    "Redis": "redis:redis",
    "Composer": "getcomposer:composer",
}


def _older_than(value: str | None, hours: float) -> bool:
    if not value:
        return True
    moment = datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - moment > timedelta(hours=hours)


# --- EPSS -------------------------------------------------------------------

def fetch_epss(client: httpx.Client, cves: list[str]) -> dict[str, tuple[float, float]]:
    """Consulta o EPSS em lotes de 100. Devolve {cve: (epss, percentil)}."""
    result: dict[str, tuple[float, float]] = {}
    cves = [c for c in dict.fromkeys(cves) if CVE_ID.match(c)]
    for i in range(0, len(cves), 100):
        batch = cves[i:i + 100]
        response = fetchers.http_get(client, EPSS_URL, params={"cve": ",".join(batch)})
        try:
            rows = response.json().get("data", [])
        except ValueError:
            raise fetchers.FetchError("A resposta do EPSS não é um JSON válido.")
        for row in rows:
            try:
                result[row["cve"].upper()] = (float(row["epss"]), float(row["percentile"]))
            except (KeyError, TypeError, ValueError):
                continue
    return result


# --- NVD --------------------------------------------------------------------

class NvdBudget:
    """Respeita o limite do NVD (5 consultas a cada 30 s sem chave, 50 com chave)."""

    def __init__(self, api_key: str = "", max_lookups: int = 15):
        self.api_key = api_key
        self.left = max_lookups
        self.interval = 0.7 if api_key else 6.5
        self._last = 0.0

    def take(self) -> bool:
        if self.left <= 0:
            return False
        wait = self._last + self.interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        self.left -= 1
        return True


def nvd_record(client: httpx.Client | None, cve: str, budget: NvdBudget | None = None,
               max_age_hours: float = 24) -> dict | None:
    """Devolve o registro do NVD para o CVE, do cache ou consultando a API."""
    cached = db.get_cve_info(cve)
    if cached and cached.get("nvd") and not _older_than(cached.get("nvd_at"), max_age_hours):
        return json.loads(cached["nvd"])
    if client is None or (budget is not None and not budget.take()):
        return json.loads(cached["nvd"]) if cached and cached.get("nvd") else None
    headers = {"apiKey": budget.api_key} if budget and budget.api_key else {}
    response = fetchers.http_get(client, NVD_URL, params={"cveId": cve}, headers=headers)
    try:
        vulns = response.json().get("vulnerabilities", [])
    except ValueError:
        raise fetchers.FetchError("A resposta do NVD não é um JSON válido.")
    if not vulns:
        return None
    record = vulns[0].get("cve", {})
    db.save_cve_nvd(cve, json.dumps(record))
    return record


def cpe_ranges(record: dict) -> list[dict]:
    """Extrai as faixas de versão vulneráveis das configurações do NVD."""
    ranges = []
    for conf in record.get("configurations") or []:
        for node in conf.get("nodes") or []:
            for match in node.get("cpeMatch") or []:
                if not match.get("vulnerable"):
                    continue
                parts = (match.get("criteria") or "").split(":")
                if len(parts) < 6:
                    continue
                ranges.append({
                    "product": f"{parts[3]}:{parts[4]}",
                    "version": parts[5],
                    "start_incl": match.get("versionStartIncluding"),
                    "start_excl": match.get("versionStartExcluding"),
                    "end_incl": match.get("versionEndIncluding"),
                    "end_excl": match.get("versionEndExcluding"),
                })
    return ranges


def summarize(record: dict) -> dict:
    """Resumo legível de um registro do NVD (para a página do CVE)."""
    metrics = record.get("metrics") or {}
    cvss, vector, severity = None, "", ""
    for key in ("cvssMetricV40", "cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        for metric in metrics.get(key) or []:
            data = metric.get("cvssData") or {}
            if data.get("baseScore") is not None:
                cvss, vector = float(data["baseScore"]), data.get("vectorString", "")
                severity = data.get("baseSeverity") or metric.get("baseSeverity") or ""
                break
        if cvss is not None:
            break
    refs = []
    for ref in record.get("references") or []:
        refs.append({"url": ref.get("url", ""), "tags": ref.get("tags") or []})
    weaknesses = sorted({
        d.get("value", "") for w in record.get("weaknesses") or [] for d in w.get("description") or []
        if d.get("value", "").startswith("CWE-")
    })
    return {
        "id": record.get("id"),
        "description": next((d["value"] for d in record.get("descriptions", []) if d.get("lang") == "en"), ""),
        "published": record.get("published"),
        "modified": record.get("lastModified"),
        "status": record.get("vulnStatus"),
        "cvss": cvss,
        "vector": vector,
        "severity": severity,
        "kev_added": record.get("cisaExploitAdd"),
        "kev_due": record.get("cisaActionDue"),
        "kev_action": record.get("cisaRequiredAction"),
        "kev_name": record.get("cisaVulnerabilityName"),
        "weaknesses": weaknesses,
        "references": refs,
        "has_patch": any("Patch" in r["tags"] for r in refs),
        "has_exploit": any("Exploit" in r["tags"] for r in refs),
        "ranges": cpe_ranges(record),
    }


# --- Comparação de versões --------------------------------------------------

def version_key(value: str) -> list:
    parts = []
    for piece in re.split(r"[.\-+_]", str(value).strip().lower().lstrip("v")):
        if piece.isdigit():
            parts.append((0, int(piece)))
        elif piece:
            parts.append((-1, piece))  # "rc1", "beta" vêm antes da versão final
    while parts and parts[-1] == (0, 0):
        parts.pop()
    return parts


def _cmp(a: str, b: str) -> int:
    ka, kb = version_key(a), version_key(b)
    return (ka > kb) - (ka < kb)


def in_range(version: str, rng: dict) -> bool:
    exact = rng.get("version")
    bounded = any(rng.get(k) for k in ("start_incl", "start_excl", "end_incl", "end_excl"))
    if exact not in ("*", "-", "", None) and not bounded:
        # Versão exata no CPE: compara só até a precisão informada ("8.2" casa com "8.2.x").
        mine, theirs = version_key(version), version_key(exact)
        return mine[:len(theirs)] == theirs
    if not bounded:
        return True  # CPE sem versão: todas as versões
    if rng.get("start_incl") and _cmp(version, rng["start_incl"]) < 0:
        return False
    if rng.get("start_excl") and _cmp(version, rng["start_excl"]) <= 0:
        return False
    if rng.get("end_incl") and _cmp(version, rng["end_incl"]) > 0:
        return False
    if rng.get("end_excl") and _cmp(version, rng["end_excl"]) >= 0:
        return False
    return True


def check_versions(ranges: list[dict], versions: list[dict]) -> tuple[str, str]:
    """Compara suas versões com as faixas do CVE.

    Devolve ("afeta" | "nao_afeta" | "", nota). Vazio quando o CVE não fala de
    nenhum produto que você cadastrou.
    """
    affected, safe = [], []
    for mine in versions:
        product = str(mine.get("cpe") or "").lower().strip()
        version = str(mine.get("version") or "").strip()
        if not product or not version:
            continue
        relevant = [r for r in ranges if r["product"].lower() == product]
        if not relevant:
            continue
        label = f"{mine.get('name') or product} {version}"
        (affected if any(in_range(version, r) for r in relevant) else safe).append(label)
    if affected:
        return "afeta", ", ".join(affected)
    if safe:
        return "nao_afeta", ", ".join(safe)
    return "", ""


def version_names(versions: list[dict]) -> list[str]:
    """Nomes para procurar no texto da notícia (decidem se vale consultar o NVD)."""
    names = []
    for v in versions:
        if v.get("name"):
            names.append(str(v["name"]))
        if v.get("cpe") and ":" in str(v["cpe"]):
            names.append(str(v["cpe"]).split(":", 1)[1].replace("_", " "))
    return names


# --- Etapa de enriquecimento da coleta ----------------------------------------

def enrich_pending(cfg: dict, client: httpx.Client) -> dict:
    """Completa EPSS e checagem de versão das notícias novas e recalcula a prioridade."""
    settings = cfg.get("collection", {})
    enrich_cfg = cfg.get("enrichment", {})
    versions = cfg.get("interests", {}).get("versions") or []
    names = version_names(versions)
    techs = cfg.get("interests", {}).get("technologies") or []
    items = db.items_to_enrich()
    stats = {"items": len(items), "epss": 0, "nvd": 0, "errors": []}
    if not items:
        return stats

    epss_values: dict[str, tuple[float, float]] = {}
    all_cves = sorted({c for i in items for c in (i["cves"] or "").split(",") if c})
    if all_cves and enrich_cfg.get("epss", True):
        stale = []
        for cve in all_cves:
            cached = db.get_cve_info(cve)
            if cached and cached.get("epss") is not None and not _older_than(cached.get("epss_at"), 24):
                epss_values[cve] = (cached["epss"], cached["epss_pct"])
            else:
                stale.append(cve)
        if stale:
            try:
                fresh = fetch_epss(client, stale)
                db.save_cve_epss(fresh)
                epss_values.update(fresh)
                stats["epss"] = len(fresh)
            except fetchers.FetchError as exc:
                stats["errors"].append(f"EPSS: {exc}")

    budget = NvdBudget(settings.get("nvd_api_key", ""), int(enrich_cfg.get("max_nvd_lookups", 15)))
    for item in items:
        cves = [c for c in (item["cves"] or "").split(",") if c]
        values = [epss_values[c] for c in cves if c in epss_values]
        if values:
            best = max(values)
            item["epss"], item["epss_pct"] = best
        complete = True
        text = f"{item['title']} {item['summary']}"
        if cves and versions and (scoring.matches(names, text) or scoring.matches(techs, text)):
            verdicts = []
            for cve in cves[:3]:
                try:
                    record = nvd_record(client, cve, budget)
                except fetchers.FetchError as exc:
                    stats["errors"].append(f"NVD {cve}: {exc}")
                    record = None
                if record is None:
                    complete = complete and db.get_cve_info(cve) is not None
                    continue
                stats["nvd"] += 1
                summary = summarize(record)
                if item.get("cvss") is None and summary["cvss"] is not None:
                    item["cvss"] = summary["cvss"]
                verdicts.append(check_versions(summary["ranges"], versions))
            verdict = next((v for v in verdicts if v[0] == "afeta"), None) or \
                next((v for v in verdicts if v[0]), ("", ""))
            item["version_verdict"], item["version_note"] = verdict
        final = scoring.finalize(item, cfg)
        recent = not _older_than(item["fetched_at"], 72)
        db.update_item(item["id"], epss=item.get("epss"), epss_pct=item.get("epss_pct"), cvss=item.get("cvss"),
                       version_verdict=item.get("version_verdict") or "", version_note=item.get("version_note") or "",
                       enriched=0 if (not complete and recent) else 1, **final)
    return stats


def rescore_all(cfg: dict) -> int:
    """Refaz a prioridade final de todas as notícias (após mudar filtros ou versões).

    Usa só o que já está em cache; não consulta a internet.
    """
    versions = cfg.get("interests", {}).get("versions") or []
    changed = 0
    for item in db.all_items_for_rescore():
        verdict = ("", "")
        if versions:
            verdicts = []
            for cve in [c for c in (item.get("cves") or "").split(",") if c]:
                cached = db.get_cve_info(cve)
                if cached and cached.get("nvd"):
                    verdicts.append(check_versions(cpe_ranges(json.loads(cached["nvd"])), versions))
            verdict = next((v for v in verdicts if v[0] == "afeta"), None) or \
                next((v for v in verdicts if v[0]), ("", ""))
        item["version_verdict"], item["version_note"] = verdict
        final = scoring.finalize(item, cfg)
        updates = {**final, "version_verdict": verdict[0], "version_note": verdict[1]}
        if any(updates[k] != item.get(k) for k in updates):
            db.update_item(item["id"], **updates)
            changed += 1
    return changed
