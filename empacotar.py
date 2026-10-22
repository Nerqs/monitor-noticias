"""Gera o executável do Monitor de notícias com PyInstaller.

Uso: python empacotar.py

Resultado:
  Windows: dist/MonitorNoticias/MonitorNoticias.exe (pasta inteira, compactada em
           dist/MonitorNoticias-windows.zip) e o instalador com assistente
           dist/MonitorNoticias-instalador-windows.exe (precisa do Inno Setup 6)
  Linux:   dist/MonitorNoticias (arquivo único), compactado em dist/MonitorNoticias-linux.tar.gz.
           AppImage, .deb, PKGBUILD e Flatpak saem depois com: python linux/pacotes.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "MonitorNoticias"
WINDOWS = sys.platform == "win32"


def write_build_deps() -> None:
    """Grava as versões das bibliotecas para a autoauditoria do executável."""
    from importlib import metadata

    deps = {d.metadata["Name"].lower(): d.version for d in metadata.distributions() if d.metadata.get("Name")}
    (ROOT / "monitor" / "_build_deps.json").write_text(json.dumps(deps, indent=1, sort_keys=True), encoding="utf-8")


def find_iscc() -> str | None:
    """Compilador do Inno Setup (variável ISCC, PATH ou a pasta padrão)."""
    candidates = [os.environ.get("ISCC"), shutil.which("iscc")]
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            candidates += [os.path.join(base, "Inno Setup 6", "ISCC.exe"),
                           os.path.join(base, "Programs", "Inno Setup 6", "ISCC.exe")]
    return next((c for c in candidates if c and os.path.exists(c)), None)


def build_installer() -> Path | None:
    iscc = find_iscc()
    if not iscc:
        print("Inno Setup 6 não encontrado: o instalador não foi gerado (só o .zip).")
        return None
    version = re.search(r'__version__ = "([^"]+)"', (ROOT / "monitor" / "__init__.py").read_text()).group(1)
    subprocess.run([iscc, f"/DMyAppVersion={version}", str(ROOT / "instalador" / f"{NAME}.iss")], check=True, cwd=ROOT)
    return ROOT / "dist" / f"{NAME}-instalador-windows.exe"


# Bibliotecas do sistema que o executável do Linux NÃO leva: as da máquina de build
# (Ubuntu 22.04) são mais velhas que as da distribuição e quebram o que vem dela. Ex.: a
# libstdc++ velha impede o driver de vídeo (Mesa) de carregar e o Qt aborta sem EGL; a
# fontconfig velha não entende a configuração de fontes nova. Todo desktop tem estas.
LINUX_SYSTEM_LIBS = (
    "libstdc++", "libgcc_s", "libgbm", "libdrm", "libEGL", "libGL",
    "libfontconfig", "libfreetype.so", "libharfbuzz.so", "libexpat", "libz.so",
    "libglib-2.0", "libgobject-2.0", "libgio-2.0", "libgmodule-2.0", "libgthread-2.0",
    "libdbus-1", "libsystemd", "libudev", "libselinux", "libmount", "libblkid", "libpcre",
    "libX11", "libXext", "libXau.so", "libXdmcp", "libXfixes", "libXrender", "libXi.so",
    "libXrandr", "libXcursor", "libXcomposite", "libXdamage", "libXtst", "libXinerama",
    "libxcb.so", "libxcb-render.so", "libxcb-shm", "libxcb-xfixes", "libxcb-randr",
    "libxcb-shape", "libxcb-sync", "libxcb-xkb", "libxcb-glx", "libxkbcommon",
    "libasound", "libnss3", "libnssutil3", "libsmime3", "libnspr4", "libplc4", "libplds4",
    "libfreebl", "libsoftokn",
    # Só vêm pelo plugin de tema GTK do Qt, que também fica de fora.
    "libgtk-3", "libgdk-3", "libgdk_pixbuf", "libatk", "libatspi", "libcairo", "libpango",
    "libpixman", "libepoxy", "libthai", "libdatrie", "libfribidi", "libgraphite2", "libcups",
    "libavahi", "libgnutls", "libgssapi", "libkrb5", "libk5crypto", "libcom_err",
)


def linux_spec(options: list[str]) -> Path:
    """Gera o .spec do Linux e tira dele as bibliotecas do sistema (LINUX_SYSTEM_LIBS)."""
    subprocess.run([sys.executable, "-m", "PyInstaller.utils.cliutils.makespec", *options],
                   check=True, cwd=ROOT)
    spec = ROOT / f"{NAME}.spec"
    text = spec.read_text(encoding="utf-8")
    keep = (
        f"SYSTEM_LIBS = {LINUX_SYSTEM_LIBS!r}\n"
        "a.binaries = [b for b in a.binaries\n"
        "              if not os.path.basename(b[0]).startswith(SYSTEM_LIBS)\n"
        "              and 'platformthemes/libqgtk3' not in b[0]]\n"
    )
    text = "import os\n" + text.replace("pyz = PYZ(", keep + "pyz = PYZ(", 1)
    spec.write_text(text, encoding="utf-8")
    return spec


def main() -> int:
    write_build_deps()
    sep = os.pathsep
    args = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--name", NAME,
        "--icon", str(ROOT / "assets" / "icone.ico"),
        "--add-data", f"{ROOT / 'monitor' / 'templates'}{sep}monitor/templates",
        "--add-data", f"{ROOT / 'monitor' / 'static'}{sep}monitor/static",
        "--add-data", f"{ROOT / 'monitor' / 'defaults'}{sep}monitor/defaults",
        "--collect-submodules", "keyring.backends",
        # Tudo do pywebview, inclusive as DLLs do WebView2 (webview/lib), sem as
        # quais a janela própria falha e o programa cai para o navegador.
        "--collect-all", "webview",
        "--collect-submodules", "pystray",
        "--add-data", f"{ROOT / 'monitor' / '_build_deps.json'}{sep}monitor",
        "--collect-data", "readability",
    ]
    if WINDOWS:
        # Pasta (abre mais rápido e o antivírus implica menos) e sem janela de console.
        args += ["--onedir", "--windowed"]
        # No Windows o pywebview usa o Edge (WebView2) através do pythonnet.
        args += ["--collect-all", "clr_loader", "--collect-all", "pythonnet", "--hidden-import", "clr"]
    else:
        args += ["--onefile"]
        # No Linux a janela própria usa o Qt embutido (PySide6, LGPL): o GTK do sistema não
        # carrega no Python do executável. Só os módulos do Qt que o pywebview usa: o
        # resto (QtNfc, drivers de SQL...) deixaria o executável com o dobro do tamanho.
        for module in ("QtCore", "QtGui", "QtWidgets", "QtNetwork", "QtWebChannel",
                       "QtWebEngineCore", "QtWebEngineWidgets"):
            args += ["--hidden-import", f"PySide6.{module}"]
        for module in ("PyQt5", "PyQt6", "gi"):
            args += ["--exclude-module", module]
    args.append(str(ROOT / "run.py"))
    if WINDOWS:
        subprocess.run(args, check=True, cwd=ROOT)
    else:
        subprocess.run(args[:5] + [str(linux_spec(args[5:]))], check=True, cwd=ROOT)

    dist = ROOT / "dist"
    if WINDOWS:
        folder = dist / NAME
        shutil.copy(ROOT / "LEIA-ME.txt", folder / "LEIA-ME.txt")
        archive = shutil.make_archive(str(dist / f"{NAME}-windows"), "zip", dist, NAME)
        installer = build_installer()
        if installer:
            print(f"Instalador gerado: {installer}")
    else:
        archive = dist / f"{NAME}-linux.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(dist / NAME, arcname=f"{NAME}/{NAME}")
            tar.add(ROOT / "LEIA-ME.txt", arcname=f"{NAME}/LEIA-ME.txt")
            from PIL import Image

            icon = dist / "icone-512.png"  # usado pelo PKGBUILD para o atalho no menu
            Image.open(ROOT / "assets" / "icone.png").resize((512, 512), Image.LANCZOS).save(icon)
            tar.add(icon, arcname=f"{NAME}/icone.png")
    print(f"Pacote gerado: {archive}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
