"""Gera os pacotes Linux a partir do executável do PyInstaller (rode depois de empacotar.py).

Uso: python linux/pacotes.py [appimage] [deb] [pkgbuild] [flatpak]   (sem nada = todos)

Resultado em dist/:
  MonitorNoticias-linux-x86_64.AppImage   arquivo único; cria o próprio atalho no menu ao abrir
  MonitorNoticias-linux-amd64.deb         Debian, Ubuntu, Mint (instala em /opt/monitor-noticias)
  PKGBUILD                                Arch, Manjaro: makepkg -si (baixa o .tar.gz da versão)
  flatpak/                                manifesto + arquivos para o flatpak-builder (o CI gera
                                          MonitorNoticias-linux.flatpak)

Cada pacote grava um instalacao.ini com "formato=..." ao lado do executável: é assim que o
programa sabe onde guardar os dados e como se atualizar (monitor/updates.py).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
BIN = DIST / "MonitorNoticias"
TARBALL = DIST / "MonitorNoticias-linux.tar.gz"
APP_ID = "io.github.nerqs.MonitorNoticias"
COMMAND = "monitor-noticias"  # nome no PATH (.deb e Arch)
REPO_URL = "https://github.com/Nerqs/monitor-noticias"
SUMMARY = "Notícias de tecnologia e falhas críticas, priorizadas para a sua stack"
DESCRIPTION = ("Coleta notícias de segurança e tecnologia em vários sites, coloca no topo as falhas "
               "críticas que afetam o que você usa e mostra tudo num leitor próprio, com envio por e-mail.")


# Bibliotecas do sistema que o executável usa e não leva (empacotar.LINUX_SYSTEM_LIBS).
DEB_DEPENDS = ("libc6 (>= 2.35), libstdc++6, libegl1, libgl1, libgbm1, libfontconfig1, libfreetype6, "
               "libnss3, libxkbcommon0, libxkbcommon-x11-0, libdbus-1-3, libglib2.0-0 | libglib2.0-0t64, "
               "libasound2 | libasound2t64")
ARCH_DEPENDS = " ".join(f"'{d}'" for d in (
    "glibc", "gcc-libs", "libglvnd", "mesa", "fontconfig", "freetype2", "nss", "libxkbcommon-x11",
    "dbus", "glib2", "alsa-lib"))


def version() -> str:
    text = (ROOT / "monitor" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def install_mark(fmt: str) -> str:
    return f"[instalacao]\nversao={version()}\nformato={fmt}\n"


def desktop_entry(exec_cmd: str) -> str:
    return ("[Desktop Entry]\nType=Application\nName=Monitor de notícias\n"
            f"Comment={SUMMARY}\nExec={exec_cmd} %U\nIcon={APP_ID}\nTerminal=false\n"
            "Categories=Network;News;\nKeywords=notícias;segurança;CVE;tecnologia;games;\n"
            "StartupNotify=true\n")


def icon(size: int, dest: Path) -> Path:
    from PIL import Image

    dest.parent.mkdir(parents=True, exist_ok=True)
    Image.open(ROOT / "assets" / "icone.png").convert("RGBA").resize((size, size), Image.LANCZOS).save(dest)
    return dest


def metainfo() -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop-application">
  <id>{APP_ID}</id>
  <name>Monitor de notícias</name>
  <summary>{SUMMARY}</summary>
  <metadata_license>CC0-1.0</metadata_license>
  <project_license>LicenseRef-proprietary</project_license>
  <developer id="io.github.nerqs"><name>Nerqs</name></developer>
  <description><p>{DESCRIPTION}</p></description>
  <launchable type="desktop-id">{APP_ID}.desktop</launchable>
  <url type="homepage">{REPO_URL}</url>
  <content_rating type="oars-1.1"/>
  <releases><release version="{version()}"/></releases>
</component>
"""


def _fresh(path: Path) -> Path:
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def _need_binary() -> None:
    if not BIN.is_file():
        sys.exit(f"Não achei {BIN}. Rode antes: python empacotar.py")


# --- AppImage -------------------------------------------------------------------------

