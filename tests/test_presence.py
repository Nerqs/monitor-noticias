from monitor import presence


def setup_function():
    presence.reset()


def test_sem_pagina_ainda_nao_encerra():
    assert not presence.page_gone(now=10_000)


def test_tchau_encerra_depois_da_folga(monkeypatch):
    monkeypatch.setattr(presence.time, "monotonic", lambda: 100.0)
    presence.beat()
    presence.bye()
    assert not presence.page_gone(now=100.0 + presence.BYE_GRACE - 1)
    assert presence.page_gone(now=100.0 + presence.BYE_GRACE)


def test_recarregar_cancela_o_tchau(monkeypatch):
    monkeypatch.setattr(presence.time, "monotonic", lambda: 100.0)
    presence.beat()
    presence.bye()
    monkeypatch.setattr(presence.time, "monotonic", lambda: 102.0)
    presence.beat()
    assert not presence.page_gone(now=102.0 + presence.BYE_GRACE + 1)


def test_aba_sumida_sem_avisar(monkeypatch):
    monkeypatch.setattr(presence.time, "monotonic", lambda: 0.0)
    presence.beat()
    assert not presence.page_gone(now=presence.IDLE_LIMIT - 1)
    assert presence.page_gone(now=presence.IDLE_LIMIT)


def test_endpoints():
    from monitor import web

    app = web.create_app("t")
    app.testing = True
    c = app.test_client()
    assert c.post("/api/heartbeat", headers={"X-Token": "t"}).status_code == 200
    assert c.post("/api/bye", headers={"X-Token": "t"}).status_code == 200
    assert c.post("/api/bye").status_code == 403
