"""Dá uma nota de prioridade a cada notícia.

Níveis:
  critico  nota >= 70  (ex.: falha já explorada, CVSS 9+, zero-day)
  alto     nota >= 40  (ex.: vulnerabilidade que afeta suas tecnologias)
  normal   o resto

Notícias de games têm regras próprias (``score_game``): nunca são "crítico";
"alto" ali quer dizer destaque (lançamento, jogo grátis, evento, jogo que você segue).
"""

from __future__ import annotations

import re
from functools import lru_cache

CRITICAL_LEVEL = 70
HIGH_LEVEL = 40


@lru_cache(maxsize=512)
def _pattern(term: str) -> re.Pattern:
    """Casa o termo como palavra inteira ("rce" não casa com "source")."""
    term = term.strip().lower()
    start = r"(?<!\w)" if re.match(r"\w", term) else ""
    end = r"(?!\w)" if re.search(r"\w$", term) else ""
    return re.compile(start + re.escape(term) + end, re.IGNORECASE)


def matches(terms: list[str], text: str) -> list[str]:
    return [t for t in terms if t and t.strip() and _pattern(t).search(text)]


def score_item(item: dict, category: str, config: dict) -> dict:
    """Devolve ``score``, ``level``, ``matched_tech`` e ``reasons`` para o item."""
    if category == "games":
        return score_game(item, config)
    text = " ".join([item.get("title", ""), item.get("summary", ""), item.get("extra_text", "")])
    priority = config.get("priority", {})
    techs = config.get("interests", {}).get("technologies", [])

    score, reasons = 0, []
    if category == "seguranca":
        score += 20

    if item.get("kev"):
        score += 60
        reasons.append("Explorada ativamente (CISA KEV)")

    cvss = item.get("cvss")
    if cvss is not None:
        if cvss >= 9:
            score += 45
            reasons.append(f"CVSS {cvss:.1f} (crítico)")
        elif cvss >= 7:
            score += 25
            reasons.append(f"CVSS {cvss:.1f} (alto)")

    critical_hits = matches(priority.get("critical_keywords", []), text)
    if critical_hits:
        score += 50 + 5 * min(len(critical_hits) - 1, 3)
        reasons.append("Termos: " + ", ".join(critical_hits[:4]))

    high_hits = matches(priority.get("high_keywords", []), text)
    if high_hits:
        score += 12
        if not critical_hits:
            reasons.append("Termos: " + ", ".join(high_hits[:4]))

    if item.get("cves"):
        score += 5

    tech_hits = matches(techs, text)
    if tech_hits:
        # Uma tecnologia sua só pesa muito quando a notícia é sobre segurança.
        is_security = category == "seguranca" or critical_hits or high_hits or item.get("cves")
        score += 35 if is_security else 10
        reasons.append("Cita o que você usa: " + ", ".join(tech_hits))

    return {
        "score": score,
        "level": level_for(score),
        "matched_tech": ",".join(tech_hits),
        "reasons": " · ".join(reasons),
    }


def score_game(item: dict, config: dict) -> dict:
    """Nota de uma notícia de games. ``matched_tech`` guarda os jogos que você segue."""
    text = " ".join([item.get("title", ""), item.get("summary", "")])
    games = config.get("games", {})
    score, reasons = 0, []
    highlight = matches(games.get("highlight_keywords", []), text)
    if highlight:
        score += 40
        reasons.append("Destaque: " + ", ".join(highlight[:4]))
    following = matches(games.get("following", []), text)
    if following:
        score += 40
        reasons.append("Você acompanha: " + ", ".join(following))
    return {
        "score": score,
        "level": game_level(score),
        "matched_tech": ",".join(following),
        "reasons": " · ".join(reasons),
    }


def game_level(score: int) -> str:
    return "alto" if score >= HIGH_LEVEL else "normal"


def level_for(score: int) -> str:
    if score >= CRITICAL_LEVEL:
        return "critico"
    if score >= HIGH_LEVEL:
        return "alto"
    return "normal"


def epss_label(epss: float) -> str:
    return f"EPSS {epss * 100:.0f}%" if epss >= 0.01 else "EPSS <1%"


def finalize(item: dict, config: dict) -> dict:
    """Aplica sobre a nota do conteúdo (``base_score``) o que vem depois dela:
    filtros pessoais, probabilidade de exploração (EPSS) e checagem de versão.

    Devolve ``score``, ``level``, ``reasons`` e ``muted``.
    """
    score = int(item.get("base_score") if item.get("base_score") is not None else item.get("score") or 0)
    reasons = [r for r in (item.get("base_reasons") or "").split(" · ") if r]
    muted = 0

    text = " ".join([item.get("title") or "", item.get("summary") or ""])
    for rule in config.get("filters", {}).get("rules") or []:
        term = str(rule.get("term") or "").strip()
        if not term or (rule.get("source") and rule["source"] != item.get("source_id")):
            continue
        if not matches([term], text):
            continue
        if rule.get("action") == "mute":
            muted = 1
        elif rule.get("action") == "highlight":
            score += 25
            reasons.append(f"Destaque seu: {term}")

    epss = item.get("epss")
    if epss is not None:
        if epss >= 0.5:
            score += 30
            reasons.append(f"{epss_label(epss)}: alta chance de exploração em 30 dias")
        elif epss >= 0.1:
            score += 15
            reasons.append(f"{epss_label(epss)} de chance de exploração em 30 dias")

    verdict = item.get("version_verdict")
    if verdict == "afeta":
        score += 40
        reasons.insert(0, f"Afeta sua versão: {item.get('version_note')}")
    elif verdict == "nao_afeta":
        score -= 25
        reasons.append(f"Sua versão não é afetada: {item.get('version_note')}")

    level = game_level(score) if item.get("category") == "games" else level_for(score)
    return {"score": score, "level": level, "reasons": " · ".join(reasons), "muted": muted}
