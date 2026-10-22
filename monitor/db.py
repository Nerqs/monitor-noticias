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

-- Resultado de cada fonte em cada coleta (saúde das fontes).
CREATE TABLE IF NOT EXISTS source_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    source_id TEXT NOT NULL,
    ok INTEGER NOT NULL,
    not_modified INTEGER DEFAULT 0,
    duration_ms INTEGER,
    items INTEGER DEFAULT 0,
    new_items INTEGER DEFAULT 0,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_source_runs_source ON source_runs(source_id, id);

-- ETag/Last-Modified de cada endereço, para baixar só o que mudou.
CREATE TABLE IF NOT EXISTS http_cache (
    url TEXT PRIMARY KEY,
    etag TEXT,
    last_modified TEXT,
    at TEXT NOT NULL
);

-- Detalhes de CVEs consultados no NVD e no EPSS (cache).
CREATE TABLE IF NOT EXISTS cve_info (
    cve TEXT PRIMARY KEY,
    nvd TEXT,
    nvd_at TEXT,
    epss REAL,
    epss_pct REAL,
    epss_at TEXT
);

-- Pequenos valores do programa (última faxina, autoauditoria...).
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    title, summary, cves, source_name,
    content='items', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS items_fts_ai AFTER INSERT ON items BEGIN
    INSERT INTO items_fts(rowid, title, summary, cves, source_name)
    VALUES (new.id, new.title, new.summary, new.cves, new.source_name);
END;
CREATE TRIGGER IF NOT EXISTS items_fts_ad AFTER DELETE ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, title, summary, cves, source_name)
    VALUES ('delete', old.id, old.title, old.summary, old.cves, old.source_name);
END;
CREATE TRIGGER IF NOT EXISTS items_fts_au AFTER UPDATE OF title, summary, cves, source_name ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, title, summary, cves, source_name)
    VALUES ('delete', old.id, old.title, old.summary, old.cves, old.source_name);
    INSERT INTO items_fts(rowid, title, summary, cves, source_name)
    VALUES (new.id, new.title, new.summary, new.cves, new.source_name);
END;
"""

ITEM_FIELDS = [
    "uid", "source_id", "source_name", "category", "title", "url", "summary",
    "published", "fetched_at", "cves", "cvss", "kev", "score", "level",
    "matched_tech", "reasons", "run_id", "affinity", "base_score", "base_reasons", "muted",
]

NEW_ITEM_COLUMNS = (
    ("liked", "INTEGER DEFAULT 0"),
    ("affinity", "REAL DEFAULT 50"),
    ("base_score", "INTEGER"),          # nota só do conteúdo, antes de filtros e enriquecimento
    ("base_reasons", "TEXT DEFAULT ''"),
    ("epss", "REAL"),                   # maior probabilidade de exploração entre os CVEs citados
    ("epss_pct", "REAL"),
    ("version_verdict", "TEXT DEFAULT ''"),  # afeta | nao_afeta | ''
    ("version_note", "TEXT DEFAULT ''"),
    ("muted", "INTEGER DEFAULT 0"),
    ("enriched", "INTEGER DEFAULT 0"),
    ("chatted", "INTEGER DEFAULT 0"),
    ("notified", "INTEGER DEFAULT 0"),
)

_fts_available: bool | None = None

_local = threading.local()
# Várias requisições abrem conexão ao mesmo tempo quando o programa inicia; a
# migração de um banco antigo roda uma thread por vez, senão duas tentam criar
# a mesma coluna ("duplicate column name").
_migrate_lock = threading.Lock()


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
        with _migrate_lock:
            conn.executescript(SCHEMA)
            _migrate(conn)
        _local.conn, _local.path = conn, path
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Acrescenta colunas novas em bancos criados por versões anteriores."""
    global _fts_available
    columns = {row[1] for row in conn.execute("PRAGMA table_info(items)")}
    for name, ddl in NEW_ITEM_COLUMNS:
        if name not in columns:
            try:
                conn.execute(f"ALTER TABLE items ADD COLUMN {name} {ddl}")
            except sqlite3.OperationalError as exc:  # outro processo já criou
                if "duplicate column" not in str(exc):
                    raise
    conn.execute("UPDATE items SET base_score=score, base_reasons=reasons WHERE base_score IS NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_items_source ON items(source_id, fetched_at)")
    try:
        existed = conn.execute("SELECT 1 FROM sqlite_master WHERE name='items_fts'").fetchone()
        conn.executescript(FTS_SCHEMA)
        if not existed:
            conn.execute("INSERT INTO items_fts(items_fts) VALUES ('rebuild')")
        _fts_available = True
    except sqlite3.OperationalError:  # SQLite sem FTS5: a busca usa LIKE
        _fts_available = False
    conn.commit()


