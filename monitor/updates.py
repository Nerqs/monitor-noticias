"""Atualização com conferência de integridade (SHA-256).

Consulta a versão mais recente publicada no GitHub, baixa o pacote do seu
sistema e confere o hash com o arquivo SHA256SUMS.txt publicado junto. Nada é
usado sem essa conferência.

Com um clique ("Atualizar agora") o programa também se troca sozinho:
  Windows instalado  roda o instalador novo em modo silencioso; ele fecha o
                     programa, troca os arquivos e abre de novo.
  Windows .zip       abre o assistente de instalação; a versão instalada
                     copia os dados da pasta do .zip na primeira vez.
  Linux (.tar.gz)     troca o executável e abre de novo.
  AppImage           troca o arquivo .AppImage e abre de novo.
  .deb / Arch        instala o pacote novo com pkexec (pede a senha) e abre de novo.
  Flatpak            só baixa e confere: o sandbox não deixa o programa se trocar.
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
TARBALL = "MonitorNoticias-linux.tar.gz"

# Pacote de cada forma de instalação no Linux (mesmos nomes que o CI publica).
LINUX_PACKAGES = {
    "appimage": "MonitorNoticias-linux-x86_64.AppImage",
    "deb": "MonitorNoticias-linux-amd64.deb",
    "arch": "MonitorNoticias-linux-x86_64.pkg.tar.zst",
    "flatpak": "MonitorNoticias-linux.flatpak",
}
# Comando que o pacote .deb/Arch instala no PATH, usado para abrir de novo após atualizar.
LINUX_COMMAND = "/usr/bin/monitor-noticias"


class UpdateError(Exception):
    pass


def asset_name(current_mode: str | None = None) -> str:
    """Pacote que "Baixar e conferir" baixa para o jeito como o programa está instalado."""
    if sys.platform == "win32":
        return "MonitorNoticias-windows.zip"
    return LINUX_PACKAGES.get(current_mode or mode(), TARBALL)


def mode() -> str:
    """Como o programa está rodando: fonte, android, instalado, portatil (.zip), linux (.tar.gz),
    appimage, deb, arch ou flatpak."""
    if config.is_android():
        return "android"
    if not getattr(sys, "frozen", False):
        return "fonte"
    if sys.platform == "win32":
        return "instalado" if config.is_installed() else "portatil"
    found = config.install_format()
    return found if found in LINUX_PACKAGES else "linux"


def auto_asset(current_mode: str | None = None) -> str | None:
    """Pacote usado na atualização com um clique (None = não dá para trocar sozinho)."""
    current_mode = current_mode or mode()
    if current_mode == "flatpak":
        return None  # dentro do sandbox não dá para reinstalar o próprio Flatpak
    return {"instalado": INSTALLER, "portatil": INSTALLER, "linux": TARBALL,
            **LINUX_PACKAGES}.get(current_mode)


def hint(current_mode: str) -> str:
    """O que o botão de atualizar faz (ou como atualizar quando ele não aparece)."""
    return {
        "portatil": "Abre o instalador: o programa passa a ficar instalado, com atalho na área de trabalho, "
                    "e seus dados vão junto.",
        "deb": "Baixa, confere o SHA-256 e instala o .deb novo (o sistema pede sua senha). "
               "Depois o programa abre de novo. Seus dados não mudam.",
        "arch": "Baixa, confere o SHA-256 e instala o pacote novo com o pacman (o sistema pede sua senha). "
                "Depois o programa abre de novo. Seus dados não mudam.",
        "flatpak": "No Flatpak use \"Baixar e conferir\" e abra o arquivo baixado (ou rode "
                   "flatpak install --user no arquivo). Seus dados não mudam.",
    }.get(current_mode, "Baixa, confere o SHA-256, fecha, troca os arquivos e abre de novo. Seus dados não mudam.")


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
        "has_package": asset_name(current_mode) in assets,
        "package": asset_name(current_mode),
        "hint": hint(current_mode),
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
            "version": tag.lstrip("v"), "how": _manual_steps(mode(), path)}


def _manual_steps(current_mode: str, path: Path) -> str:
    """Como instalar à mão o pacote baixado por "Baixar e conferir"."""
    return {
        "flatpak": f"Feche o programa e rode: flatpak install --user --bundle \"{path}\"",
        "deb": f"Feche o programa e rode: sudo dpkg -i \"{path}\"",
        "arch": f"Feche o programa e rode: sudo pacman -U \"{path}\"",
        "appimage": "Feche o programa e troque o seu arquivo .AppImage por este (deixe-o executável).",
    }.get(current_mode, f"Feche o programa e extraia {path} por cima da pasta atual.")


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
    _replace_file(data, exe)  # no Linux dá para trocar o arquivo com o programa aberto


def _replace_file(data: bytes, target: Path) -> None:
    """Troca um arquivo executável de uma vez (cópia ao lado + rename atômico)."""
    novo = target.with_name(f".{target.name}.novo")
    try:
        novo.write_bytes(data)
        novo.chmod(0o755)
        os.replace(novo, target)
    except OSError as exc:
        novo.unlink(missing_ok=True)
        raise UpdateError(f"Não foi possível trocar {target.name} em {target.parent} ({exc}). "
                          "Use \"Baixar e conferir\" e troque o arquivo manualmente.")


def _install_with_pkexec(current_mode: str, package: bytes, tag: str) -> None:
    """.deb/Arch: grava o pacote e pede ao sistema para instalá-lo como administrador."""
    import shutil

    if not shutil.which("pkexec"):
        raise UpdateError("Não achei o pkexec para pedir a senha de administrador. Use \"Baixar e conferir\" "
                          "e instale o pacote pela central de programas ou pelo terminal.")
    name = LINUX_PACKAGES[current_mode]
    folder = _folder()
    for old in folder.glob(f"*{name}"):
        old.unlink(missing_ok=True)
    path = folder / f"{tag}-{name}"
    path.write_bytes(package)
    path.chmod(0o644)  # o gerenciador de pacotes roda como root, mas há distros que leem como outro usuário
    install = (["dpkg", "-i"] if current_mode == "deb" else ["pacman", "-U", "--noconfirm"])
    # Abre de novo mesmo se a pessoa cancelar a senha (aí continua na versão atual).
    _launch(["sh", "-c", 'sleep 2; pkexec "$@"; exec ' + LINUX_COMMAND, "atualizar", *install, str(path)])


def apply(cfg: dict | None = None) -> dict:
    """Baixa, confere e deixa a troca encaminhada. Quem chama deve fechar o programa depois."""
    cfg = cfg or config.load_config()
    current_mode = mode()
    name = auto_asset(current_mode)
    if current_mode == "flatpak":
        raise UpdateError(hint(current_mode))
    if not name:
        raise UpdateError("Atualização automática só funciona no executável. "
                          "Rodando pelo código-fonte, atualize com git pull.")
    package, tag = _fetch_verified(cfg, name)
    version = tag.lstrip("v")
    if current_mode == "appimage":
        image = Path(os.environ.get("APPIMAGE") or "")
        if not image.is_file():
            raise UpdateError("Não achei o arquivo .AppImage em uso (variável APPIMAGE). "
                              "Use \"Baixar e conferir\" e troque o arquivo manualmente.")
        _replace_file(package, image)  # o AppImage aberto continua montado até fechar
        _launch(["sh", "-c", 'sleep 2; exec "$0" "$@"', str(image), *sys.argv[1:]])
        message = f"Versão {version} instalada. O programa vai fechar e abrir de novo."
    elif current_mode in ("deb", "arch"):
        _install_with_pkexec(current_mode, package, tag)
        message = (f"Instalando a versão {version}: digite sua senha na janela do sistema. "
                   "O programa vai fechar e abrir de novo.")
    elif current_mode == "linux":
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
