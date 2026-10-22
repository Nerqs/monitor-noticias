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
}


class EmailError(Exception):
    pass


def build_digest(cfg: dict, only_new: bool = True) -> tuple[str, str, list[int]]:
    """Devolve (assunto, html, ids das notícias incluídas)."""
    limit = int(cfg.get("email", {}).get("max_items_per_section", 15))
    sections = db.items_for_email(only_new=only_new, limit_per_section=limit)
    run = db.last_run()
    errors = db.errors_for_run(run["id"]) if run else []
    ids = [item["id"] for items in sections.values() for item in items]
    n_crit = sum(1 for i in sections["criticos"] if i["level"] == "critico")
    today = datetime.now().strftime("%d/%m/%Y %H:%M")
    subject = f"Monitor de notícias {today}: {n_crit} críticas, {len(ids)} no total"
    html = _env.get_template("email.html").render(
        sections=sections,
        titles=SECTION_TITLES,
        errors=errors,
        generated=today,
        total=len(ids),
        n_critical=n_crit,
        n_security=sum(1 for i in sections["criticos"] if i["category"] == "seguranca") + len(sections["seguranca"]),
        n_tech=len(sections["tecnologia"]) + sum(1 for i in sections["criticos"] if i["category"] == "tecnologia"),
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
            if username:
                server.login(username, password)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        detail = exc.smtp_error.decode(errors="ignore") if isinstance(exc.smtp_error, bytes) else str(exc.smtp_error)
        hint = ""
        if "basic authentication" in detail.lower() or "outlook" in host or "office365" in host:
            hint = (" Contas Microsoft costumam exigir uma senha de aplicativo (com verificação em duas etapas"
                    " ativada) ou ter o SMTP AUTH liberado. Veja o README.")
        elif "gmail" in host:
            hint = " No Gmail, use uma senha de app (myaccount.google.com/apppasswords)."
        raise EmailError(f"O servidor recusou o usuário/senha: {detail}.{hint}")
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailError(f"Falha ao enviar pelo servidor {host}:{port}: {exc}")


def send_digest(cfg: dict | None = None, only_new: bool = True) -> dict:
    cfg = cfg or config.load_config()
    subject, html, ids = build_digest(cfg, only_new=only_new)
    if not ids:
        return {"sent": False, "reason": "Nenhuma notícia nova para enviar."}
    limit = int(cfg.get("email", {}).get("max_items_per_section", 15))
    text = _plain_text(db.items_for_email(only_new=only_new, limit_per_section=limit))
    send_message(cfg, subject, html, text)
    db.mark_emailed(ids)
    return {"sent": True, "items": len(ids), "subject": subject}


def send_test(cfg: dict | None = None) -> None:
    cfg = cfg or config.load_config()
    html = _env.get_template("email_test.html").render()
    send_message(cfg, "Monitor de notícias: e-mail de teste", html, "Se você recebeu isto, o envio está funcionando.")