def fts_available() -> bool:
    connect()
    return bool(_fts_available)


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


def fts_query(text: str) -> str:
    """Transforma o que a pessoa digitou numa consulta FTS5 segura.

    Cada palavra vira um prefixo entre aspas ("php" acha "phpmailer"); palavras
    precedidas de "-" são excluídas.
    """
    include, exclude = [], []
    for word in text.replace('"', " ").split():
        negative = word.startswith("-") and len(word) > 1
        word = word.lstrip("-")
        if not word:
            continue
        (exclude if negative else include).append(f'"{word}"*')
    if not include:
        return ""
    query = " AND ".join(include)
    for word in exclude:
        query += f" NOT {word}"
    return query


GAME_VIEWS = {"games", "games_destaques", "games_meus", "games_paravoce", "games_favoritos"}


def query_items(
    view: str = "criticos",
    search: str = "",
    unread_only: bool = False,
    source_id: str = "",
    days: int = 0,
    limit: int = 300,
    sort: str = "",
    level: str = "",
) -> list[dict]:
    where, params = [], []
    if view == "silenciadas":
        where.append("muted=1")
    else:
        where.append("muted=0")
        # Games têm uma área própria: não aparecem nas listas de tecnologia e vice-versa.
        where.append("category='games'" if view in GAME_VIEWS else "category<>'games'")
    if view == "criticos":
        where.append("level IN ('critico','alto')")
    elif view == "seguranca":
        where.append("category='seguranca'")
    elif view == "tecnologia":
        where.append("category='tecnologia'")
    elif view in ("favoritos", "games_favoritos"):
        where.append("starred=1")
    elif view == "minhas":
        where.append("(matched_tech <> '' OR version_verdict='afeta')")
    elif view == "games_destaques":
        where.append("level IN ('critico','alto')")
    elif view == "games_meus":
        where.append("matched_tech <> ''")
    elif view in ("paravoce", "games_paravoce"):
        where.append("liked >= 0")
        sort = "afinidade"
    if unread_only:
        where.append("is_read=0")
    if source_id:
        where.append("source_id=?")
        params.append(source_id)
    if level in ("critico", "alto", "normal"):
        where.append("level=?")
        params.append(level)
    if days:
        where.append("COALESCE(published, fetched_at) >= datetime('now', ?)")
        params.append(f"-{int(days)} days")
    source = "items"
    ranked = False
    if search:
        match = fts_query(search) if fts_available() else ""
        if match:
            source = ("items JOIN (SELECT rowid AS fid, bm25(items_fts) AS rank FROM items_fts "
                      "WHERE items_fts MATCH ?) f ON f.fid = items.id")
            params.insert(0, match)
            ranked = True
        else:
            where.append("(title LIKE ? OR summary LIKE ? OR cves LIKE ?)")
            params += [f"%{search}%"] * 3
    sql = "SELECT id, uid, source_id, source_name, category, title, url, summary, published, fetched_at, " \
          "cves, cvss, kev, score, level, matched_tech, reasons, is_read, starred, liked, affinity, " \
          "epss, epss_pct, version_verdict, version_note, muted, " \
          f"article_html IS NOT NULL AS has_article FROM {source} WHERE " + " AND ".join(where)
    if sort == "afinidade":
        sql += " ORDER BY affinity DESC, score DESC, COALESCE(published, fetched_at) DESC"
    elif sort == "relevancia" and ranked:
        sql += " ORDER BY f.rank"
    elif sort == "data":
        sql += " ORDER BY COALESCE(published, fetched_at) DESC"
    elif view in ("criticos", "games_destaques"):
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
            SUM(level IN ('critico','alto') AND is_read=0 AND category<>'games') AS criticos,
            SUM(category='seguranca' AND is_read=0) AS seguranca,
            SUM(category='tecnologia' AND is_read=0) AS tecnologia,
            SUM((matched_tech <> '' OR version_verdict='afeta') AND is_read=0 AND category<>'games') AS minhas,
            SUM(starred=1 AND category<>'games') AS favoritos,
            SUM(liked >= 0 AND is_read=0 AND affinity >= 65 AND category<>'games') AS paravoce,
            SUM(is_read=0 AND category<>'games') AS todos,
            SUM(category='games' AND level IN ('critico','alto') AND is_read=0) AS games_destaques,
            SUM(category='games' AND matched_tech <> '' AND is_read=0) AS games_meus,
            SUM(category='games' AND liked >= 0 AND is_read=0 AND affinity >= 65) AS games_paravoce,
            SUM(category='games' AND starred=1) AS games_favoritos,
            SUM(category='games' AND is_read=0) AS games
        FROM items WHERE muted=0"""
    ).fetchone()
    return {k: (row[k] or 0) for k in row.keys()}


def items_for_email(only_new: bool = True, limit_per_section: int = 15, area: str = "tech") -> dict[str, list[dict]]:
    """Seções do resumo por e-mail. ``area`` "tech" (segurança e tecnologia) ou "games"."""
    base = "SELECT * FROM items WHERE muted=0" + (" AND emailed=0" if only_new else "")
    recent = " ORDER BY COALESCE(published, fetched_at) DESC LIMIT ?"
    conn = connect()
    if area == "games":
        base += " AND category='games'"
        sections = {
            "games_destaques": conn.execute(
                base + " AND level IN ('critico','alto') ORDER BY score DESC LIMIT ?", (limit_per_section,)
            ).fetchall(),
            "games": conn.execute(base + " AND level='normal'" + recent, (limit_per_section,)).fetchall(),
        }
    else:
        base += " AND category<>'games'"
        sections = {
            "criticos": conn.execute(
                base + " AND level IN ('critico','alto') ORDER BY score DESC LIMIT ?", (limit_per_section,)
            ).fetchall(),
            "seguranca": conn.execute(base + " AND category='seguranca' AND level='normal'" + recent,
                                      (limit_per_section,)).fetchall(),
            "tecnologia": conn.execute(base + " AND category='tecnologia' AND level='normal'" + recent,
                                       (limit_per_section,)).fetchall(),
        }
    return {k: [dict(r) for r in v] for k, v in sections.items()}


def mark_emailed(ids: list[int]) -> None:
    conn = connect()
    conn.executemany("UPDATE items SET emailed=1 WHERE id=?", [(i,) for i in ids])
    conn.commit()


# --- Valores do programa ----------------------------------------------------

def get_meta(key: str, default: str | None = None) -> str | None:
    row = connect().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(key: str, value: str) -> None:
    conn = connect()
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, value))
    conn.commit()


# --- Cache HTTP (ETag / Last-Modified) --------------------------------------

class HttpCache:
    """Guarda os validadores de cada endereço entre uma coleta e outra."""

    def conditional_headers(self, url: str) -> dict:
        row = connect().execute("SELECT etag, last_modified FROM http_cache WHERE url=?", (url,)).fetchone()
        headers = {}
        if row and row["etag"]:
            headers["If-None-Match"] = row["etag"]
        if row and row["last_modified"]:
            headers["If-Modified-Since"] = row["last_modified"]
        return headers

    def store(self, url: str, etag: str | None, last_modified: str | None) -> None:
        conn = connect()
        if not etag and not last_modified:
            conn.execute("DELETE FROM http_cache WHERE url=?", (url,))
        else:
            conn.execute(
                "INSERT INTO http_cache (url, etag, last_modified, at) VALUES (?,?,?,?) "
                "ON CONFLICT(url) DO UPDATE SET etag=excluded.etag, last_modified=excluded.last_modified, at=excluded.at",
                (url, etag, last_modified, now_iso()),
            )
        conn.commit()

    def forget(self, url: str) -> None:
        conn = connect()
        conn.execute("DELETE FROM http_cache WHERE url=?", (url,))
        conn.commit()


# --- Saúde das fontes -------------------------------------------------------

def add_source_run(run_id: int | None, source_id: str, ok: bool, duration_ms: int,
                   items: int = 0, new_items: int = 0, not_modified: bool = False) -> None:
    conn = connect()
    conn.execute(
        "INSERT INTO source_runs (run_id, source_id, ok, not_modified, duration_ms, items, new_items, at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (run_id, source_id, int(ok), int(not_modified), duration_ms, items, new_items, now_iso()),
    )
    conn.commit()


def source_health(window: int = 30) -> dict[str, dict]:
    """Estatísticas das últimas ``window`` coletas de cada fonte."""
    conn = connect()
    health: dict[str, dict] = {}
    rows = conn.execute(
        """SELECT source_id, ok, duration_ms, new_items FROM (
               SELECT *, ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY id DESC) AS n FROM source_runs
           ) WHERE n <= ?""", (window,)
    ).fetchall()
    for row in rows:
        h = health.setdefault(row["source_id"], {"runs": 0, "ok": 0, "durations": [], "new_items": 0})
        h["runs"] += 1
        h["ok"] += row["ok"]
        if row["ok"] and row["duration_ms"] is not None:
            h["durations"].append(row["duration_ms"])
        h["new_items"] += row["new_items"] or 0
    stats = conn.execute(
        """SELECT source_id, MAX(fetched_at) AS last_new,
                  SUM(fetched_at >= datetime('now', '-30 days')) AS last30,
                  SUM(liked = 1) AS likes, SUM(liked = -1) AS dislikes
           FROM items GROUP BY source_id"""
    ).fetchall()
    for row in stats:
        h = health.setdefault(row["source_id"], {"runs": 0, "ok": 0, "durations": [], "new_items": 0})
        h.update(last_new=row["last_new"], per_day=round((row["last30"] or 0) / 30, 1),
                 likes=row["likes"] or 0, dislikes=row["dislikes"] or 0)
    for h in health.values():
        durations = sorted(h.pop("durations"))
        h["success_rate"] = round(100 * h["ok"] / h["runs"]) if h["runs"] else None
        h["median_ms"] = durations[len(durations) // 2] if durations else None
        h.setdefault("last_new", None)
        h.setdefault("per_day", 0)
        h.setdefault("likes", 0)
        h.setdefault("dislikes", 0)
    return health


def last_success(source_id: str) -> str | None:
    row = connect().execute(
        "SELECT MAX(at) AS at FROM source_runs WHERE source_id=? AND ok=1", (source_id,)
    ).fetchone()
    return row["at"] if row else None


def first_seen(source_id: str) -> str | None:
    row = connect().execute("SELECT MIN(at) AS at FROM source_runs WHERE source_id=?", (source_id,)).fetchone()
    return row["at"] if row else None


# --- CVEs (NVD / EPSS) ------------------------------------------------------

def get_cve_info(cve: str) -> dict | None:
    row = connect().execute("SELECT * FROM cve_info WHERE cve=?", (cve,)).fetchone()
    return dict(row) if row else None


def save_cve_nvd(cve: str, data_json: str) -> None:
    conn = connect()
    conn.execute(
        "INSERT INTO cve_info (cve, nvd, nvd_at) VALUES (?,?,?) "
        "ON CONFLICT(cve) DO UPDATE SET nvd=excluded.nvd, nvd_at=excluded.nvd_at",
        (cve, data_json, now_iso()),
    )
    conn.commit()


def save_cve_epss(values: dict[str, tuple[float, float]]) -> None:
    conn = connect()
    at = now_iso()
    conn.executemany(
        "INSERT INTO cve_info (cve, epss, epss_pct, epss_at) VALUES (?,?,?,?) "
        "ON CONFLICT(cve) DO UPDATE SET epss=excluded.epss, epss_pct=excluded.epss_pct, epss_at=excluded.epss_at",
        [(cve, e, p, at) for cve, (e, p) in values.items()],
    )
    conn.commit()


def items_with_cve(cve: str) -> list[dict]:
    rows = connect().execute(
        "SELECT id, title, source_name, url, published, fetched_at, kev, level FROM items "
        "WHERE (',' || cves || ',') LIKE ? ORDER BY COALESCE(published, fetched_at)",
        (f"%,{cve.upper()},%",),
    ).fetchall()
    return [dict(r) for r in rows]


def items_to_enrich(limit: int = 300) -> list[dict]:
    rows = connect().execute(
        "SELECT * FROM items WHERE enriched=0 ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def update_item(item_id: int, **fields) -> None:
    allowed = {"score", "level", "reasons", "epss", "epss_pct", "version_verdict", "version_note", "muted",
               "enriched", "chatted", "notified", "cvss"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    conn = connect()
    conn.execute(f"UPDATE items SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", [*fields.values(), item_id])
    conn.commit()


def all_items_for_rescore() -> list[dict]:
    rows = connect().execute(
        "SELECT id, source_id, category, title, summary, cves, base_score, base_reasons, matched_tech, epss, epss_pct, "
        "version_verdict, version_note, muted, score, level, reasons FROM items"
    ).fetchall()
    return [dict(r) for r in rows]


def mark_flag_many(ids: list[int], flag: str) -> None:
    if flag not in ("chatted", "notified"):
        raise ValueError(flag)
    conn = connect()
    conn.executemany(f"UPDATE items SET {flag}=1 WHERE id=?", [(i,) for i in ids])
    conn.commit()


def items_for_alert(flag: str, limit: int = 10) -> list[dict]:
    """Notícias graves ainda não avisadas (chat ou notificação da área de trabalho)."""
    if flag not in ("chatted", "notified"):
        raise ValueError(flag)
    rows = connect().execute(
        f"SELECT * FROM items WHERE muted=0 AND {flag}=0 AND level='critico' AND category<>'games' "
        "AND fetched_at >= datetime('now', '-2 days') ORDER BY score DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def items_to_notify(limit: int = 300) -> list[dict]:
    """Notícias novas (de qualquer nível) que ainda não viraram notificação na área de trabalho."""
    rows = connect().execute(
        "SELECT id, title, level, category, source_name, reasons, matched_tech, version_verdict FROM items "
        "WHERE muted=0 AND notified=0 AND fetched_at >= datetime('now', '-2 days') ORDER BY score DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_all_notified() -> None:
    """Ao abrir o programa, o que já estava no banco não vira notificação."""
    conn = connect()
    conn.execute("UPDATE items SET notified=1 WHERE notified=0")
    conn.commit()


def items_for_chat(limit: int = 10) -> list[dict]:
    rows = connect().execute(
        "SELECT * FROM items WHERE muted=0 AND chatted=0 AND level IN ('critico','alto') AND category<>'games' "
        "AND fetched_at >= datetime('now', '-2 days') ORDER BY score DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# --- Faxina -----------------------------------------------------------------

def cleanup(keep_days: int = 90, article_days: int = 30) -> dict:
    """Apaga notícias antigas (menos favoritas e as que você avaliou) e históricos velhos."""
    conn = connect()
    removed = conn.execute(
        "DELETE FROM items WHERE starred=0 AND liked=0 AND fetched_at < datetime('now', ?)",
        (f"-{int(keep_days)} days",),
    ).rowcount
    articles = conn.execute(
        "UPDATE items SET article_html=NULL WHERE article_html IS NOT NULL AND starred=0 "
        "AND fetched_at < datetime('now', ?)", (f"-{int(article_days)} days",),
    ).rowcount
    old = f"-{max(int(keep_days), 30)} days"
    conn.execute("DELETE FROM fetch_errors WHERE at < datetime('now', ?)", (old,))
    conn.execute("DELETE FROM source_runs WHERE at < datetime('now', ?)", (old,))
    conn.execute("DELETE FROM runs WHERE started_at < datetime('now', ?) AND id < (SELECT MAX(id) FROM runs)", (old,))
    conn.execute("DELETE FROM cve_info WHERE COALESCE(nvd_at, epss_at) < datetime('now', ?)", (old,))
    conn.commit()
    return {"items": removed, "articles": articles}


def vacuum() -> None:
    conn = connect()
    conn.commit()
    conn.execute("VACUUM")


def integrity_ok() -> bool:
    row = connect().execute("PRAGMA quick_check").fetchone()
    return row[0] == "ok"


def stats() -> dict:
    conn = connect()
    row = conn.execute("SELECT COUNT(*) AS n, SUM(starred) AS fav, SUM(liked<>0) AS rated FROM items").fetchone()
    path = db_path()
    size = path.stat().st_size if path.exists() else 0
    wal = path.with_name(path.name + "-wal")
    size += wal.stat().st_size if wal.exists() else 0
    return {"items": row["n"] or 0, "starred": row["fav"] or 0, "rated": row["rated"] or 0, "bytes": size}
