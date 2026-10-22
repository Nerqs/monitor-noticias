from monitor import config, db, pipeline, preferences, web

SOURCE = {"id": "src", "name": "Fonte", "category": "tecnologia"}


def add(uid, title, summary=""):
    raw = {"uid": uid, "title": title, "url": f"https://x/{uid}", "summary": summary,
           "published": None, "cves": [], "cvss": None, "kev": False}
    pipeline.process_items(SOURCE, [raw], config.load_config(), None)
    return db.connect().execute("SELECT id FROM items WHERE uid=?", (uid,)).fetchone()[0]


def test_inactive_until_min_likes():
    ids = [add(str(i), f"Rust compiler release {i}") for i in range(3)]
    db.set_flag(ids[0], "liked", 1)
    assert preferences.refresh() is None
    assert preferences.status()["active"] is False


def test_liked_topics_rank_higher():
    liked = [add(f"r{i}", f"Rust {v} released with faster compiler", "Rust language news")
             for i, v in enumerate(["1.90", "1.91", "1.92"])]
    for i in range(6):
        add(f"p{i}", f"Smartphone launch event number {i}", "Battery and camera upgrades")
    for item_id in liked:
        db.set_flag(item_id, "liked", 1)
    preferences.refresh()

    new_rust = add("new-rust", "Rust 2.0 roadmap announced", "Big changes for the Rust compiler")
    new_phone = add("new-phone", "Another smartphone with bigger battery", "Camera upgrades")
    rust = db.get_item(new_rust)["affinity"]
    phone = db.get_item(new_phone)["affinity"]
    assert rust > 60 > phone

    ranked = db.query_items("paravoce", limit=50)
    top_ids = [r["id"] for r in ranked[:4]]
    assert new_rust in top_ids and new_phone not in top_ids
    assert "rust" in preferences.explain(db.get_item(new_rust), preferences.get_model())


def test_dislikes_push_down():
    for i in range(3):
        db.set_flag(add(f"l{i}", f"Kubernetes operator tutorial {i}"), "liked", 1)
    for i in range(3):
        db.set_flag(add(f"d{i}", f"Celebrity gadget gossip {i}"), "liked", -1)
    preferences.refresh()
    gossip = add("g", "More celebrity gossip about gadgets")
    assert db.get_item(gossip)["affinity"] < 50


def test_like_via_api_and_reset():
    client = web.create_app("t").test_client()
    h = {"X-Token": "t"}
    ids = [add(str(i), f"Python {i} news") for i in range(3)]
    for item_id in ids:
        res = client.post(f"/api/items/{item_id}/flag", json={"flag": "liked", "value": 1}, headers=h).json
    assert res["preferences"]["active"] is True
    assert client.post(f"/api/items/{ids[0]}/flag", json={"flag": "liked", "value": 5}, headers=h).status_code == 400
    assert client.post("/api/preferences/reset", headers=h).json["likes"] == 0
