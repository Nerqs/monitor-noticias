"""Monta e envia o resumo por e-mail (HTML com estilos embutidos)."""

from __future__ import annotations

import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import config, db, secrets

_env = Environment(
    loader=FileSystemLoader(config.PACKAGE_DIR / "templates"),
    autoescape=select_autoescape(["html"]),
)

def _local_time(value: str | None) -> str:
    """Converte "AAAA-MM-DD HH:MM:SS" (UTC) para o horário local, ex.: "21/10 19:20"."""
    if not value:
        return ""
    try:
        moment = datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return value
    return moment.astimezone().strftime("%d/%m %H:%M")


_env.filters["local_time"] = _local_time

SECTION_TITLES = {
    "criticos": "Críticos e importantes",
    "seguranca": "Outras notícias de segurança",
    "tecnologia": "Tecnologia",
    "games_destaques": "Destaques de games",
    "games": "Outras notícias de games",
}


class EmailError(Exception):
    pass


def _limit(cfg: dict, area: str) -> int:
    section = cfg.get("games", {}) if area == "games" else cfg.get("email", {})
    return int(section.get("max_items_per_section") or cfg.get("email", {}).get("max_items_per_section", 15))


def build_digest(cfg: dict, only_new: bool = True, area: str = "tech") -> tuple[str, str, list[int]]:
    """Devolve (assunto, html, ids das notícias incluídas). ``area``: "tech" ou "games"."""
    sections = db.items_for_email(only_new=only_new, limit_per_section=_limit(cfg, area), area=area)
    run = db.last_run()
    games_source = {s["id"] for s in config.load_sources() if s.get("category") == config.GAMES}
    errors = [e for e in (db.errors_for_run(run["id"]) if run else [])
              if (e["source_id"] in games_source) == (area == "games")]
    ids = [item["id"] for items in sections.values() for item in items]
    today = datetime.now().strftime("%d/%m/%Y %H:%M")
    if area == "games":
        n_high = len(sections["games_destaques"])
        n_following = sum(1 for items in sections.values() for i in items if i["matched_tech"])
        subject = f"Monitor de games {today}: {n_high} destaques, {len(ids)} no total"
        heading, preheader = "Monitor de games", f"{n_high} destaques · {len(ids)} notícias de games"
        stats = [(n_high, "destaques", "#c4b5fd"), (n_following, "dos jogos que você segue", "#ffffff"),
                 (len(ids), "notícias de games", "#ffffff")]
    else:
        n_crit = sum(1 for i in sections["criticos"] if i["level"] == "critico")
        subject = f"Monitor de notícias {today}: {n_crit} críticas, {len(ids)} no total"
        heading, preheader = "Monitor de notícias", f"{n_crit} críticas · {len(ids)} notícias selecionadas para você"
        n_security = sum(1 for i in sections["criticos"] if i["category"] == "seguranca") + len(sections["seguranca"])
        n_tech = len(sections["tecnologia"]) + sum(1 for i in sections["criticos"] if i["category"] == "tecnologia")
        stats = [(n_crit, "críticas", "#f87171"), (n_security, "de segurança", "#ffffff"),
                 (n_tech, "de tecnologia", "#ffffff")]
    html = _env.get_template("email.html").render(
        area=area,
        heading=heading,
        preheader=preheader,
        stats=stats,
        sections=sections,
        titles=SECTION_TITLES,
        errors=errors,
        generated=today,
        total=len(ids),
    )
    return subject, html, ids


def _plain_text(sections: dict[str, list[dict]]) -> str:
    lines = []
    for key, items in sections.items():
        if not items:
            continue
        lines.append(SECTION_TITLES[key].upper())
        for item in items:
            lines.append(f"- [{item['source_name']}] {item['title']}\n  {item['url']}")
        lines.append("")
    return "\n".join(lines) or "Nenhuma notícia nova."


def send_message(cfg: dict, subject: str, html: str, text: str) -> None:
    email_cfg = cfg.get("email", {})
    recipients = [a.strip() for a in email_cfg.get("to_addresses") or [] if a.strip()]
    username = email_cfg.get("username", "").strip()
    sender = (email_cfg.get("from_address") or username).strip()
    if not recipients:
        raise EmailError("Informe pelo menos um destinatário nas configurações de e-mail.")
    if not email_cfg.get("smtp_host"):
        raise EmailError("Informe o servidor SMTP nas configurações de e-mail.")
    use_oauth = email_cfg.get("auth") == "oauth_microsoft"
    password = None
    if use_oauth:
        from . import oauth

        if not username:
            raise EmailError("Informe o usuário (seu e-mail) nas configurações.")
        try:
            auth_string = oauth.xoauth2_string(username, cfg)
        except oauth.OAuthError as exc:
            raise EmailError(str(exc))
    else:
        password = secrets.get_password(username) if username else None
        if username and not password:
            raise EmailError("Senha do e-mail não encontrada. Salve a senha na página Configurações.")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1] if "@" in sender else None)
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    host, port = email_cfg["smtp_host"], int(email_cfg.get("smtp_port") or 587)
    security = email_cfg.get("security", "starttls")
    context = ssl.create_default_context()
    try:
        if security == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=30, context=context)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
        with server:
            server.ehlo()
            if security == "starttls":
                server.starttls(context=context)
                server.ehlo()
            if use_oauth:
                server.auth("XOAUTH2", lambda challenge=None: auth_string, initial_response_ok=True)
            elif username:
                server.login(username, password)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        detail = exc.smtp_error.decode(errors="ignore") if isinstance(exc.smtp_error, bytes) else str(exc.smtp_error)
        hint = ""
        if use_oauth:
            hint = " Desconecte e conecte de novo a conta Microsoft; confira se o e-mail do usuário é o mesmo da conta."
        elif "basic authentication" in detail.lower() or "outlook" in host or "office365" in host:
            hint = (" A Microsoft está desligando o login por senha: use \"Conta Microsoft (OAuth2)\" em"
                    " Configurações → E-mail. Veja o README.")
        elif "gmail" in host:
            hint = " No Gmail, use uma senha de app (myaccount.google.com/apppasswords)."
        raise EmailError(f"O servidor recusou o usuário/senha: {detail}.{hint}")
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailError(f"Falha ao enviar pelo servidor {host}:{port}: {exc}")


def send_digest(cfg: dict | None = None, only_new: bool = True, area: str | None = None) -> dict:
    """Envia o resumo de tecnologia e, se ligado, o de games num e-mail separado.

    ``area`` "tech" ou "games" envia só aquele resumo.
    """
    cfg = cfg or config.load_config()
    areas = [area] if area else ["tech"] + (["games"] if cfg.get("games", {}).get("email", True) else [])
    result = {"sent": False, "items": 0, "subjects": []}
    for name in areas:
        subject, html, ids = build_digest(cfg, only_new=only_new, area=name)
        if not ids:
            continue
        sections = db.items_for_email(only_new=only_new, limit_per_section=_limit(cfg, name), area=name)
        send_message(cfg, subject, html, _plain_text(sections))
        db.mark_emailed(ids)
        result["sent"] = True
        result["items"] += len(ids)
        result["subjects"].append(subject)
    if not result["sent"]:
        return {"sent": False, "reason": "Nenhuma notícia nova para enviar."}
    result["subject"] = result["subjects"][0]
    result["emails"] = len(result["subjects"])
    return result


def send_test(cfg: dict | None = None) -> None:
    cfg = cfg or config.load_config()
    html = _env.get_template("email_test.html").render()
    send_message(cfg, "Monitor de notícias: e-mail de teste", html, "Se você recebeu isto, o envio está funcionando.")
