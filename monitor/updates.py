"""Atualização com conferência de integridade (SHA-256).

Consulta a versão mais recente publicada no GitHub, baixa o pacote do seu
sistema e confere o hash com o arquivo SHA256SUMS.txt publicado junto. Nada é
usado sem essa conferência.

Com um clique ("Atualizar agora") o programa também se troca sozinho:
  Windows instalado  roda o instalador novo em modo silencioso; ele fecha o
                     programa, troca os arquivos e abre de novo.
  Windows .zip       abre o assistente de instalação; a versão instalada
                     copia os dados da pasta do .zip na primeira vez.
  Linux              troca o executável e abre de novo.
Os dados ficam fora dos arquivos trocados, então nada se perde.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path

from . import __version__, config, fetchers
from .cveinfo import version_key

REPO = "Nerqs/monitor-noticias"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
SUMS = "SHA256SUMS.txt"
INSTALLER = "MonitorNoticias-instalador-windows.exe"


class UpdateError(Exception):
    pass


def asset_name() -> str:
    return "MonitorNoticias-windows.zip" if sys.platform == "win32" else "MonitorNoticias-linux.tar.gz"


def mode() -> str:
    """Como o programa está rodando: fonte, android, instalado, portatil (.zip) ou linux."""
    if config.is_android():
        return "android"
    if not getattr(sys, "frozen", False):
        return "fonte"
    if sys.platform == "win32":
        return "instalado" if config.is_installed() else "portatil"
    return "linux"


def auto_asset(current_mode: str | None = None) -> str | None:
    """Pacote usado na atualização com um clique (None = não dá para trocar sozinho)."""
    return {"instalado": INSTALLER, "portatil": INSTALLER,
            "linux": "MonitorNoticias-linux.tar.gz"}.get(current_mode or mode())


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


def _latest(cfg: dict) -> dict:
    with _client(cfg) as client:
        try:
            return fetchers.http_get(client, API, headers=_headers(cfg)).json()
        except fetchers.FetchError as exc:
            if "404" in str(exc):
                raise UpdateError("Nenhuma versão publicada ainda no GitHub.")
            raise UpdateError(f"Não foi possível consultar o GitHub: {exc}")


def check(cfg: dict | None = None) -> dict:
    cfg = cfg or config.load_config()
    release = _latest(cfg)
    latest = str(release.get("tag_name", "")).lstrip("v")
    assets = {a["name"]: a for a in release.get("assets") or []}
    current_mode = mode()
    auto = auto_asset(current_mode)
    return {
        "current": __version__,
        "latest": latest,
        "newer": bool(latest) and version_key(latest) > version_key(__version__),
        "notes": (release.get("body") or "")[:3000],
        "url": release.get("html_url"),
        "has_package": asset_name() in assets,
        "has_sums": SUMS in assets,
        "frozen": bool(getattr(sys, "frozen", False)),
        "mode": current_mode,
        "auto": bool(auto) and auto in assets and SUMS in assets,
        "skipped": latest == str(cfg.get("updates", {}).get("skip_version") or ""),
    }


def parse_sums(text: str) -> dict[str, str]:
    sums = {}
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and len(parts[0]) == 64:
            sums[parts[1].lstrip("*")] = parts[0].lower()
    return sums


def _fetch_verified(cfg: dict, name: str) -> tuple[bytes, str]:
    """Baixa um pacote da versão mais recente e confere o SHA-256. Devolve (bytes, tag)."""
    release = _latest(cfg)
    assets = {a["name"]: a for a in release.get("assets") or []}
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
    return package, str(release.get("tag_name", "nova"))


def _folder() -> Path:
    folder = config.data_dir() / "atualizacoes"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def download(cfg: dict | None = None) -> dict:
    cfg = cfg or config.load_config()
    name = asset_name()
    package, tag = _fetch_verified(cfg, name)
    path = _folder() / f"{tag}-{name}"
    path.write_bytes(package)
    return {"path": str(path), "folder": str(path.parent), "sha256": hashlib.sha256(package).hexdigest(),
            "version": tag.lstrip("v")}


# --- Atualizar com um clique ------------------------------------------------------

def _launch(command: list[str]) -> None:
    """Inicia o processo que termina a troca depois que este programa fechar."""
    if sys.platform == "win32":
        # ShellExecute: se o programa foi instalado para todos os usuários, o
        # instalador pede permissão de administrador (UAC) normalmente.
        os.startfile(command[0], "open", subprocess.list2cmdline(command[1:]), str(Path(command[0]).parent))
    else:
        subprocess.Popen(command, start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def exit_soon(delay: float = 1.5) -> None:
    """Fecha o programa logo depois de responder à janela, para a troca acontecer."""
    def stop():
        time.sleep(delay)
        from . import db

        logging.info("fechando para atualizar")
        db.close()
        logging.shutdown()
        os._exit(0)

    threading.Thread(target=stop, daemon=True).start()


def _replace_linux(package: bytes, exe: Path) -> None:
    try:
        with tarfile.open(fileobj=io.BytesIO(package), mode="r:gz") as tar:
            member = next((m for m in tar.getmembers() if m.isfile() and m.name.endswith("/MonitorNoticias")), None)
            if member is None:
                raise UpdateError("O pacote baixado não tem o executável MonitorNoticias.")
            data = tar.extractfile(member).read()
    except tarfile.TarError as exc:
        raise UpdateError(f"Pacote inválido: {exc}")
    novo = exe.with_name(f".{exe.name}.novo")
    try:
        novo.write_bytes(data)
        novo.chmod(0o755)
        os.replace(novo, exe)  # no Linux dá para trocar o arquivo com o programa aberto
    except OSError as exc:
        novo.unlink(missing_ok=True)
        raise UpdateError(f"Não foi possível trocar o executável em {exe.parent} ({exc}). "
                          "Use \"Baixar e conferir\" e troque o arquivo manualmente.")


def apply(cfg: dict | None = None) -> dict:
    """Baixa, confere e deixa a troca encaminhada. Quem chama deve fechar o programa depois."""
    cfg = cfg or config.load_config()
    current_mode = mode()
    name = auto_asset(current_mode)
    if not name:
        raise UpdateError("Atualização automática só funciona no executável. "
                          "Rodando pelo código-fonte, atualize com git pull.")
    package, tag = _fetch_verified(cfg, name)
    version = tag.lstrip("v")
    if current_mode == "linux":
        exe = Path(sys.executable).resolve()
        _replace_linux(package, exe)
        _launch(["sh", "-c", 'sleep 2; exec "$0" "$@"', str(exe), *sys.argv[1:]])
        message = f"Versão {version} instalada. O programa vai fechar e abrir de novo."
    else:
        folder = _folder()
        for old in folder.glob(f"*{INSTALLER}"):
            old.unlink(missing_ok=True)
        path = folder / f"{tag}-{INSTALLER}"
        path.write_bytes(package)
        if current_mode == "instalado":
            # Silencioso: mostra só a barra de progresso, fecha o programa se ainda
            # estiver aberto, troca os arquivos e abre a versão nova.
            _launch([str(path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"])
            message = f"Instalando a versão {version}. O programa vai fechar e abrir de novo sozinho."
        else:
            _launch([str(path)])
            message = (f"Abrindo o instalador da versão {version}. Siga o assistente: seus dados desta "
                       "pasta são copiados para a versão instalada na primeira vez que ela abrir.")
    logging.info("atualização para %s encaminhada (%s)", version, current_mode)
    return {"version": version, "mode": current_mode, "message": message}
