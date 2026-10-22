"""Backup e importação: fontes, configurações, curtidas e favoritos; listas OPML.

Os segredos (senha do e-mail, tokens) ficam no cofre do sistema e nunca entram
no arquivo de backup. Chaves de API das configurações também são omitidas.
"""

from __future__ import annotations

import copy
import json
import xml.etree.ElementTree as ET
from datetime import datetime

from . import __version__, config, db, preferences

SECRET_KEYS = {("collection", "nvd_api_key"), ("collection", "github_token")}


class BackupError(Exception):
    pass


def export_data() -> dict:
    cfg = copy.deepcopy(config.load_config())
    for section, key in SECRET_KEYS:
        cfg.get(section, {}).pop(key, None)
    rows = db.connect().execute(
        "SELECT uid, source_id, source_name, category, title, url, summary, published, fetched_at, cves, "
        "liked, starred, is_read FROM items WHERE liked<>0 OR starred=1"
    ).fetchall()
    return {
        "formato": "monitor-noticias-backup",
        "versao": 1,
        "programa": __version__,
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "config": cfg,
        "sources": config.load_sources(),
        "items": [dict(r) for r in rows],
    }


def save_backup_file() -> str:
    folder = config.data_dir() / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"monitor-backup-{datetime.now():%Y-%m-%d-%H%M}.json"
    path.write_text(json.dumps(export_data(), ensure_ascii=False, indent=1), encoding="utf-8")
    return str(path)


def import_data(data: dict, replace_config: bool = True) -> dict:
    if not isinstance(data, dict) or data.get("formato") != "monitor-noticias-backup":
        raise BackupError("Este arquivo não é um backup do Monitor de notícias.")
    result = {"sources": 0, "items": 0, "config": False}

    existing = config.load_sources()
    urls = {s.get("url") for s in existing}
    for source in data.get("sources") or []:
        if not isinstance(source, dict) or source.get("url") in urls or config.validate_source(source):
            continue
        config.upsert_source({k: v for k, v in source.items() if k != "id"} | {"id": source.get("id")})
        urls.add(source.get("url"))
        result["sources"] += 1

    if replace_config and isinstance(data.get("config"), dict):
        current = config.load_config()
        for section, values in data["config"].items():
            if isinstance(values, dict) and isinstance(current.get(section), dict):
                for key, value in values.items():
                    if (section, key) not in SECRET_KEYS:
                        current[section][key] = value
        config.save_config(current)
        result["config"] = True

    conn = db.connect()
    for item in data.get("items") or []:
        if not isinstance(item, dict) or not item.get("uid") or not item.get("title") or not item.get("url"):
            continue
        liked = item.get("liked") if item.get("liked") in (-1, 0, 1) else 0
        starred = 1 if item.get("starred") else 0
        cur = conn.execute("UPDATE items SET liked=?, starred=? WHERE uid=?", (liked, starred, item["uid"]))
        if cur.rowcount == 0:
            conn.execute(
                "INSERT INTO items (uid, source_id, source_name, category, title, url, summary, published, "
                "fetched_at, cves, liked, starred, is_read, enriched, base_score, base_reasons) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,1,0,'')",
                (item["uid"], item.get("source_id") or "importado", item.get("source_name") or "Importado",
                 item.get("category") if item.get("category") in config.CATEGORIES else "tecnologia",
                 str(item["title"])[:500], str(item["url"])[:2000], str(item.get("summary") or "")[:2000],
                 item.get("published"), item.get("fetched_at") or db.now_iso(), item.get("cves") or "",
                 liked, starred),
            )
        result["items"] += 1
    conn.commit()
    preferences.refresh()
    return result


# --- OPML (lista de feeds de outros leitores de RSS) -----------------------------

def import_opml(text: str, category: str = "tecnologia") -> dict:
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    if "<!ENTITY" in text.upper():
        raise BackupError("O arquivo declara entidades XML; importação recusada por segurança.")
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError as exc:
        raise BackupError(f"OPML inválido: {exc}")
    urls = {s.get("url") for s in config.load_sources()}
    added, skipped = 0, 0
    for outline in root.iter("outline"):
        url = (outline.get("xmlUrl") or "").strip()
        if not url:
            continue
        if url in urls or not url.startswith(("http://", "https://")):
            skipped += 1
            continue
        name = (outline.get("title") or outline.get("text") or url).strip()[:120]
        config.upsert_source({"name": name, "type": "rss", "url": url, "category": category,
                              "language": "en", "enabled": True})
        urls.add(url)
        added += 1
    return {"added": added, "skipped": skipped}


def export_opml() -> str:
    root = ET.Element("opml", version="2.0")
    head = ET.SubElement(root, "head")
    ET.SubElement(head, "title").text = "Monitor de notícias"
    body = ET.SubElement(root, "body")
    for source in config.load_sources():
        if source.get("type") != "rss":
            continue
        ET.SubElement(body, "outline", type="rss", text=source["name"], title=source["name"],
                      xmlUrl=source["url"])
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")
