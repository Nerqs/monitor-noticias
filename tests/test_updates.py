"""Atualização com um clique, instalador e migração dos dados da versão .zip."""

import hashlib
import io
import sys
import tarfile

import httpx
import pytest

from monitor import config, fetchers, pipeline, updates, web


def release_with(name: str, package: bytes, sums: str | None = None):
    sums = sums or hashlib.sha256(package).hexdigest()

    def handler(request):
        path = request.url.path
        if path.endswith("/latest"):
            return httpx.Response(200, json={"tag_name": "v9.9.9", "assets": [
                {"name": name, "url": "https://api.github.com/assets/1"},
                {"name": "SHA256SUMS.txt", "url": "https://api.github.com/assets/2"}]})
        if path.endswith("/1"):
            return httpx.Response(200, content=package)
        return httpx.Response(200, text=f"{sums}  {name}\n")
    return httpx.MockTransport(handler)


@pytest.fixture
def github(monkeypatch):
    def use(transport):
        monkeypatch.setattr(pipeline, "client_for",
                            lambda settings, retries=True: fetchers.make_client(transport=transport))
    return use


@pytest.fixture
def launched(monkeypatch):
    calls = []
    monkeypatch.setattr(updates, "_launch", calls.append)
    return calls


def test_installed_windows_runs_installer_silently(monkeypatch, github, launched, data_dir):
    monkeypatch.setattr(updates, "mode", lambda: "instalado")
    github(release_with(updates.INSTALLER, b"instalador"))
    check = updates.check()
    assert check["newer"] and check["auto"] and check["mode"] == "instalado"
    result = updates.apply()
    assert result["version"] == "9.9.9"
    exe = data_dir / "atualizacoes" / f"v9.9.9-{updates.INSTALLER}"
    assert exe.read_bytes() == b"instalador"
    assert launched == [[str(exe), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"]]


def test_portable_opens_installer_wizard(monkeypatch, github, launched):
    monkeypatch.setattr(updates, "mode", lambda: "portatil")
    github(release_with(updates.INSTALLER, b"instalador"))
    result = updates.apply()
    assert len(launched[0]) == 1 and "copiados" in result["message"]


def test_bad_hash_launches_nothing(monkeypatch, github, launched, data_dir):
    monkeypatch.setattr(updates, "mode", lambda: "instalado")
    github(release_with(updates.INSTALLER, b"instalador", sums="0" * 64))
    with pytest.raises(updates.UpdateError, match="não confere"):
        updates.apply()
    assert launched == [] and not list((data_dir / "atualizacoes").glob("*"))


def test_source_checkout_cannot_self_update(monkeypatch, github, launched):
    monkeypatch.setattr(updates, "mode", lambda: "fonte")
    github(release_with(updates.INSTALLER, b"x"))
    assert updates.check()["auto"] is False
    with pytest.raises(updates.UpdateError, match="git pull"):
        updates.apply()


def test_linux_replaces_executable(monkeypatch, github, launched, tmp_path):
    exe = tmp_path / "bin" / "MonitorNoticias"
    exe.parent.mkdir()
    exe.write_bytes(b"velho")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("MonitorNoticias/MonitorNoticias")
        info.size = 4
        tar.addfile(info, io.BytesIO(b"novo"))
    monkeypatch.setattr(updates, "mode", lambda: "linux")
    monkeypatch.setattr(sys, "executable", str(exe))
    github(release_with("MonitorNoticias-linux.tar.gz", buf.getvalue()))
    updates.apply()
    assert exe.read_bytes() == b"novo" and exe.stat().st_mode & 0o111
    assert launched[0][:2] == ["sh", "-c"] and launched[0][3] == str(exe)


def test_apply_route_closes_program_and_skip_is_saved(monkeypatch):
    closed = []
    monkeypatch.setattr(updates, "apply", lambda: {"version": "9.9.9", "message": "ok"})
    monkeypatch.setattr(updates, "exit_soon", lambda: closed.append(True))
    client = web.create_app("t").test_client()
    assert client.post("/api/updates/apply", headers={"X-Token": "t"}).get_json()["version"] == "9.9.9"
    assert closed == [True]
    client.post("/api/updates/skip", json={"version": "9.9.9"}, headers={"X-Token": "t"})
    assert config.load_config()["updates"]["skip_version"] == "9.9.9"
    assert b'data-check-updates="1"' in client.get("/").data


def test_installed_copies_portable_data_once(monkeypatch, tmp_path):
    portable = tmp_path / "zip" / "data"
    (portable / "logs").mkdir(parents=True)
    (portable / "atualizacoes").mkdir()
    (portable / "monitor.db").write_bytes(b"banco")
    (portable / "config.yaml").write_text("x: 1")
    (portable / "atualizacoes" / "pacote.zip").write_bytes(b"grande")
    target = tmp_path / "local" / "MonitorNoticias"
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.delenv("MONITOR_DATA_DIR")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "zip" / "MonitorNoticias.exe"))

    config.remember_portable()  # rodando a versão .zip
    assert (target / config.PORTABLE_NOTE).read_text() == str(portable.resolve())
    assert config.data_dir() == portable

    (tmp_path / "zip" / config.INSTALL_MARK).write_text("[instalacao]")  # agora "instalado"
    assert config.is_installed() and config.data_dir() == target
    assert config.migrate_portable() == portable.resolve()
    assert (target / "monitor.db").read_bytes() == b"banco"
    assert (target / "config.yaml").exists() and not (target / "atualizacoes").exists()
    assert (portable / config.MOVED_NOTE).exists() and not (target / config.PORTABLE_NOTE).exists()

    (target / "monitor.db").write_bytes(b"novo")
    (target / config.PORTABLE_NOTE).write_text(str(portable))
    assert config.migrate_portable() is None  # já tem banco: não sobrescreve
    assert (target / "monitor.db").read_bytes() == b"novo"
