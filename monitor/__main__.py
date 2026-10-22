"""Ponto de entrada: ``python -m monitor`` (ou ``python run.py``).

Exemplos:
  python -m monitor                 abre o leitor
  python -m monitor --coletar       abre o leitor e já começa a coletar
  python -m monitor --so-coletar    coleta sem abrir janela (bom para agendar)
  python -m monitor --so-coletar --email   coleta e envia o resumo por e-mail
  python -m monitor --navegador     abre no navegador em vez da janela própria
  python -m monitor --diagnostico   confere se está tudo funcionando e diz o que corrigir
  python -m monitor --inicio-sistema  abre escondido na bandeja (usado ao entrar no sistema)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import webbrowser

from . import __version__, db, emailer, pipeline, presence, web


def _serve(app, host="127.0.0.1", port=0):
    from werkzeug.serving import make_server

    server = make_server(host, port, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://{host}:{server.server_port}/"


def run_headless(send_email: bool) -> int:
    summary = pipeline.run_collection()
    if "error" in summary:
        print(summary["error"])
        return 1
    print(f"Coleta concluída: {summary['new_items']} notícias novas, "
          f"{summary['sources_ok']} fontes OK, {summary['sources_failed']} com erro.")
    for err in db.errors_for_run(summary["run_id"]):
        print(f"  ERRO {err['source_name']}: {err['error']}")
    if summary.get("chat"):
        print(f"Destaques enviados para o celular: {summary['chat']['sent']}.")
    if summary.get("chat_error"):
        print(f"Celular: {summary['chat_error']}")
    if send_email and "email" not in summary:
        try:
            result = emailer.send_digest()
            extra = " (dois e-mails: tecnologia e games)" if result.get("emails", 1) > 1 else ""
            print(result.get("reason") or f"E-mail enviado com {result['items']} notícias{extra}.")
        except emailer.EmailError as exc:
            print(f"E-mail não enviado: {exc}")
            return 1
    return 0


def self_test() -> int:
    """Confere se o pacote está completo (usado ao gerar o executável)."""
    import tempfile

    from . import config

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MONITOR_DATA_DIR"] = tmp
        client = web.create_app("t").test_client()
        checks = {
            "leitor": client.get("/").status_code,
            "estilo": client.get("/static/style.css").status_code,
            "script": client.get("/static/app.js").status_code,
            "api": client.get("/api/counts", headers={"X-Token": "t"}).status_code,
            "email": client.get("/email/preview?t=t").status_code,
            "saude": client.get("/api/health", headers={"X-Token": "t"}).status_code,
            "diagnostico": client.post("/api/diagnostics", json={"quick": True},
                                       headers={"X-Token": "t"}).status_code,
            "busca": client.get("/api/items?view=todos&q=php", headers={"X-Token": "t"}).status_code,
        }
        from . import selfcheck

        checks["bibliotecas"] = 200 if selfcheck.installed_packages() else 500
        sources = len(config.load_sources())
        db.close()
    ok = all(code == 200 for code in checks.values()) and sources > 0
    logging.info("autoteste: %s, %d fontes -> %s", checks, sources, "OK" if ok else "FALHOU")
    from .updates import mode

    print(f"autoteste: {checks}, {sources} fontes, instalação {mode()} -> {'OK' if ok else 'FALHOU'}")
    return 0 if ok else 1


def unblock_bundle() -> int:
    """Tira a marca "baixado da internet" das DLLs do executável no Windows.

    Ao extrair o .zip baixado, o Windows marca cada arquivo (Zone.Identifier) e o
    .NET recusa carregar DLL marcada; sem isso a janela própria não abre
    ("Failed to resolve Python.Runtime.Loader.Initialize").
    """
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return 0
    removed = 0
    for folder, _dirs, files in os.walk(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))):
        for name in files:
            if name.lower().endswith(".dll"):
                try:
                    os.remove(os.path.join(folder, name) + ":Zone.Identifier")
                    removed += 1
                except OSError:
                    pass  # sem marca, ou pasta sem permissão de escrita
    if removed:
        logging.info("marca de download removida de %d DLLs", removed)
    return removed


def _open_window(url: str, on_start=None, desktop=None, hidden: bool = False):
    """Abre a janela própria (pywebview). Volta quando a janela fecha de vez."""
    unblock_bundle()
    import webview

    window = webview.create_window("Monitor de notícias", url, width=1360, height=880, min_size=(900, 600),
                                   hidden=hidden)
    if desktop is not None:
        desktop.window = window
        window.events.closing += desktop.on_closing
        window.events.minimized += lambda: setattr(desktop, "minimized", True)
        window.events.restored += lambda: setattr(desktop, "minimized", False)

        def on_start(window):
            # Roda numa thread depois que a janela existe: liga a bandeja e o laço de coleta.
            gtk = sys.platform.startswith("linux") and str(getattr(webview, "renderer", "")).startswith("gtk")
            if not desktop.start_tray(gtk_main_loop=gtk) and hidden:
                desktop.show_window()  # sem bandeja não dá para ficar escondido
            desktop.start_loop()

    if on_start:
        webview.start(on_start, window)
    else:
        webview.start()
    return window


def _finish(server, code: int) -> int:
    """Desliga o servidor e encerra o processo de vez, mesmo com uma coleta no meio."""
    server.shutdown()
    db.close()
    logging.info("programa encerrado")
    logging.shutdown()
    os._exit(code)


def _desktop_hooks(desktop) -> dict:
    """O que o programa aberto faz quando outra cópia dele (ou a própria janela) pede."""
    return {
        "show": lambda data: desktop.show_window(),
        "hide": lambda data: desktop.hide_window(),
        "quit": lambda data: desktop.quit(),
        "collect": lambda data: desktop.collect_async(data.get("email"), scheduled=bool(data.get("scheduled"))),
    }


def run_gui(collect: bool, use_browser: bool, at_login: bool = False) -> int:
    from . import config, instance
    from .resident import Desktop

    # Já aberto (talvez escondido na bandeja): só traz a janela de volta.
    if instance.forward("show"):
        print("O Monitor de notícias já está aberto; a janela dele foi trazida para a frente.")
        return 0

    # O programa é um pequeno servidor em 127.0.0.1 (só este computador acessa,
    # com token). A janela própria, ou o navegador, só mostra as páginas dele.
    app = web.create_app()
    server, url = _serve(app)
    logging.info("leitor em %s", url)
    instance.register(url, web.API_TOKEN)
    desktop_cfg = config.load_config().get("desktop", {})
    # Abriu agora: confere se a coleta agendada ficou para trás (computador desligado no horário).
    desktop = Desktop(url, catch_up_now=not collect)
    web.app_hooks.update(_desktop_hooks(desktop))
    hidden = at_login and bool(desktop_cfg.get("start_hidden", True))
    desktop.hidden = hidden
    desktop.on_quit = lambda: _finish(server, 0)
    if collect:
        pipeline.run_in_background()

    if not use_browser:
        try:
            _open_window(url, desktop=desktop, hidden=hidden)
            desktop.stop.set()
            desktop.stop_tray()
            return _finish(server, 0)  # janela fechada de vez: fecha tudo junto
        except Exception:  # sem pywebview ou sem motor gráfico (WebView2 no Windows, GTK/Qt no Linux)
            logging.exception("Janela própria indisponível; abrindo no navegador")
            desktop.window = None

    if hidden:
        # Sem janela própria e iniciado com o sistema: fica só o ícone, que abre o navegador.
        desktop.run_without_window()
        return _finish(server, 0)

    # Modo navegador: encerra quando a última aba fecha (ou com Ctrl+C no terminal).
    print(f"Monitor de notícias aberto em {url}  (feche a aba ou use Ctrl+C para sair)")
    desktop.start_loop()
    webbrowser.open(url)
    try:
        presence.wait_until_gone()
    except KeyboardInterrupt:
        pass
    return _finish(server, 0)


def window_test(seconds: float = 60.0) -> int:
    """Abre a janela própria, confere que a página carregou e fecha (usado no CI)."""
    import webview

    app = web.create_app()
    server, url = _serve(app)
    result = {"title": None}

    def check(window):
        if window.events.loaded.wait(seconds):
            result["title"] = window.evaluate_js("document.title")
        window.destroy()

    try:
        _open_window(url, check)
    except Exception:
        logging.exception("testar-janela: falhou ao abrir")
    ok = bool(result["title"])
    msg = f"testar-janela: motor={getattr(webview, 'renderer', '?')}, título={result['title']!r} -> {'OK' if ok else 'FALHOU'}"
    logging.info(msg)
    print(msg)
    server.shutdown()
    return 0 if ok else 1


def _prepare_data():
    """Versão .zip anota onde guarda os dados; a instalada copia esses dados na 1ª vez."""
    from . import config

    try:
        if config.is_installed():
            return config.migrate_portable()
        config.remember_portable()
    except OSError as exc:  # sem permissão ou disco cheio: segue com a pasta como está
        config.migration_status = f"falhou: {exc!r}"
    return None


def _desktop_entry() -> None:
    """AppImage: cria o atalho no menu de aplicativos (os outros pacotes já trazem o seu)."""
    from . import desktop

    try:
        if desktop.integrate_appimage():
            logging.info("atalho do AppImage criado no menu de aplicativos")
            _reschedule()  # se o arquivo mudou de pasta, a coleta agendada aponta para o lugar novo
    except OSError:
        logging.exception("não foi possível criar o atalho do AppImage")


def _migration_status() -> str:
    from . import config

    return config.migration_status


def _reschedule() -> None:
    """Coleta agendada e alertas apontavam para o .exe antigo: aponta para o instalado."""
    from . import config, scheduler

    cfg = config.load_config()
    for name, check, install in (("coleta", lambda: cfg.get("schedule", {}).get("installed"),
                                  lambda: scheduler.install(cfg)),
                                 ("alertas", scheduler.resident_installed, scheduler.install_resident)):
        try:
            if check():
                install()
                logging.info("agendamento de %s refeito para a versão instalada", name)
        except Exception:
            logging.exception("não foi possível refazer o agendamento de %s", name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="monitor", description="Monitor de notícias de tecnologia")
    parser.add_argument("--coletar", action="store_true", help="começa a coletar assim que abrir")
    parser.add_argument("--so-coletar", action="store_true", help="coleta sem abrir a janela")
    parser.add_argument("--email", action="store_true", help="com --so-coletar, envia o resumo por e-mail")
    parser.add_argument("--navegador", action="store_true", help="abre no navegador em vez da janela própria")
    parser.add_argument("--diagnostico", action="store_true", help="confere ambiente, rede, e-mail e banco")
    parser.add_argument("--inicio-sistema", action="store_true",
                        help="abre escondido na bandeja e coleta se a agendada ficou para trás")
    parser.add_argument("--residente", action="store_true", help=argparse.SUPPRESS)  # nome antigo de --inicio-sistema
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--autoteste", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--testar-janela", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    from . import logsetup

    migrated = None
    if not args.autoteste:
        migrated = _prepare_data()
    logsetup.setup()
    if config_status := _migration_status():
        logging.info("migração da versão .zip: %s", config_status)
    if migrated:
        logging.info("dados da versão .zip copiados de %s", migrated)
        _reschedule()
    if not args.autoteste:
        _desktop_entry()

    if args.autoteste:
        return self_test()
    if args.testar_janela:
        return window_test()
    if args.diagnostico:
        from . import diagnostics

        return diagnostics.print_report()
    if args.so_coletar:
        from . import instance

        # Programa aberto na bandeja: ele mesmo coleta (e mostra a notificação).
        if instance.forward("collect", email=args.email, scheduled=True):
            print("O programa já está aberto: a coleta foi pedida para ele.")
            return 0
        return run_headless(args.email)
    return run_gui(args.coletar, args.navegador, at_login=args.inicio_sistema or args.residente)


if __name__ == "__main__":
    sys.exit(main())
