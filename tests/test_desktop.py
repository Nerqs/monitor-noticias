"""Bandeja, iniciar com o sistema, coleta que ficou para trás e notificações."""

import threading
from datetime import datetime, timedelta

from monitor import config, db, instance, pipeline, resident, scheduler, web

from test_app import add_items

H = {"X-Token": "t"}


def _schedule(time="07:00", weekdays=True, installed=True):
    cfg = config.load_config()
    cfg["schedule"].update(time=time, weekdays_only=weekdays, installed=installed)
    return cfg


def _local(*args):
    return datetime(*args).astimezone()


def test_last_slot_skips_weekend():
    cfg = _schedule("07:00", weekdays=True)
    monday_early = _local(2026, 10, 19, 6, 0)  # segunda antes do horário: vale a sexta
    assert scheduler.last_slot(cfg, monday_early).weekday() == 4
    assert scheduler.last_slot(cfg, _local(2026, 10, 21, 9, 0)) == _local(2026, 10, 21, 7, 0)
    cfg = _schedule("07:00", weekdays=False)
    assert scheduler.last_slot(cfg, monday_early) == _local(2026, 10, 18, 7, 0)


def test_collection_due(monkeypatch):
    now = _local(2026, 10, 21, 9, 0)
    assert not scheduler.collection_due(_schedule(installed=False), now)
    cfg = _schedule("07:00")
    monkeypatch.setattr(scheduler, "last_run_time", lambda: None)
    assert scheduler.collection_due(cfg, now)  # nunca coletou
    monkeypatch.setattr(scheduler, "last_run_time", lambda: _local(2026, 10, 20, 7, 0))
    assert scheduler.collection_due(cfg, now)  # a de hoje não rodou
    monkeypatch.setattr(scheduler, "last_run_time", lambda: _local(2026, 10, 21, 7, 2))
    assert not scheduler.collection_due(cfg, now)


def test_collection_due_uses_last_run():
    cfg = _schedule("00:00", weekdays=False)
    assert scheduler.collection_due(cfg)
    db.finish_run(db.start_run(), 1, 0, 0)
    assert not scheduler.collection_due(cfg)


