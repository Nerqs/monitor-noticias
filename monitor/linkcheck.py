"""Avalia o domínio de um link antes de abrir no navegador.

Marca como suspeitos domínios com caracteres de outros alfabetos (punycode,
"xn--"), que imitam letras latinas, e domínios muito parecidos com sites
conhecidos de segurança e tecnologia (ex.: "rnicrosoft.com").
"""

from __future__ import annotations

import ipaddress
import unicodedata
from urllib.parse import urlsplit

KNOWN = [
    "microsoft.com", "github.com", "google.com", "apple.com", "cisa.gov", "nist.gov", "php.net", "mariadb.org",
    "laravel.com", "nodejs.org", "npmjs.com", "cloudflare.com", "amazon.com", "oracle.com", "mozilla.org",
    "bleepingcomputer.com", "thehackernews.com", "paypal.com", "linkedin.com", "outlook.com", "office.com",
]


def _distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _registered(host: str) -> str:
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in ("com", "gov", "org", "net", "edu") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def assess(url: str) -> dict:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    try:
        display = host.encode("ascii").decode("idna") if "xn--" in host else host
    except UnicodeError:
        display = host
    reasons = []
    if parts.scheme not in ("http", "https"):
        reasons.append("o endereço não é http/https")
    if "xn--" in host or any(ord(c) > 127 for c in host):
        scripts = {unicodedata.name(c, "?").split()[0] for c in display if c.isalpha() and ord(c) > 127}
        reasons.append("o domínio usa caracteres de outro alfabeto" + (f" ({', '.join(sorted(scripts))})" if scripts else ""))
    try:
        ipaddress.ip_address(host)
        reasons.append("o link aponta direto para um número IP")
    except ValueError:
        pass
    if parts.username or "@" in parts.netloc:
        reasons.append("o endereço tem um \"@\", truque usado para esconder o destino real")
    registered = _registered(host)
    label, _, tld = registered.partition(".")
    for known in KNOWN:
        known_label, _, known_tld = known.partition(".")
        limit = 1 if len(known_label) <= 6 else 2
        if registered != known and tld == known_tld and 0 < _distance(label, known_label) <= limit:
            reasons.append(f"o domínio é muito parecido com {known}")
            break
    return {"host": host, "display": display, "registered": registered, "suspicious": bool(reasons),
            "reasons": reasons}
