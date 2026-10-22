"""Verificação de atualização com conferência de integridade (SHA-256).

Consulta a versão mais recente publicada no GitHub, baixa o pacote do seu
sistema e confere o hash com o arquivo SHA256SUMS.txt publicado junto. Só
depois de conferido o pacote fica disponível na pasta data/atualizacoes.
"""

from __future__ import annotations

import hashlib
import sys

from . import __version__, config, fetchers
from .cveinfo import version_key

REPO = "Nerqs/monitor-noticias"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
SUMS = "SHA256SUMS.txt"


class UpdateError(Exception):
    pass


def asset_name() -> str:
    return "MonitorNoticias-windows.zip" if sys.platform == "win32" else "MonitorNoticias-linux.tar.gz"


def _client(cfg: dict, max_mb: float = 2):
    from .pipeline import client_for

    settings = dict(cfg.get("collection", {}))
    settings["max_download_mb"] = max_mb
    return client_for(settings, retries=False)


def _headers(cfg: dict) -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    token = cfg.get("collection", {}).get("github_token")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def check(cfg: dict | None = None) -> dict:
    cfg = cfg or config.load_config()
    with _client(cfg) as client:
        try:
            release = fetchers.http_get(client, API, headers=_headers(cfg)).json()
        except fetchers.FetchError as exc:
            if "404" in str(exc):
                raise UpdateError("Nenhuma versão publicada ainda no GitHub.")
            raise UpdateError(f"Não foi possível consultar o GitHub: {exc}")
    latest = str(release.get("tag_name", "")).lstrip("v")
    assets = {a["name"]: a for a in release.get("assets") or []}
    return {
        "current": __version__,
        "latest": latest,
        "newer": bool(latest) and version_key(latest) > version_key(__version__),
        "notes": (release.get("body") or "")[:3000],
        "url": release.get("html_url"),
        "has_package": asset_name() in assets,
        "has_sums": SUMS in assets,
        "frozen": bool(getattr(sys, "frozen", False)),
    }


def parse_sums(text: str) -> dict[str, str]:
    sums = {}
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and len(parts[0]) == 64:
            sums[parts[1].lstrip("*")] = parts[0].lower()
    return sums


def download(cfg: dict | None = None) -> dict:
    cfg = cfg or config.load_config()
    with _client(cfg) as client:
        try:
            release = fetchers.http_get(client, API, headers=_headers(cfg)).json()
        except fetchers.FetchError as exc:
            raise UpdateError(f"Não foi possível consultar o GitHub: {exc}")
    assets = {a["name"]: a for a in release.get("assets") or []}
    name = asset_name()
    if name not in assets:
        raise UpdateError(f"A versão publicada não tem o pacote {name}.")
    if SUMS not in assets:
        raise UpdateError("A versão publicada não tem o arquivo SHA256SUMS.txt; por segurança o download foi cancelado.")
    headers = {**_headers(cfg), "Accept": "application/octet-stream"}
    with _client(cfg, max_mb=400) as client:
        try:
            sums = parse_sums(fetchers.http_get(client, assets[SUMS]["url"], headers=headers).text)
            package = fetchers.http_get(client, assets[name]["url"], headers=headers).content
        except fetchers.FetchError as exc:
            raise UpdateError(f"Falha no download: {exc}")
    expected = sums.get(name)
    actual = hashlib.sha256(package).hexdigest()
    if not expected:
        raise UpdateError(f"O SHA256SUMS.txt não lista {name}; download descartado.")
    if actual != expected:
        raise UpdateError("O hash do arquivo baixado não confere com o publicado. O arquivo foi descartado: "
                          "pode ter sido corrompido ou alterado no caminho.")
    folder = config.data_dir() / "atualizacoes"
    folder.mkdir(parents=True, exist_ok=True)
    tag = str(release.get("tag_name", "nova"))
    path = folder / f"{tag}-{name}"
    path.write_bytes(package)
    return {"path": str(path), "folder": str(folder), "sha256": actual, "version": tag.lstrip("v")}
