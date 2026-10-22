"""Aprende com as notícias marcadas como "Gostei" / "Não curti".

Tudo roda localmente, sem serviços externos. O modelo é um Naive Bayes simples
sobre palavras do título e do resumo, a fonte e as tecnologias citadas:

* cada termo recebe um peso = quanto ele aparece mais nas notícias curtidas
  do que no conjunto de todas as notícias, menos o mesmo cálculo para as
  "não curti";
* a afinidade de uma notícia é a média dos pesos dos seus termos, convertida
  para uma escala de 0 a 100 (50 = neutra).

Com menos de ``MIN_LIKES`` curtidas o modelo fica inativo e tudo vale 50.

Tecnologia e games aprendem separados: curtir notícias de games não muda a
ordem das notícias de tecnologia (e vice-versa).
"""

from __future__ import annotations

import math
import re
import threading
from collections import Counter

from . import config, db

MIN_LIKES = 3
ALPHA = 1.0  # suavização de Laplace

STOPWORDS = set("""
a o as os um uma uns umas de do da dos das em no na nos nas por para pelo pela com sem sob sobre e ou que
se mais menos muito como foi ser sao são está esta este isso essa esse ao aos à às já não nao também tambem
novo nova novos novas após apos entre seu sua seus suas pode podem ter tem têm era até ate the of and to in
for on with by from at as is are was were be been it its this that these those an or not but new how why
what when who will can has have had you your our their they we all more most than into about after over
says said just now get gets via out up use using used one two first also
under over version versions release released releases update updates report reports today week year
segundo ainda agora hoje semana anos ano lança lançou anuncia anunciou chega versão
""".split())

_lock = threading.Lock()
_model: dict | None = None  # {"tech": modelo ou {}, "games": modelo ou {}}
AREAS = ("tech", "games")
_token_re = re.compile(r"[a-z0-9áàâãéêíóôõúüç][a-z0-9áàâãéêíóôõúüç.+#-]*[a-z0-9+#]|[a-z0-9]", re.IGNORECASE)


def tokenize(item: dict) -> set[str]:
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    words = {w for w in _token_re.findall(text) if len(w) >= 3 and w not in STOPWORDS and not w.isdigit()}
    # CVEs individuais quase nunca se repetem; não ajudam a aprender gosto.
    words = {w for w in words if not w.startswith("cve-")}
    features = {f"w:{w}" for w in words}
    if item.get("source_id"):
        features.add(f"src:{item['source_id']}")
    for tech in filter(None, (item.get("matched_tech") or "").split(",")):
        features.add(f"tech:{tech}")
    if item.get("category"):
        features.add(f"cat:{item['category']}")
    return features


def _doc_freq(items: list[dict]) -> Counter:
    counts: Counter = Counter()
    for item in items:
        counts.update(tokenize(item))
    return counts


def _area_sql(area: str) -> str:
    return "category='games'" if area == "games" else "category<>'games'"


def build_model(area: str = "tech") -> dict | None:
    conn = db.connect()
    rows = [dict(r) for r in conn.execute(
        "SELECT id, title, summary, source_id, matched_tech, category, liked FROM items WHERE " + _area_sql(area)
    ).fetchall()]
    liked = [r for r in rows if r["liked"] == 1]
    if len(liked) < MIN_LIKES:
        return None
    disliked = [r for r in rows if r["liked"] == -1]
    all_df, n_all = _doc_freq(rows), len(rows)
    like_df, dislike_df = _doc_freq(liked), _doc_freq(disliked)

    def lift(df: Counter, n: int, feature: str) -> float:
        """Quanto o termo é mais comum no grupo do que no conjunto todo (log)."""
        p_group = (df[feature] + ALPHA) / (n + 2 * ALPHA)
        p_all = (all_df[feature] + ALPHA) / (n_all + 2 * ALPHA)
        return math.log(p_group / p_all)

    weights = {}
    for feature in set(like_df) | set(dislike_df):
        weight = 0.0
        if like_df[feature]:
            weight += lift(like_df, len(liked), feature)
        if dislike_df[feature]:
            weight -= lift(dislike_df, len(disliked), feature)
        if feature.startswith("w:") and like_df[feature] < 2 and dislike_df[feature] < 2:
            continue  # palavra vista numa notícia só: é ruído, não gosto
        if feature.startswith("src:"):
            weight *= 0.6  # a fonte ajuda, mas não deve dominar
        if abs(weight) > 0.15:
            weights[feature] = weight
    return {"weights": weights, "likes": len(liked), "dislikes": len(disliked)}


def affinity(item: dict, model: dict | None) -> float:
    """0 a 100; 50 é neutro."""
    if not model:
        return 50.0
    weights = model["weights"]
    hits = [weights[f] for f in tokenize(item) if f in weights]
    if not hits:
        return 50.0
    # Média dos pesos mais fortes, para notícias longas não serem favorecidas.
    hits.sort(key=abs, reverse=True)
    top = hits[:8]
    raw = sum(top) / math.sqrt(len(top))
    return round(100 / (1 + math.exp(-raw)), 1)


def explain(item: dict, model: dict | None, limit: int = 4) -> list[str]:
    """Termos que mais puxaram a afinidade para cima (para mostrar ao usuário)."""
    if not model:
        return []
    weights = model["weights"]
    positive = sorted(((weights[f], f) for f in tokenize(item) if weights.get(f, 0) > 0), reverse=True)
    labels = []
    for _w, feature in positive[:limit]:
        kind, value = feature.split(":", 1)
        labels.append({"w": value, "src": f"fonte {value}", "tech": value, "cat": value}[kind])
    return labels


def get_model(area: str = "tech") -> dict | None:
    global _model
    with _lock:
        if _model is None:
            _model = {a: build_model(a) or {} for a in AREAS}
        return _model.get(area) or None


def model_for(item: dict) -> dict | None:
    return get_model(config.area_of(item.get("category")))


def refresh() -> dict | None:
    """Reconstrói os modelos e recalcula a afinidade de todas as notícias.

    Devolve o modelo de tecnologia (o de games fica em ``get_model("games")``).
    """
    global _model
    with _lock:
        _model = {a: build_model(a) or {} for a in AREAS}
        models = {a: _model[a] or None for a in AREAS}
    conn = db.connect()
    rows = [dict(r) for r in conn.execute(
        "SELECT id, title, summary, source_id, matched_tech, category FROM items"
    ).fetchall()]
    conn.executemany("UPDATE items SET affinity=? WHERE id=?",
                     [(affinity(r, models[config.area_of(r["category"])]), r["id"]) for r in rows])
    conn.commit()
    return models["tech"]


def status(area: str = "tech") -> dict:
    area = area if area in AREAS else "tech"
    model = get_model(area)
    conn = db.connect()
    where = _area_sql(area)
    likes = conn.execute(f"SELECT COUNT(*) FROM items WHERE liked=1 AND {where}").fetchone()[0]
    dislikes = conn.execute(f"SELECT COUNT(*) FROM items WHERE liked=-1 AND {where}").fetchone()[0]
    top = []
    if model:
        top = [f.split(":", 1)[1] for f, w in sorted(model["weights"].items(), key=lambda kv: -kv[1])
               if w > 0 and f.startswith(("w:", "tech:"))][:12]
    return {"area": area, "active": bool(model), "likes": likes, "dislikes": dislikes, "min_likes": MIN_LIKES,
            "top_terms": top}
