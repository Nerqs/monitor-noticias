import os

import pytest

from monitor import config, diagnostics, fetchers, scheduler, secrets, web


@pytest.fixture
def android(monkeypatch):
    monkeypatch.setenv("MONITOR_PLATFORM", "android")
    secrets._keyring.cache_clear()
    yield
    secrets._keyring.cache_clear()


def test_secrets_in_private_file(android, data_dir):
    assert secrets.keyring_available()
    assert secrets.set_secret("telegram_token", "123:abc")
    assert secrets.get_secret("telegram_token") == "123:abc"
    if os.name == "posix":  # o Android é Linux; no Windows não há bits de permissão POSIX
        assert (data_dir / "segredos.json").stat().st_mode & 0o077 == 0
    secrets.set_secret("telegram_token", None)
    assert secrets.get_secret("telegram_token") is None


def test_android_hides_desktop_features(android):
    assert scheduler.status()["android"] is True
    with pytest.raises(RuntimeError):
        scheduler.install(config.load_config())
    names = {c["name"] for c in diagnostics.run(quick=True)}
    assert "Janela própria" not in names and "Agendamento" not in names
    html = web.create_app("t").test_client().get("/").get_data(as_text=True)
    assert 'class="android"' in html


def test_simple_article_extraction():
    page = """<html><body><nav><p>menu menu</p></nav><div class="side"><p>curto</p></div>
      <article><h1>Título</h1><p>Primeiro parágrafo longo do artigo com conteúdo.</p><p>Segundo parágrafo.</p></article>
      <footer><p>rodapé</p></footer></body></html>"""
    html = fetchers.simple_main_content(page)
    assert "Primeiro parágrafo" in html and "menu" not in html and "rodapé" not in html
