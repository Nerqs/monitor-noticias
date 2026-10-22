"""O monitor vigia a si mesmo: confere as bibliotecas do programa na base OSV.

Consulta https://osv.dev com o nome e a versão de cada pacote Python instalado
e guarda o resultado. Se alguma biblioteca tiver falha conhecida, o leitor
mostra um aviso com a versão que corrige.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from importlib import metadata

import httpx

from . import config, db, fetchers

log = logging.getLogger(__name__)

OSV_BATCH = "https://api.osv.dev/v1/querybatch"
OSV_VULN = "https://api.osv.dev/v1/vulns/"
BUILD_DEPS = config.PACKAGE_DIR / "_build_deps.json"  # gerado pelo empacotar.py


def installed_packages() -> dict[str, str]:
    packages = {}
    for dist in metadata.distributions():
        name = (dist.metadata.get("Name") or "").strip()
        if name and dist.version:
            packages[name.lower()] = dist.version
    if BUILD_DEPS.exists():
        # No executável os metadados nem sempre vêm junto: usa a lista gravada no empacotamento.
        try:
            for name, version in json.loads(BUILD_DEPS.read_text(encoding="utf-8")).items():
                packages.setdefault(name.lower(), version)
        except (OSError, ValueError):
            pass
    packages.pop("monitor-noticias", None)
    return packages


def _fixed_versions(vuln: dict, package: str) -> list[str]:
    fixed = []
    for affected in vuln.get("affected") or []:
        if (affected.get("package") or {}).get("name", "").lower() != package:
            continue
        for rng in affected.get("ranges") or []:
            for event in rng.get("events") or []:
                if event.get("fixed"):
                    fixed.append(event["fixed"])
    return sorted(set(fixed))


def run(cfg: dict | None = None) -> dict:
    cfg = cfg or config.load_config()
    from .pipeline import client_for

    packages = installed_packages()
    names = sorted(packages)
    queries = [{"package": {"name": n, "ecosystem": "PyPI"}, "version": packages[n]} for n in names]
    findings = []
    with client_for(cfg.get("collection", {})) as client:
        results = []
        for i in range(0, len(queries), 500):
            try:
                response = client.post(OSV_BATCH, json={"queries": queries[i:i + 500]}, timeout=30)
            except httpx.HTTPError as exc:
                raise fetchers.FetchError(f"Não foi possível consultar a base OSV: {exc}")
            if response.status_code != 200:
                raise fetchers.FetchError(f"OSV respondeu HTTP {response.status_code}.")
            results += response.json().get("results", [])
        for name, result in zip(names, results):
            for vuln in (result or {}).get("vulns") or []:
                detail = {}
                if len(findings) < 30:
                    try:
                        detail = fetchers.http_get(client, OSV_VULN + vuln["id"]).json()
                    except (fetchers.FetchError, ValueError):
                        detail = {}
                findings.append({
                    "package": name,
                    "version": packages[name],
                    "id": vuln["id"],
                    "aliases": detail.get("aliases") or [],
                    "summary": detail.get("summary") or "",
                    "fixed": _fixed_versions(detail, name),
                    "url": f"https://osv.dev/vulnerability/{vuln['id']}",
                })
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "packages": len(packages),
        "findings": findings,
    }
    db.set_meta("selfcheck", json.dumps(report))
    if findings:
        log.warning("autoauditoria: %d falhas conhecidas nas bibliotecas do programa", len(findings))
    return report


def last_report() -> dict | None:
    raw = db.get_meta("selfcheck")
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def run_if_due(cfg: dict) -> dict | None:
    if not cfg.get("security", {}).get("self_audit", True):
        return None
    report = last_report()
    if report:
        last = datetime.fromisoformat(report["checked_at"])
        if datetime.now(timezone.utc) - last < timedelta(days=1):
            return None
    return run(cfg)
