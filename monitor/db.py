"""Banco SQLite local com as notícias, as execuções e os erros de coleta."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    source_id TEXT NOT NULL,
    source_name TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    summary TEXT DEFAULT '',
    published TEXT,
    fetched_at TEXT NOT NULL,
    cves TEXT DEFAULT '',
    cvss REAL,
    kev INTEGER DEFAULT 0,
    score INTEGER DEFAULT 0,
    level TEXT DEFAULT 'normal',
    matched_tech TEXT DEFAULT '',
    reasons TEXT DEFAULT '',
    is_read INTEGER DEFAULT 0,
    starred INTEGER DEFAULT 0,
    liked INTEGER DEFAULT 0,
    affinity REAL DEFAULT 50,
    emailed INTEGER DEFAULT 0,
    article_html TEXT,
    run_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_items_published ON items(published);
CREATE INDEX IF NOT EXISTS idx_items_level ON items(level);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    sources_ok INTEGER DEFAULT 0,
    sources_failed INTEGER DEFAULT 0,
    new_items INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS fetch_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    source_id TEXT NOT NULL,
    source_name TEXT NOT NULL,
    url TEXT,
    error TEXT NOT NULL,
    at TEXT NOT NULL
);
"""

ITEM_FIELDS = [
    "uid", "source_id", "source_name", "category", "title", "url", "summary",
    "published", "fetched_at", "cves", "cvss", "kev", "score", "level",
    "matched_tech", "reasons", "run_id", "affinity",
]

_local = threading.local()


def now_iso() -> str:
    """Data/hora em UTC no formato do SQLite ("AAAA-MM-DD HH:MM:SS")."""
    return to_db_time(datetime.now(timezone.utc))


def to_db_time(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def db_path() -> Path:
    return config.data_dir() / "monitor.db"


def connect() -> sqlite3.Connection:
    """Uma conexão por thread, criada sob demanda."""
    path = str(db_path())
    conn = getattr(_local, "conn", None)
    if conn is None or getattr(_local, "path", None) != path:
        conn = sqlite3.connect(path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        _migrate(conn)
        _local.conn, _local.path = conn, path
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Acrescenta colunas novas em bancos criados por versões anteriores."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(items)")}
    for name, ddl in (("liked", "INTEGER DEFAULT 0"), ("affinity", "REAL DEFAULT 50")):
        if name not in columns:
            conn.execute(f"ALTER TABLE items ADD COLUMN {name} {ddl}")
    conn.commit()


def close() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


# --- Execuções e erros ----------------------------------------------------

def start_run() -> int:
    conn = connect()
    cur = conn.execute("INSERT INTO runs (started_at) VALUES (?)", (now_iso(),))
    conn.commit()
    return cur.lastrowid


def finish_run(run_id: int, ok: int, failed: int, new_items: int) -> None:
    conn = connect()
    conn.execute(
        "UPDATE runs SET finished_at=?, sources_ok=?, sources_failed=?, new_items=? WHERE id=?",
        (now_iso(), ok, failed, new_items, run_id),
    )
    conn.commit()


def last_run() -> dict | None:
    row = connect().execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def add_error(run_id: int | None, source: dict, error: str) -> None:
    conn = connect()
    conn.execute(
        "INSERT INTO fetch_errors (run_id, source_id, source_name, url, error, at) VALUES (?,?,?,?,?,?)",
        (run_id, source["id"], source["name"], source.get("url"), error, now_iso()),
    )
    conn.commit()


