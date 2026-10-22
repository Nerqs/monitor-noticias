"""Diagnóstico do programa: confere ambiente, banco, rede, e-mail e agendamento.

Use ``python run.py --diagnostico`` ou o botão "Rodar diagnóstico" na página Saúde.
Cada item volta com a situação (ok, aviso ou erro), o detalhe e como corrigir.
"""

from __future__ import annotations

import importlib
import shutil
import smtplib
import ssl
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from . import __version__, config, db, secrets

REQUIRED_MODULES = ["feedparser", "httpx", "flask", "jinja2", "yaml", "bs4", "readability", "lxml_html_clean",
                    "keyring"]


def _check(name: str, status: str, detail: str, fix: str = "") -> dict:
    return {"name": name, "status": status, "detail": detail, "fix": fix}


def check_python() -> dict:
    v = sys.version_info
    text = f"Python {v.major}.{v.minor}.{v.micro}, Monitor {__version__}"
    if v < (3, 10):
        return _check("Python", "erro", text, "Instale o Python 3.10 ou mais novo.")
    return _check("Python", "ok", text)


def check_modules() -> dict:
    missing = []
    required = [m for m in REQUIRED_MODULES if m not in ("readability", "lxml_html_clean", "keyring")] \
        if config.is_android() else REQUIRED_MODULES
    for name in required:
        try:
            importlib.import_module(name)
        except Exception as exc:
            missing.append(f"{name} ({exc.__class__.__name__})")
    if missing:
        return _check("Bibliotecas", "erro", "Faltando: " + ", ".join(missing),
                      "Rode de novo o iniciar.bat/iniciar.sh ou: pip install -r requirements.txt")
    return _check("Bibliotecas", "ok", f"{len(required)} bibliotecas principais carregadas.")


def check_data_dir() -> dict:
    folder = config.data_dir()
    probe = folder / ".teste-diagnostico"
    try:
        probe.write_text("ok")
        probe.unlink()
    except OSError as exc:
        return _check("Pasta de dados", "erro", f"Não dá para gravar em {folder}: {exc}",
                      "Mova o programa para uma pasta sua (ex.: Documentos) ou ajuste as permissões.")
    free = shutil.disk_usage(folder).free
    if free < 500 * 1024 * 1024:
        return _check("Pasta de dados", "aviso", f"{folder} · só {free // 1048576} MB livres no disco.",
                      "Libere espaço no disco.")
    return _check("Pasta de dados", "ok", f"{folder} · {free // 1073741824} GB livres.")


def check_database() -> dict:
    try:
        ok = db.integrity_ok()
        stats = db.stats()
    except Exception as exc:
        return _check("Banco de dados", "erro", f"Não abriu: {exc}",
                      "Feche outras cópias do programa. Se continuar, renomeie data/monitor.db (as notícias "
                      "serão coletadas de novo).")
    detail = f"{stats['items']} notícias, {stats['bytes'] / 1048576:.1f} MB"
    if not ok:
        return _check("Banco de dados", "erro", "A verificação de integridade falhou. " + detail,
                      "Faça um backup e use \"Limpar agora\" na página Saúde; se persistir, renomeie data/monitor.db.")
    search = "busca completa ativa" if db.fts_available() else "busca simples (SQLite sem FTS5)"
    return _check("Banco de dados", "ok", f"{detail} · {search}.")


def check_keyring() -> dict:
    if secrets.keyring_available():
        return _check("Cofre de senhas", "ok", "Disponível para guardar senha e tokens.")
    return _check("Cofre de senhas", "aviso", "Indisponível neste sistema.",
                  "No Linux, instale gnome-keyring ou kwallet; ou use as variáveis MONITOR_SMTP_PASSWORD etc.")


def check_window() -> dict:
    try:
        importlib.import_module("webview")
    except Exception:
        return _check("Janela própria", "aviso", "pywebview não está instalado; o leitor abre no navegador.",
                      "pip install pywebview")
    if sys.platform.startswith("linux"):
        for module in ("gi", "qtpy", "PyQt5", "PyQt6", "PySide6"):
            try:
                importlib.import_module(module)
                return _check("Janela própria", "ok", f"pywebview com {module}.")
            except Exception:
                continue
        return _check("Janela própria", "aviso", "Sem GTK nem Qt: o leitor abre no navegador.",
                      "sudo apt install python3-gi gir1.2-webkit2-4.1")
    return _check("Janela própria", "ok", "pywebview disponível.")


def check_internet(cfg: dict) -> list[dict]:
    from .pipeline import client_for

    results = []
    skew = None
    reached = []
    with client_for(cfg.get("collection", {}), retries=False) as client:
        for url in ("https://www.cisa.gov/", "https://api.github.com/", "https://services.nvd.nist.gov/"):
            try:
                response = client.head(url, timeout=10)
                reached.append(url.split("/")[2])
                if skew is None and response.headers.get("date"):
                    remote = parsedate_to_datetime(response.headers["date"])
                    skew = (datetime.now(timezone.utc) - remote).total_seconds()
            except Exception:
                continue
    if not reached:
        results.append(_check("Internet", "erro", "Nenhum site de teste respondeu.",
                              "Confira a conexão, proxy ou firewall/antivírus bloqueando o programa."))
    else:
        results.append(_check("Internet", "ok" if len(reached) == 3 else "aviso",
                              f"Responderam: {', '.join(reached)}."))
    if skew is not None:
        if abs(skew) > 300:
            results.append(_check("Relógio", "aviso", f"O relógio do computador está {abs(skew) / 60:.0f} min "
                                  f"{'adiantado' if skew > 0 else 'atrasado'}.",
                                  "Ative o ajuste automático de data e hora do sistema."))
        else:
            results.append(_check("Relógio", "ok", f"Diferença de {abs(skew):.0f} s para a internet."))
    return results


