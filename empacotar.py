"""Gera o executável do Monitor de notícias com PyInstaller.

Uso: python empacotar.py

Resultado:
  Windows: dist/MonitorNoticias/MonitorNoticias.exe (pasta inteira, compactada em
           dist/MonitorNoticias-windows.zip)
  Linux:   dist/MonitorNoticias (arquivo único), compactado em dist/MonitorNoticias-linux.tar.gz
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "MonitorNoticias"
WINDOWS = sys.platform == "win32"


def main() -> int:
    sep = os.pathsep
    args = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--name", NAME,
        "--icon", str(ROOT / "assets" / "icone.ico"),
        "--add-data", f"{ROOT / 'monitor' / 'templates'}{sep}monitor/templates",
        "--add-data", f"{ROOT / 'monitor' / 'static'}{sep}monitor/static",
        "--add-data", f"{ROOT / 'monitor' / 'defaults'}{sep}monitor/defaults",
        "--collect-submodules", "keyring.backends",
        "--collect-submodules", "webview",
        "--collect-data", "readability",
    ]
    if WINDOWS:
        # Pasta (abre mais rápido e o antivírus implica menos) e sem janela de console.
        args += ["--onedir", "--windowed"]
    else:
        args += ["--onefile"]
    args.append(str(ROOT / "run.py"))
    subprocess.run(args, check=True, cwd=ROOT)

    dist = ROOT / "dist"
    if WINDOWS:
        folder = dist / NAME
        shutil.copy(ROOT / "LEIA-ME.txt", folder / "LEIA-ME.txt")
        archive = shutil.make_archive(str(dist / f"{NAME}-windows"), "zip", dist, NAME)
    else:
        archive = dist / f"{NAME}-linux.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(dist / NAME, arcname=f"{NAME}/{NAME}")
            tar.add(ROOT / "LEIA-ME.txt", arcname=f"{NAME}/LEIA-ME.txt")
    print(f"Pacote gerado: {archive}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
