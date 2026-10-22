from monitor import config, db, emailer, pipeline, preferences, web

GAMES = {"id": "jogos", "name": "Site de games", "category": "games"}
TECH = {"id": "tech", "name": "Site de tecnologia", "category": "tecnologia"}


def add(source, uid, title, summary=""):
    raw = {"uid": uid, "title": title, "url": f"https://x/{uid}", "summary": summary,
           "published": None, "cves": [], "cvss": None, "kev": False}
    pipeline.process_items(source, [raw], config.load_config(), None)
    return db.connect().execute("SELECT id FROM items WHERE uid=?", (uid,)).fetchone()[0]


def titles(view):
    return {i["title"] for i in db.query_items(view, limit=100)}


def test_default_sources_include_games():
    games = [s for s in config.load_sources() if s["category"] == "games"]
    assert len(games) >= 20
    assert any(s["language"] == "pt" and s["enabled"] for s in games)
    assert all(not config.validate_source(s) for s in games)


def test_existing_sources_file_gets_games_once(data_dir):
    (data_dir / "sources.yaml").write_text(
        "sources:\n  - {id: meu, name: Meu, type: rss, url: 'https://a/feed', category: tecnologia, enabled: true}\n",
        encoding="utf-8")
    sources = config.load_sources()
    assert sources[0]["id"] == "meu"
    assert any(s["category"] == "games" for s in sources)
    # Quem remove uma fonte de games não a vê voltar.
    config.delete_source("ign-brasil")
    assert config.get_source("ign-brasil") is None
    assert config.get_source("the-enemy") is not None


def test_games_stay_out_of_tech_views():
    add(TECH, "t1", "Zero-day in PHP exploited")
    add(GAMES, "g1", "Speedrunner finds zero-day exploit in Zelda")
    add(GAMES, "g2", "Novo patch do jogo chega amanhã")
    for view in ("criticos", "todos", "tecnologia", "paravoce", "minhas"):
        assert not any("Zelda" in t or "jogo" in t for t in titles(view)), view
    assert titles("games") == {"Speedrunner finds zero-day exploit in Zelda", "Novo patch do jogo chega amanhã"}
    assert "Zero-day in PHP exploited" not in titles("games")
    counts = db.counts()
    assert counts["games"] == 2 and counts["todos"] == 1 and counts["criticos"] == 1


def test_games_are_never_critical_and_highlights_work():
    exploit = add(GAMES, "g1", "Zero-day ransomware exploit RCE found in game")
    free = add(GAMES, "g2", "Jogo grátis da semana na Epic")
    assert db.get_item(exploit)["level"] == "normal"
    assert db.get_item(free)["level"] == "alto"
    assert titles("games_destaques") == {"Jogo grátis da semana na Epic"}


def test_following_games_via_config_rescores():
    client = web.create_app("t").test_client()
    h = {"X-Token": "t"}
    item_id = add(GAMES, "g1", "Hollow Knight Silksong ganha expansão")
    assert titles("games_meus") == set()
    cfg = config.load_config()
    cfg["games"]["following"] = ["silksong"]
    res = client.put("/api/config", json={"config": {"games": cfg["games"]}}, headers=h).json
    assert res["rescored"] >= 1
    item = db.get_item(item_id)
    assert item["matched_tech"] == "silksong" and item["level"] == "alto"
    assert titles("games_meus") == {"Hollow Knight Silksong ganha expansão"}


def test_likes_are_learned_per_area():
    for i in range(3):
        db.set_flag(add(GAMES, f"g{i}", f"Roguelike indie {i} com pixel art"), "liked", 1)
    for i in range(6):
        add(GAMES, f"f{i}", f"Futebol anual edição {i} ganha modo carreira")
    add(TECH, "t1", "Kubernetes release notes")
    preferences.refresh()
    assert preferences.status("games")["active"] is True
    assert preferences.status("tech")["active"] is False
    assert preferences.get_model("tech") is None
    new_game = add(GAMES, "g9", "Outro roguelike indie em pixel art")
    new_tech = add(TECH, "t9", "Roguelike indie pixel art tool for developers")
    assert db.get_item(new_game)["affinity"] > 60
    assert db.get_item(new_tech)["affinity"] == 50


def test_reset_preferences_only_for_one_area():
    client = web.create_app("t").test_client()
    h = {"X-Token": "t"}
    game = add(GAMES, "g1", "Jogo de corrida")
    tech = add(TECH, "t1", "Novo Python")
    db.set_flag(game, "liked", 1)
    db.set_flag(tech, "liked", 1)
    assert client.post("/api/preferences/reset", json={"area": "games"}, headers=h).json["likes"] == 0
    assert db.get_item(tech)["liked"] == 1 and db.get_item(game)["liked"] == 0


def test_games_email_is_separate(monkeypatch):
    add(TECH, "t1", "Zero-day in PHP exploited")
    add(GAMES, "g1", "Trailer novo de GTA 6")
    cfg = config.load_config()
    cfg["email"].update({"to_addresses": ["eu@exemplo.com"], "username": ""})

    subject, html, ids = emailer.build_digest(cfg)
    assert "GTA 6" not in html and len(ids) == 1
    subject, html, ids = emailer.build_digest(cfg, area="games")
    assert subject.startswith("Monitor de games") and "GTA 6" in html and "DESTAQUE" in html
    assert "PHP" not in html and len(ids) == 1

    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def ehlo(self): pass
        def starttls(self, context=None): pass
        def login(self, u, p): pass
        def send_message(self, msg): sent.append(msg)

    monkeypatch.setattr(emailer.smtplib, "SMTP", FakeSMTP)
    result = emailer.send_digest(cfg)
    assert result["emails"] == 2 and len(sent) == 2
    assert sent[1]["Subject"].startswith("Monitor de games")

    add(GAMES, "g2", "Outro trailer")
    cfg["games"]["email"] = False
    assert emailer.send_digest(cfg)["sent"] is False


def test_chat_and_alerts_ignore_games():
    add(GAMES, "g1", "Jogo grátis hoje")
    assert db.items_for_chat() == []
    assert db.items_for_alert("notified") == []


def test_reader_api_lists_games_view():
    client = web.create_app("t").test_client()
    h = {"X-Token": "t"}
    add(GAMES, "g1", "Nintendo Direct anunciado")
    res = client.get("/api/items?view=games_destaques", headers=h).json
    assert [i["title"] for i in res] == ["Nintendo Direct anunciado"]
    assert client.get("/api/counts", headers=h).json["games_destaques"] == 1
    assert client.get("/api/preferences?area=games", headers=h).json["area"] == "games"
    assert 'data-view="games_destaques"' in client.get("/").get_data(as_text=True)
