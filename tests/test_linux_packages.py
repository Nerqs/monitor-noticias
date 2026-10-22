"""Gerador dos pacotes Linux (linux/pacotes.py): PKGBUILD e manifesto do Flatpak."""

import importlib.util
import json
from pathlib import Path

import pytest

from monitor import __version__, updates

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def pacotes(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("pacotes", ROOT / "linux" / "pacotes.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "MonitorNoticias").write_bytes(b"binario")
    (dist / "MonitorNoticias-linux.tar.gz").write_bytes(b"tarball")
    monkeypatch.setattr(module, "DIST", dist)
    monkeypatch.setattr(module, "BIN", dist / "MonitorNoticias")
    monkeypatch.setattr(module, "TARBALL", dist / "MonitorNoticias-linux.tar.gz")
    return module


def test_pkgbuild_downloads_release_and_marks_arch(pacotes):
    import hashlib

    text = pacotes.build_pkgbuild().read_text()
    assert f"pkgver={__version__}" in text
    assert "releases/download/v$pkgver/MonitorNoticias-linux.tar.gz" in text
    assert hashlib.sha256(b"tarball").hexdigest() in text
    assert "formato=arch" in text and "'!strip'" in text
    assert f'/usr/bin/{pacotes.COMMAND}"' in text and updates.LINUX_COMMAND == f"/usr/bin/{pacotes.COMMAND}"


def test_flatpak_manifest_keeps_binary_intact(pacotes):
    folder = pacotes.build_flatpak_dir()
    manifest = json.loads((folder / f"{pacotes.APP_ID}.json").read_text())
    module = manifest["modules"][0]
    assert module["build-options"]["strip"] is False
    assert "formato=flatpak" in (folder / "instalacao.ini").read_text()
    for source in module["sources"]:
        assert (folder / source["path"]).exists()
    assert "--share=network" in manifest["finish-args"]
    assert "--filesystem=home" not in manifest["finish-args"]  # só o necessário


def test_desktop_entry_has_menu_fields(pacotes):
    entry = pacotes.desktop_entry("monitor-noticias")
    assert entry.startswith("[Desktop Entry]") and f"Icon={pacotes.APP_ID}" in entry
    assert "Exec=monitor-noticias %U" in entry and "Categories=" in entry
