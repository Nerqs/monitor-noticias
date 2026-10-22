"""Atalho no menu de aplicativos para o AppImage.

Os pacotes .deb, Arch e Flatpak já instalam o atalho. O AppImage é um arquivo
solto, então na primeira vez ele mesmo cria o atalho em ~/.local/share/applications
(e o atualiza se o arquivo mudar de pasta).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from . import config

APP_ID = "io.github.nerqs.MonitorNoticias"


def _data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def entry(exec_path: str) -> str:
    return ("[Desktop Entry]\nType=Application\nName=Monitor de notícias\n"
            "Comment=Notícias de tecnologia e falhas críticas\n"
            f'Exec="{exec_path}" %U\nTryExec={exec_path}\nIcon={APP_ID}\nTerminal=false\n'
            "Categories=Network;News;\nKeywords=notícias;segurança;CVE;tecnologia;\n"
            f"StartupWMClass=MonitorNoticias\nX-AppImage-Integrated=true\n")


def integrate_appimage() -> Path | None:
    """Cria ou corrige o atalho do AppImage. Devolve o arquivo .desktop quando muda algo."""
    image = os.environ.get("APPIMAGE")
    if not image or config.install_format() != "appimage":
        return None
    target = _data_home() / "applications" / f"{APP_ID}.desktop"
    text = entry(image)
    if target.exists() and target.read_text(encoding="utf-8", errors="replace") == text:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    icon = _data_home() / "icons" / "hicolor" / "128x128" / "apps" / f"{APP_ID}.png"
    if not icon.exists():
        icon.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(config.PACKAGE_DIR / "static" / "icone.png", icon)
    return target
