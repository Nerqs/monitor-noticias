"""Dá uma nota de prioridade a cada notícia.

Níveis:
  critico  nota >= 70  (ex.: falha já explorada, CVSS 9+, zero-day)
  alto     nota >= 40  (ex.: vulnerabilidade que afeta suas tecnologias)
  normal   o resto
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

    if score >= CRITICAL_LEVEL:
        level = "critico"
    elif score >= HIGH_LEVEL:
        level = "alto"
    else:
        level = "normal"

    return {
        "score": score,
        "level": level,
        "matched_tech": ",".join(tech_hits),
        "reasons": " · ".join(reasons),
    }
