"""Envio dos destaques para o celular: Telegram, Discord ou Microsoft Teams.

O token do Telegram e os endereços de webhook ficam no cofre do sistema,
como a senha do e-mail.
"""

from __future__ import annotations

import html

from . import config, db, fetchers, secrets

PROVIDERS = ["telegram", "discord", "teams"]
LEVEL_LABEL = {"critico": "CRÍTICO", "alto": "ALTO", "normal": ""}


class ChatError(Exception):
    pass


def _secret_name(provider: str) -> str:
    return "telegram_token" if provider == "telegram" else f"{provider}_webhook"


def has_secret(provider: str) -> bool:
    return bool(secrets.get_secret(_secret_name(provider)))


def save_secret(provider: str, value: str) -> None:
    if provider not in PROVIDERS:
        raise ChatError("Serviço de chat desconhecido.")
    if not secrets.set_secret(_secret_name(provider), value.strip()):
        raise ChatError("O cofre de senhas do sistema não está disponível. "
                        f"Defina a variável de ambiente MONITOR_{_secret_name(provider).upper()}.")


def _lines(items: list[dict]) -> list[dict]:
    out = []
    for item in items:
        extra = []
        if item.get("kev"):
            extra.append("explorada (CISA KEV)")
        if item.get("version_verdict") == "afeta":
            extra.append(f"afeta {item.get('version_note')}")
        if item.get("epss") is not None and item["epss"] >= 0.1:
            extra.append(f"EPSS {item['epss'] * 100:.0f}%")
        out.append({
            "level": LEVEL_LABEL.get(item.get("level"), ""),
            "title": item["title"],
            "source": item["source_name"],
            "extra": ", ".join(extra),
            "url": item["url"],
        })
    return out


def _chunks(parts: list[str], limit: int) -> list[str]:
    chunks, current = [], ""
    for part in parts:
        if current and len(current) + len(part) + 2 > limit:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{part}" if current else part
    if current:
        chunks.append(current[:limit])
    return chunks


def _post(client, url: str, payload: dict) -> None:
    try:
        response = client.post(url, json=payload, timeout=20)
    except Exception as exc:
        raise ChatError(f"Falha de rede ao enviar: {exc}")
    if response.status_code >= 300:
        detail = response.text[:200]
        raise ChatError(f"O serviço recusou a mensagem (HTTP {response.status_code}): {detail}")


def send(cfg: dict, title: str, items: list[dict]) -> int:
    chat = cfg.get("chat", {})
    provider = chat.get("provider", "telegram")
    secret = secrets.get_secret(_secret_name(provider))
    if not secret:
        raise ChatError("Salve primeiro o token ou o endereço do webhook em Configurações → Celular.")
    from .pipeline import client_for

    lines = _lines(items)
    with client_for(cfg.get("collection", {}), retries=False) as client:
        if provider == "telegram":
            chat_id = str(chat.get("telegram_chat_id") or "").strip()
            if not chat_id:
                raise ChatError("Informe o chat do Telegram (use o botão Descobrir).")
            parts = [f"<b>{html.escape(title)}</b>"] + [
                (f"<b>[{l['level']}]</b> " if l["level"] else "") + f"{html.escape(l['title'])}\n"
                f"<i>{html.escape(l['source'])}{' · ' + html.escape(l['extra']) if l['extra'] else ''}</i>\n"
                f"{html.escape(l['url'])}" for l in lines]
            for text in _chunks(parts, 4000):
                _post(client, f"https://api.telegram.org/bot{secret}/sendMessage",
                      {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})
        elif provider == "discord":
            parts = [f"**{title}**"] + [
                (f"**[{l['level']}]** " if l["level"] else "") + f"{l['title']}\n"
                f"*{l['source']}{' · ' + l['extra'] if l['extra'] else ''}*\n<{l['url']}>" for l in lines]
            for text in _chunks(parts, 1900):
                _post(client, secret, {"content": text, "username": "Monitor de notícias"})
        elif provider == "teams":
            body = [{"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium", "wrap": True}]
            for l in lines:
                body.append({"type": "TextBlock", "wrap": True, "spacing": "Medium",
                             "text": (f"**[{l['level']}]** " if l["level"] else "") + f"[{l['title']}]({l['url']})"})
                body.append({"type": "TextBlock", "wrap": True, "isSubtle": True, "spacing": "None",
                             "text": l["source"] + (f" · {l['extra']}" if l["extra"] else "")})
            _post(client, secret, {"type": "message", "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                            "type": "AdaptiveCard", "version": "1.4", "body": body}}]})
        else:
            raise ChatError("Serviço de chat desconhecido.")
    return len(items)


def send_highlights(cfg: dict | None = None) -> dict | None:
    """Manda as notícias críticas e altas que ainda não foram para o chat."""
    cfg = cfg or config.load_config()
    if not cfg.get("chat", {}).get("enabled"):
        return None
    items = db.items_for_chat(int(cfg["chat"].get("max_items", 10)))
    if not items:
        return {"sent": 0}
    n = send(cfg, f"Monitor de notícias: {len(items)} destaque{'s' if len(items) > 1 else ''}", items)
    db.mark_flag_many([i["id"] for i in items], "chatted")
    return {"sent": n}


def send_test(cfg: dict | None = None) -> None:
    cfg = cfg or config.load_config()
    sample = [{"title": "Mensagem de teste do Monitor de notícias", "source_name": "Monitor", "level": "normal",
               "url": "https://github.com/Nerqs/monitor-noticias", "kev": 0}]
    send(cfg, "Teste: se você recebeu isto, o envio está funcionando.", sample)


def discover_telegram_chat(cfg: dict | None = None) -> dict:
    """Acha o chat mais recente que mandou mensagem para o robô."""
    cfg = cfg or config.load_config()
    token = secrets.get_secret("telegram_token")
    if not token:
        raise ChatError("Salve primeiro o token do robô.")
    from .pipeline import client_for

    with client_for(cfg.get("collection", {}), retries=False) as client:
        try:
            data = fetchers.http_get(client, f"https://api.telegram.org/bot{token}/getUpdates").json()
        except fetchers.FetchError as exc:
            raise ChatError(f"O Telegram recusou o token: {exc}")
    for update in reversed(data.get("result") or []):
        message = update.get("message") or update.get("channel_post") or {}
        chat = message.get("chat") or {}
        if chat.get("id"):
            name = chat.get("title") or " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")]))
            return {"chat_id": str(chat["id"]), "name": name}
    raise ChatError("Nenhuma mensagem encontrada. Mande um \"oi\" para o seu robô no Telegram e tente de novo.")
