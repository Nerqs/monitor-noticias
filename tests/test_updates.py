"""Atualização com um clique, instalador e migração dos dados da versão .zip."""

import hashlib
import io
import os
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
    assert exe.read_bytes() == b"novo"
    if os.name == "posix":  # no Windows não existe bit de execução
        assert exe.stat().st_mode & 0o111
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


# --- Pacotes Linux (AppImage, .deb, Arch, Flatpak) -----------------------------------

@pytest.fixture
def packaged(monkeypatch, tmp_path):
    """Simula o executável instalado por um pacote Linux com o instalacao.ini ao lado."""
    def install(fmt: str):
        folder = tmp_path / "pacote" / fmt
        folder.mkdir(parents=True)
        exe = folder / "MonitorNoticias"
        exe.write_bytes(b"velho")
        (folder / config.INSTALL_MARK).write_text(f"[instalacao]\nversao=0.1\nformato={fmt}\n")
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(exe))
        monkeypatch.setattr(sys, "platform", "linux")
        return exe
    return install


@pytest.mark.parametrize("fmt", ["appimage", "deb", "arch", "flatpak"])
def test_linux_package_format_is_detected(packaged, fmt):
    packaged(fmt)
    assert config.is_installed() and config.install_format() == fmt
    assert updates.mode() == fmt
    assert updates.asset_name() == updates.LINUX_PACKAGES[fmt]


def test_linux_tarball_without_mark_stays_linux(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "MonitorNoticias"))
    monkeypatch.setattr(sys, "platform", "linux")
    assert updates.mode() == "linux" and updates.asset_name() == updates.TARBALL


def test_packaged_linux_keeps_data_in_xdg_data_home(packaged, monkeypatch, tmp_path):
    packaged("flatpak")
    monkeypatch.delenv("MONITOR_DATA_DIR")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "var" / "data"))
    assert config.data_dir() == tmp_path / "var" / "data" / "MonitorNoticias"


def test_appimage_replaces_its_own_file(packaged, monkeypatch, github, launched, tmp_path):
    packaged("appimage")
    image = tmp_path / "Baixados" / "Monitor.AppImage"
    image.parent.mkdir()
    image.write_bytes(b"velho")
    monkeypatch.setenv("APPIMAGE", str(image))
    github(release_with(updates.LINUX_PACKAGES["appimage"], b"novo"))
    assert updates.check()["auto"]
    updates.apply()
    assert image.read_bytes() == b"novo"
    if os.name == "posix":
        assert image.stat().st_mode & 0o111
    assert launched[0][:2] == ["sh", "-c"] and launched[0][3] == str(image)


@pytest.mark.parametrize("fmt,tool", [("deb", ["dpkg", "-i"]), ("arch", ["pacman", "-U", "--noconfirm"])])
def test_deb_and_arch_install_with_pkexec(packaged, monkeypatch, github, launched, data_dir, fmt, tool):
    packaged(fmt)
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    github(release_with(updates.LINUX_PACKAGES[fmt], b"pacote"))
    result = updates.apply()
    path = data_dir / "atualizacoes" / f"v9.9.9-{updates.LINUX_PACKAGES[fmt]}"
    assert path.read_bytes() == b"pacote" and "senha" in result["message"]
    command = launched[0]
    assert command[:2] == ["sh", "-c"] and "pkexec" in command[2] and updates.LINUX_COMMAND in command[2]
    assert command[4:] == [*tool, str(path)]


def test_deb_without_pkexec_explains_manual_install(packaged, monkeypatch, github, launched):
    packaged("deb")
    monkeypatch.setattr("shutil.which", lambda name: None)
    github(release_with(updates.LINUX_PACKAGES["deb"], b"pacote"))
    with pytest.raises(updates.UpdateError, match="pkexec"):
        updates.apply()
    assert launched == []


def test_flatpak_only_downloads(packaged, github, launched):
    packaged("flatpak")
    github(release_with(updates.LINUX_PACKAGES["flatpak"], b"bundle"))
    check = updates.check()
    assert not check["auto"] and check["has_package"] and "flatpak install" in check["hint"]
    with pytest.raises(updates.UpdateError, match="Baixar e conferir"):
        updates.apply()
    result = updates.download()
    assert result["path"].endswith(updates.LINUX_PACKAGES["flatpak"]) and "flatpak install" in result["how"]
    assert launched == []


def test_appimage_creates_menu_shortcut_once(packaged, monkeypatch, tmp_path):
    from monitor import desktop

    packaged("appimage")
    monkeypatch.setenv("APPIMAGE", "/home/usuario/Apps/Monitor.AppImage")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    entry = desktop.integrate_appimage()
    text = entry.read_text()
    assert 'Exec="/home/usuario/Apps/Monitor.AppImage"' in text and f"Icon={desktop.APP_ID}" in text
    assert (tmp_path / "share" / "icons" / "hicolor" / "128x128" / "apps" / f"{desktop.APP_ID}.png").exists()
    assert desktop.integrate_appimage() is None  # nada mudou
    monkeypatch.setenv("APPIMAGE", "/home/usuario/Outro/Monitor.AppImage")
    assert "Outro" in desktop.integrate_appimage().read_text()  # mudou de pasta: corrige


def test_shortcut_not_created_outside_appimage(packaged, monkeypatch, tmp_path):
    from monitor import desktop

    packaged("deb")
    monkeypatch.setenv("APPIMAGE", "/x.AppImage")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert desktop.integrate_appimage() is None and not (tmp_path / "applications").exists()


def test_schedule_points_to_appimage_and_flatpak(monkeypatch):
    from monitor import scheduler

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/tmp/.mount_abc/usr/bin/MonitorNoticias")
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.setenv("APPIMAGE", "/home/usuario/Monitor.AppImage")
    assert scheduler.program_command("--so-coletar") == ["/home/usuario/Monitor.AppImage", "--so-coletar"]
    monkeypatch.setenv("FLATPAK_ID", "io.github.nerqs.MonitorNoticias")
    assert scheduler.program_command("--residente") == ["flatpak", "run", "io.github.nerqs.MonitorNoticias",
                                                        "--residente"]


def test_flatpak_schedule_explains_alternative(monkeypatch):
    from monitor import scheduler

    monkeypatch.setattr(scheduler, "WINDOWS", False)
    monkeypatch.setenv("FLATPAK_ID", "io.github.nerqs.MonitorNoticias")
    with pytest.raises(RuntimeError, match="modo de alertas"):
        scheduler.install(config.load_config())