def test_run_in_background_marks_running_at_once(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(pipeline, "_run_locked", lambda ids, workers: (release.wait(5), pipeline._run_lock.release()))
    assert pipeline.run_in_background(origin="bandeja")
    snap = pipeline.progress.snapshot()
    assert snap["running"] and snap["origin"] == "bandeja"  # a tela já vê a coleta logo depois do clique
    assert not pipeline.run_in_background()
    release.set()
    for _ in range(50):
        if not pipeline._run_lock.locked():
            break
        threading.Event().wait(0.05)
    pipeline.progress.reset()


def test_failed_lock_does_not_leave_running():
    pipeline.progress.reset()
    assert pipeline._run_lock.acquire(blocking=False)
    try:
        assert pipeline.run_collection()["error"]
        assert not pipeline.progress.snapshot()["running"]
    finally:
        pipeline._run_lock.release()


def test_notify_new_summary_and_critical(monkeypatch):
    shown = []
    desk = resident.Desktop("http://127.0.0.1:1/")
    monkeypatch.setattr(desk, "notify", lambda title, message, urgent=False: shown.append((title, message, urgent)))
    add_items()
    assert desk.notify_new(config.load_config()) == 3
    assert any(urgent and title.startswith("Crítico: Zero-day") for title, _, urgent in shown)
    assert any("3 notícias novas" in message for _, message, _ in shown)
    shown.clear()
    assert desk.notify_new(config.load_config()) == 0  # cada notícia avisa uma vez só
    assert shown == []


def test_notify_respects_mode(monkeypatch):
    shown = []
    desk = resident.Desktop("http://127.0.0.1:1/")
    monkeypatch.setattr(desk, "notify", lambda title, message, urgent=False: shown.append(title))
    add_items()
    cfg = config.load_config()
    cfg["alerts"]["notify"] = "nenhuma"
    desk.notify_new(cfg)
    assert shown == []
    assert db.items_to_notify() == []


def test_existing_items_are_not_notified_on_start():
    add_items()
    db.mark_all_notified()
    assert db.items_to_notify() == []


def test_manual_collection_with_window_open_is_not_notified(monkeypatch):
    shown = []
    desk = resident.Desktop("http://127.0.0.1:1/")
    monkeypatch.setattr(desk, "notify", lambda *a, **k: shown.append(a))
    desk._seen_run = None
    add_items()
    db.finish_run(db.start_run(), 1, 0, 3)
    pipeline.progress.begin("manual")
    pipeline.progress.update(running=False)
    desk.check_finished(config.load_config())
    assert shown == [] and db.items_to_notify() == []


def test_app_endpoint_hooks_and_remember(monkeypatch):
    called = threading.Event()
    monkeypatch.setattr(web, "app_hooks", {"hide": lambda data: called.set()})
    client = web.create_app("t").test_client()
    assert client.post("/api/app", json={"action": "quit"}, headers=H).json == {"ok": False}
    assert client.post("/api/app", json={"action": "hide", "remember": True}, headers=H).json["ok"]
    assert called.wait(3)
    desktop = config.load_config()["desktop"]
    assert desktop["ask_on_close"] is False and desktop["close_to_tray"] is True


def test_start_with_system_via_config(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "install_resident", lambda: calls.append("on"))
    monkeypatch.setattr(scheduler, "uninstall_resident", lambda: calls.append("off"))
    client = web.create_app("t").test_client()
    assert client.get("/api/config", headers=H).json["config"]["desktop"]["start_with_system"] is False
    client.put("/api/config", json={"config": {"desktop": {"start_with_system": True}}}, headers=H)
    assert calls == ["on"]
    client.put("/api/config", json={"config": {"desktop": {"start_with_system": False}}}, headers=H)
    assert calls == ["on"]  # já estava desligado no sistema


def test_autostart_file_on_linux(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler, "WINDOWS", False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    scheduler.install_resident()
    text = scheduler._autostart_file().read_text(encoding="utf-8")
    assert "--inicio-sistema" in text and "Name=Monitor de notícias" in text
    scheduler.uninstall_resident()
    assert not scheduler._autostart_file().exists()


def test_second_copy_asks_the_open_one(monkeypatch):
    from werkzeug.serving import make_server

    shown = threading.Event()
    monkeypatch.setattr(web, "app_hooks", {"show": lambda data: shown.set()})
    server = make_server("127.0.0.1", 0, web.create_app("t"), threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert not instance.forward("show")  # ninguém aberto ainda
        instance.register(f"http://127.0.0.1:{server.server_port}/", "t")
        assert instance.forward("show")
        assert shown.wait(3)
        assert not instance.forward("collect")  # ação que o programa aberto não tem
    finally:
        server.shutdown()
        instance.unregister()
    assert not (config.data_dir() / instance.FILE).exists()


def test_background_interval(monkeypatch):
    desk = resident.Desktop("http://127.0.0.1:1/")
    cfg = config.load_config()
    cfg["alerts"].update(background=True, interval_minutes=60)
    monkeypatch.setattr(scheduler, "last_run_time", lambda: datetime.now().astimezone() - timedelta(minutes=30))
    assert not desk.background_due(cfg)
    monkeypatch.setattr(scheduler, "last_run_time", lambda: datetime.now().astimezone() - timedelta(minutes=61))
    assert desk.background_due(cfg)
    cfg["alerts"]["background"] = False
    assert not desk.background_due(cfg)


class _Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class _Window:
    def __init__(self, hidden):
        self.hidden = hidden
        self.calls = []
        self.events = type("E", (), {})()
        for name in ("closing", "minimized", "restored"):
            setattr(self.events, name, _Event())

    def __getattr__(self, name):  # hide, show, restore, destroy, evaluate_js
        return lambda *a: self.calls.append(name)


def test_window_close_goes_to_tray(monkeypatch):
    import sys
    import types

    from monitor import __main__ as main

    created = {}
    fake = types.SimpleNamespace(renderer="edgechromium")
    fake.create_window = lambda *a, hidden=False, **k: created.setdefault("w", _Window(hidden))
    fake.start = lambda func=None, window=None: func(window) if func else None
    monkeypatch.setitem(sys.modules, "webview", fake)
    desk = resident.Desktop("http://127.0.0.1:1/")
    monkeypatch.setattr(desk, "start_tray", lambda gtk_main_loop=False: True)
    monkeypatch.setattr(desk, "start_loop", lambda: None)
    monkeypatch.setattr(desk, "notify", lambda *a, **k: None)
    desk.icon = object()
    main._open_window("http://127.0.0.1:1/", desktop=desk, hidden=True)
    window = created["w"]
    assert window.hidden and desk.window is window
    closing = window.events.closing.handlers[0]
    assert closing() is False  # o X não fecha: pergunta e vai para a bandeja
    threading.Event().wait(0.3)
    assert "evaluate_js" in window.calls
    window.events.minimized.handlers[0]()
    desk.show_window()
    assert window.calls[-2:] == ["show", "restore"]
    desk.quit()
    assert closing() is True and window.calls[-1] == "destroy"