def errors_for_run(run_id: int) -> list[dict]:
    rows = connect().execute(
        "SELECT * FROM fetch_errors WHERE run_id=? ORDER BY source_name", (run_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def error_history(limit: int = 200) -> list[dict]:
    rows = connect().execute(
        "SELECT * FROM fetch_errors ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def failure_streaks() -> dict[str, int]:
    """Quantas execuções seguidas cada fonte falhou (contando da mais recente)."""
    conn = connect()
    runs = [r["id"] for r in conn.execute("SELECT id FROM runs ORDER BY id DESC LIMIT 30")]
    failed_by_run: dict[int, set[str]] = {}
    for row in conn.execute("SELECT run_id, source_id FROM fetch_errors WHERE run_id IS NOT NULL"):
        failed_by_run.setdefault(row["run_id"], set()).add(row["source_id"])
    streaks: dict[str, int] = {}
    for source_id in {s for ids in failed_by_run.values() for s in ids}:
        n = 0
        for run_id in runs:
            if source_id in failed_by_run.get(run_id, set()):
                n += 1
            else:
                break
        if n:
            streaks[source_id] = n
    return streaks


# --- Notícias -------------------------------------------------------------

def insert_item(item: dict) -> bool:
    """Insere a notícia se ainda não existir. Devolve True quando é nova."""
    conn = connect()
    values = [item.get(f) for f in ITEM_FIELDS]
    cur = conn.execute(
        f"INSERT OR IGNORE INTO items ({','.join(ITEM_FIELDS)}) VALUES ({','.join('?' * len(ITEM_FIELDS))})",
        values,
    )
    conn.commit()
    return cur.rowcount > 0


def query_items(
    view: str = "criticos",
    search: str = "",
    unread_only: bool = False,
    source_id: str = "",
    days: int = 0,
    limit: int = 300,
    sort: str = "",
) -> list[dict]:
    where, params = [], []
    if view == "criticos":
        where.append("level IN ('critico','alto')")
    elif view == "seguranca":
        where.append("category='seguranca'")
    elif view == "tecnologia":
        where.append("category='tecnologia'")
    elif view == "favoritos":
        where.append("starred=1")
    elif view == "minhas":
        where.append("matched_tech <> ''")
    elif view == "paravoce":
        where.append("liked >= 0")
        sort = "afinidade"
    if unread_only:
        where.append("is_read=0")
    if source_id:
        where.append("source_id=?")
        params.append(source_id)
    if days:
        where.append("COALESCE(published, fetched_at) >= datetime('now', ?)")
        params.append(f"-{int(days)} days")
    if search:
        where.append("(title LIKE ? OR summary LIKE ? OR cves LIKE ?)")
        params += [f"%{search}%"] * 3
    sql = "SELECT id, uid, source_id, source_name, category, title, url, summary, published, fetched_at, " \
          "cves, cvss, kev, score, level, matched_tech, reasons, is_read, starred, liked, affinity, " \
          "article_html IS NOT NULL AS has_article FROM items"
    if where:
        sql += " WHERE " + " AND ".join(where)
    if sort == "afinidade":
        sql += " ORDER BY affinity DESC, score DESC, COALESCE(published, fetched_at) DESC"
    elif view == "criticos":
        sql += " ORDER BY score DESC, affinity DESC, COALESCE(published, fetched_at) DESC"
    else:
        sql += " ORDER BY COALESCE(published, fetched_at) DESC"
    sql += " LIMIT ?"
    params.append(limit)
    return [dict(r) for r in connect().execute(sql, params).fetchall()]


def get_item(item_id: int) -> dict | None:
    row = connect().execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    return dict(row) if row else None


def related_by_cve(item: dict) -> list[dict]:
    """Outras notícias que citam os mesmos CVEs."""
    cves = [c for c in (item.get("cves") or "").split(",") if c]
    if not cves:
        return []
    clauses = " OR ".join("(',' || cves || ',') LIKE ?" for _ in cves)
    rows = connect().execute(
        f"SELECT id, title, source_name, url FROM items WHERE id<>? AND ({clauses}) "
        "ORDER BY COALESCE(published, fetched_at) DESC LIMIT 10",
        [item["id"], *[f"%,{c},%" for c in cves]],
    ).fetchall()
    return [dict(r) for r in rows]


def set_flag(item_id: int, flag: str, value) -> None:
    """is_read/starred: 0 ou 1. liked: 1 (gostei), -1 (não curti) ou 0."""
    if flag not in ("is_read", "starred", "liked"):
        raise ValueError(flag)
    value = int(value)
    if value not in ((-1, 0, 1) if flag == "liked" else (0, 1)):
        raise ValueError(value)
    conn = connect()
    conn.execute(f"UPDATE items SET {flag}=? WHERE id=?", (value, item_id))
    conn.commit()


def mark_all_read(view: str) -> int:
    ids = [i["id"] for i in query_items(view=view, unread_only=True, limit=100000)]
    conn = connect()
    conn.executemany("UPDATE items SET is_read=1 WHERE id=?", [(i,) for i in ids])
    conn.commit()
    return len(ids)


def save_article(item_id: int, html: str) -> None:
    conn = connect()
    conn.execute("UPDATE items SET article_html=? WHERE id=?", (html, item_id))
    conn.commit()


def counts() -> dict:
    row = connect().execute(
        """SELECT
            SUM(level IN ('critico','alto') AND is_read=0) AS criticos,
            SUM(category='seguranca' AND is_read=0) AS seguranca,
            SUM(category='tecnologia' AND is_read=0) AS tecnologia,
            SUM(matched_tech <> '' AND is_read=0) AS minhas,
            SUM(starred=1) AS favoritos,
            SUM(liked >= 0 AND is_read=0 AND affinity >= 65) AS paravoce,
            SUM(is_read=0) AS todos
        FROM items"""
    ).fetchone()
    return {k: (row[k] or 0) for k in row.keys()}


def items_for_email(only_new: bool = True, limit_per_section: int = 15) -> dict[str, list[dict]]:
    base = "SELECT * FROM items WHERE 1=1" + (" AND emailed=0" if only_new else "")
    conn = connect()
    sections = {
        "criticos": conn.execute(
            base + " AND level IN ('critico','alto') ORDER BY score DESC LIMIT ?", (limit_per_section,)
        ).fetchall(),
        "seguranca": conn.execute(
            base + " AND category='seguranca' AND level='normal' "
            "ORDER BY COALESCE(published, fetched_at) DESC LIMIT ?", (limit_per_section,)
        ).fetchall(),
        "tecnologia": conn.execute(
            base + " AND category='tecnologia' AND level='normal' "
            "ORDER BY COALESCE(published, fetched_at) DESC LIMIT ?", (limit_per_section,)
        ).fetchall(),
    }
    return {k: [dict(r) for r in v] for k, v in sections.items()}


def mark_emailed(ids: list[int]) -> None:
    conn = connect()
    conn.executemany("UPDATE items SET emailed=1 WHERE id=?", [(i,) for i in ids])
    conn.commit()