def build_appimage() -> Path:
    _need_binary()
    tool = os.environ.get("APPIMAGETOOL") or shutil.which("appimagetool")
    if not tool:
        sys.exit("appimagetool não encontrado (defina APPIMAGETOOL ou coloque no PATH).")
    appdir = _fresh(DIST / "AppDir")
    bindir = appdir / "usr" / "bin"
    bindir.mkdir(parents=True)
    shutil.copy2(BIN, bindir / "MonitorNoticias")
    (bindir / "instalacao.ini").write_text(install_mark("appimage"), encoding="utf-8")
    app_run = appdir / "AppRun"
    app_run.write_text('#!/bin/sh\nHERE="$(dirname "$(readlink -f "$0")")"\n'
                       'exec "$HERE/usr/bin/MonitorNoticias" "$@"\n', encoding="utf-8")
    app_run.chmod(0o755)
    (appdir / f"{APP_ID}.desktop").write_text(desktop_entry("MonitorNoticias"), encoding="utf-8")
    icon(256, appdir / f"{APP_ID}.png")
    icon(256, appdir / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps" / f"{APP_ID}.png")
    out = DIST / "MonitorNoticias-linux-x86_64.AppImage"
    out.unlink(missing_ok=True)
    env = {**os.environ, "ARCH": "x86_64", "APPIMAGE_EXTRACT_AND_RUN": "1"}
    subprocess.run([tool, "--no-appstream", str(appdir), str(out)], check=True, env=env)
    return out


# --- .deb -----------------------------------------------------------------------------

def build_deb() -> Path:
    _need_binary()
    root = _fresh(DIST / "deb")
    opt = root / "opt" / COMMAND
    opt.mkdir(parents=True)
    shutil.copy2(BIN, opt / "MonitorNoticias")
    (opt / "MonitorNoticias").chmod(0o755)
    (opt / "instalacao.ini").write_text(install_mark("deb"), encoding="utf-8")
    (root / "usr" / "bin").mkdir(parents=True)
    os.symlink(f"/opt/{COMMAND}/MonitorNoticias", root / "usr" / "bin" / COMMAND)
    share = root / "usr" / "share"
    (share / "applications").mkdir(parents=True)
    (share / "applications" / f"{APP_ID}.desktop").write_text(desktop_entry(COMMAND), encoding="utf-8")
    for size in (128, 256, 512):
        icon(size, share / "icons" / "hicolor" / f"{size}x{size}" / "apps" / f"{APP_ID}.png")
    (share / "metainfo").mkdir(parents=True)
    (share / "metainfo" / f"{APP_ID}.metainfo.xml").write_text(metainfo(), encoding="utf-8")
    size_kb = sum(f.stat().st_size for f in root.rglob("*") if f.is_file() and not f.is_symlink()) // 1024
    control = root / "DEBIAN"
    control.mkdir()
    (control / "control").write_text(
        f"Package: {COMMAND}\nVersion: {version()}\nArchitecture: amd64\n"
        "Maintainer: Nerqs <96776286+Nerqs@users.noreply.github.com>\nSection: net\nPriority: optional\n"
        f"Depends: {DEB_DEPENDS}\nInstalled-Size: {size_kb}\nHomepage: {REPO_URL}\n"
        f"Description: {SUMMARY}\n {DESCRIPTION}\n", encoding="utf-8")
    out = DIST / "MonitorNoticias-linux-amd64.deb"
    subprocess.run(["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(root), str(out)], check=True)
    return out


# --- Arch (PKGBUILD) ------------------------------------------------------------------

def build_pkgbuild() -> Path:
    """PKGBUILD que baixa o .tar.gz da versão publicada e confere o SHA-256."""
    if not TARBALL.is_file():
        sys.exit(f"Não achei {TARBALL}. Rode antes: python empacotar.py")
    ver = version()
    sha = hashlib.sha256(TARBALL.read_bytes()).hexdigest()
    entry = desktop_entry(COMMAND).rstrip("\n")
    text = f"""# PKGBUILD do Monitor de notícias (gerado por linux/pacotes.py).
# Instalar: makepkg -si   (na mesma pasta deste arquivo)
pkgname={COMMAND}-bin
pkgver={ver}
pkgrel=1
pkgdesc="{SUMMARY}"
arch=('x86_64')
url="{REPO_URL}"
license=('LicenseRef-proprietary')
depends=({ARCH_DEPENDS})
provides=('{COMMAND}')
conflicts=('{COMMAND}')
# O executável do PyInstaller carrega os módulos anexados ao fim do arquivo: strip estraga.
options=('!strip' '!debug')
source=("MonitorNoticias-linux-$pkgver.tar.gz::{REPO_URL}/releases/download/v$pkgver/MonitorNoticias-linux.tar.gz")
sha256sums=('{sha}')

package() {{
  cd "$srcdir/MonitorNoticias"
  install -Dm755 MonitorNoticias "$pkgdir/opt/{COMMAND}/MonitorNoticias"
  printf '[instalacao]\\nversao=%s\\nformato=arch\\n' "$pkgver" > instalacao.ini
  install -Dm644 instalacao.ini "$pkgdir/opt/{COMMAND}/instalacao.ini"
  install -d "$pkgdir/usr/bin"
  ln -s "/opt/{COMMAND}/MonitorNoticias" "$pkgdir/usr/bin/{COMMAND}"
  install -Dm644 icone.png "$pkgdir/usr/share/icons/hicolor/512x512/apps/{APP_ID}.png"
  install -d "$pkgdir/usr/share/applications"
  cat > "$pkgdir/usr/share/applications/{APP_ID}.desktop" <<'EOF'
{entry}
EOF
}}
"""
    out = DIST / "PKGBUILD"
    out.write_text(text, encoding="utf-8")
    return out


# --- Flatpak --------------------------------------------------------------------------

FLATPAK_PERMISSIONS = [
    "--share=network",
    "--share=ipc",
    "--socket=wayland",
    "--socket=fallback-x11",
    "--device=dri",
    "--talk-name=org.freedesktop.Notifications",   # avisos de notícia nova
    "--talk-name=org.kde.StatusNotifierWatcher",   # ícone na bandeja
    "--talk-name=org.freedesktop.secrets",         # senha do e-mail no chaveiro
    "--filesystem=xdg-config/autostart:create",    # iniciar com o sistema
    # O executável se descompacta numa pasta temporária a cada abertura (~700 MB com o Qt).
    # O /tmp do Flatpak é pequeno e fica na memória; o /var/tmp dele fica no disco.
    "--env=TMPDIR=/var/tmp",
]


def build_flatpak_dir() -> Path:
    """Pasta com o manifesto e os arquivos; o flatpak-builder monta o pacote a partir dela."""
    _need_binary()
    folder = _fresh(DIST / "flatpak")
    shutil.copy2(BIN, folder / "MonitorNoticias")
    (folder / "instalacao.ini").write_text(install_mark("flatpak"), encoding="utf-8")
    (folder / f"{APP_ID}.desktop").write_text(desktop_entry("MonitorNoticias"), encoding="utf-8")
    (folder / f"{APP_ID}.metainfo.xml").write_text(metainfo(), encoding="utf-8")
    icon(512, folder / "icone.png")
    manifest = {
        "id": APP_ID,
        "runtime": "org.freedesktop.Platform",
        "runtime-version": "24.08",
        "sdk": "org.freedesktop.Sdk",
        "command": "MonitorNoticias",
        "finish-args": FLATPAK_PERMISSIONS,
        "modules": [{
            "name": "monitor-noticias",
            "buildsystem": "simple",
            # Sem strip: o executável do PyInstaller traz os módulos anexados ao fim do arquivo.
            "build-options": {"no-debuginfo": True, "strip": False},
            "build-commands": [
                "install -Dm755 MonitorNoticias /app/bin/MonitorNoticias",
                "install -Dm644 instalacao.ini /app/bin/instalacao.ini",
                f"install -Dm644 icone.png /app/share/icons/hicolor/512x512/apps/{APP_ID}.png",
                f"install -Dm644 {APP_ID}.desktop /app/share/applications/{APP_ID}.desktop",
                f"install -Dm644 {APP_ID}.metainfo.xml /app/share/metainfo/{APP_ID}.metainfo.xml",
            ],
            "sources": [{"type": "file", "path": name} for name in (
                "MonitorNoticias", "instalacao.ini", "icone.png", f"{APP_ID}.desktop", f"{APP_ID}.metainfo.xml")],
        }],
    }
    (folder / f"{APP_ID}.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return folder


BUILDERS = {"appimage": build_appimage, "deb": build_deb, "pkgbuild": build_pkgbuild, "flatpak": build_flatpak_dir}


def main(argv: list[str]) -> int:
    wanted = argv or list(BUILDERS)
    unknown = [w for w in wanted if w not in BUILDERS]
    if unknown:
        sys.exit(f"Formato desconhecido: {', '.join(unknown)}. Use: {', '.join(BUILDERS)}")
    for name in wanted:
        print(f"{name}: {BUILDERS[name]()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