def check_email(cfg: dict) -> dict:
    email_cfg = cfg.get("email", {})
    host = email_cfg.get("smtp_host")
    if not email_cfg.get("username") or not email_cfg.get("to_addresses"):
        return _check("E-mail", "aviso", "Não configurado.", "Preencha em Configurações → E-mail (opcional).")
    port = int(email_cfg.get("smtp_port") or 587)
    try:
        context = ssl.create_default_context()
        if email_cfg.get("security") == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=15, context=context)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
        with server:
            server.ehlo()
            if email_cfg.get("security") == "starttls":
                server.starttls(context=context)
                server.ehlo()
            if email_cfg.get("auth") == "oauth_microsoft":
                from . import oauth

                if not oauth.has_refresh_token():
                    return _check("E-mail", "aviso", f"Servidor {host} OK, conta Microsoft não conectada.",
                                  "Clique em \"Conectar conta Microsoft\" em Configurações → E-mail.")
                server.auth("XOAUTH2", lambda challenge=None: oauth.xoauth2_string(email_cfg["username"]),
                            initial_response_ok=True)
            else:
                password = secrets.get_password(email_cfg["username"])
                if not password:
                    return _check("E-mail", "aviso", f"Servidor {host} OK, mas sem senha salva.",
                                  "Salve a senha em Configurações → E-mail.")
                server.login(email_cfg["username"], password)
    except smtplib.SMTPAuthenticationError as exc:
        return _check("E-mail", "erro", f"O servidor recusou o login: {exc.smtp_error!r}"[:300],
                      "Use senha de app ou o login com conta Microsoft (Outlook).")
    except Exception as exc:
        return _check("E-mail", "erro", f"Não conectou em {host}:{port}: {exc}",
                      "Confira servidor, porta e segurança, ou se o antivírus bloqueia a porta.")
    return _check("E-mail", "ok", f"Login em {host}:{port} funcionando.")


def check_sources(cfg: dict) -> dict:
    from . import health

    sources = [s for s in config.load_sources() if s.get("enabled", True)]
    run = db.last_run()
    failed = len(db.errors_for_run(run["id"])) if run else 0
    silent = health.silent_sources(cfg)
    detail = f"{len(sources)} fontes ativas, {failed} com erro na última coleta, {len(silent)} silenciosas."
    if failed or silent:
        return _check("Fontes", "aviso", detail, "Veja a página Erros de coleta.")
    return _check("Fontes", "ok", detail)


def check_schedule(cfg: dict) -> dict:
    from . import scheduler

    status = scheduler.status(cfg)
    if status["missed"]:
        return _check("Agendamento", "aviso", status["message"], status.get("fix", ""))
    return _check("Agendamento", "ok" if status["installed"] else "aviso", status["message"],
                  "" if status["installed"] else "Use \"Agendar coleta diária\" na página Saúde (opcional).")


def check_selfaudit() -> dict:
    from . import selfcheck

    report = selfcheck.last_report()
    if not report:
        return _check("Bibliotecas vulneráveis", "aviso", "Autoauditoria ainda não rodou.",
                      "Ela roda sozinha após a próxima coleta.")
    if report["findings"]:
        pkgs = sorted({f["package"] for f in report["findings"]})
        return _check("Bibliotecas vulneráveis", "erro",
                      f"{len(report['findings'])} falhas conhecidas em: {', '.join(pkgs)}.",
                      "Atualize o programa (página Saúde → Verificar atualização) ou rode: "
                      "pip install -U " + " ".join(pkgs))
    return _check("Bibliotecas vulneráveis", "ok", f"{report['packages']} bibliotecas sem falhas conhecidas.")


def run(quick: bool = False) -> list[dict]:
    cfg = config.load_config()
    checks = [check_python(), check_modules(), check_data_dir(), check_database(), check_keyring()]
    if not config.is_android():
        checks += [check_window(), check_schedule(cfg)]
    checks += [check_sources(cfg), check_selfaudit()]
    if not quick:
        checks += check_internet(cfg)
        checks.append(check_email(cfg))
    return checks


def print_report() -> int:
    marks = {"ok": "OK   ", "aviso": "AVISO", "erro": "ERRO "}
    checks = run()
    for c in checks:
        print(f"[{marks[c['status']]}] {c['name']}: {c['detail']}")
        if c["fix"] and c["status"] != "ok":
            print(f"         Como corrigir: {c['fix']}")
    return 1 if any(c["status"] == "erro" for c in checks) else 0
