"""Limpeza do HTML dos artigos antes de mostrar no leitor.

Funciona por lista de permissões: só passam as tags e atributos de texto
(parágrafos, títulos, listas, tabelas, imagens, links). Todo o resto é
removido, e endereços que não sejam http(s) são descartados.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Comment

ALLOWED_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "caption", "cite", "code", "dd", "del", "dfn", "div", "dl", "dt",
    "em", "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "ins", "kbd", "li",
    "mark", "ol", "p", "pre", "q", "s", "samp", "small", "span", "strong", "sub", "sup", "table", "tbody",
    "td", "tfoot", "th", "thead", "time", "tr", "u", "ul", "picture",
}
# Tags removidas junto com todo o conteúdo (as demais fora da lista só perdem a tag).
DROP_WITH_CONTENT = {
    "script", "style", "iframe", "frame", "frameset", "object", "embed", "applet", "form", "input", "button",
    "select", "textarea", "noscript", "template", "svg", "math", "link", "meta", "base", "audio", "video",
    "source", "track", "canvas", "dialog", "head", "title",
}
ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
    "time": {"datetime"},
    "blockquote": {"cite"},
    "q": {"cite"},
    "abbr": {"title"},
}
URL_ATTRS = {"href", "src", "cite"}
_SIZE = re.compile(r"^\d{1,4}%?$")


def safe_url(value: str, base: str = "") -> str | None:
    """Devolve a URL absoluta se for http(s); ``None`` para javascript:, data: etc."""
    value = (value or "").strip()
    if not value:
        return None
    absolute = urljoin(base, value) if base else value
    parts = urlsplit(absolute)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return absolute


def clean_html(html: str, base_url: str = "", block_images: bool = False) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()
    for tag in soup.find_all(DROP_WITH_CONTENT):
        tag.decompose()
    for tag in soup.find_all(True):
        if tag.decomposed:
            continue
        name = tag.name.lower()
        if name not in ALLOWED_TAGS:
            tag.unwrap()
            continue
        allowed = ALLOWED_ATTRS.get(name, set())
        for attr in list(tag.attrs):
            value = tag.attrs[attr]
            if isinstance(value, list):
                value = " ".join(value)
            if attr not in allowed:
                del tag[attr]
            elif attr in URL_ATTRS:
                url = safe_url(value, base_url)
                if url:
                    tag[attr] = url
                else:
                    del tag[attr]
            elif attr in ("width", "height") and not _SIZE.match(str(value)):
                del tag[attr]
        if name == "img":
            if not tag.get("src"):
                tag.decompose()
                continue
            if block_images:
                # Imagens externas servem de rastreador: vira um link que você abre se quiser.
                link = soup.new_tag("a", href=tag["src"])
                link.string = f"[imagem bloqueada: {tag.get('alt') or urlsplit(tag['src']).netloc}]"
                tag.replace_with(link)
                continue
            tag["loading"] = "lazy"
            tag["referrerpolicy"] = "no-referrer"
        if name == "a":
            if tag.get("href"):
                tag["target"] = "_blank"
                tag["rel"] = "noopener noreferrer"
    return str(soup)
