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


def test_migracao_com_varias_threads(data_dir):
    """Banco antigo + várias requisições ao abrir não pode dar "duplicate column name"."""
    import sqlite3
    import threading

    from monitor import db

    db.connect()
    db.close()
    conn = sqlite3.connect(db.db_path())
    for name, _ddl in db.NEW_ITEM_COLUMNS[-2:]:
        conn.execute(f"ALTER TABLE items DROP COLUMN {name}")
    conn.commit()
    conn.close()

    errors = []

    def worker():
        try:
            db.connect()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            db.close()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_never_opened_only_without_any_beat():
    assert not presence.never_opened(since=0.0, now=presence.OPEN_LIMIT - 1)
    assert presence.never_opened(since=0.0, now=presence.OPEN_LIMIT)
    presence.beat()
    assert not presence.never_opened(since=0.0, now=presence.OPEN_LIMIT * 10)


def test_wait_returns_false_when_browser_never_opens(monkeypatch):
    clock = iter(range(0, 10_000, 60))
    monkeypatch.setattr(presence.time, "monotonic", lambda: float(next(clock)))
    assert presence.wait_until_gone(poll=0) is False
