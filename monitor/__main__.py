"""Ponto de entrada: ``python -m monitor`` (ou ``python run.py``).

Exemplos:
  python -m monitor                 abre o leitor
  python -m monitor --coletar       abre o leitor e já começa a coletar
  python -m monitor --so-coletar    coleta sem abrir janela (bom para agendar)
  python -m monitor --so-coletar --email   coleta e envia o resumo por e-mail
  python -m monitor --navegador     abre no navegador em vez da janela própria
  python -m monitor --diagnostico   confere se está tudo funcionando e diz o que corrigir
  python -m monitor --residente     fica na bandeja e avisa quando surge algo crítico
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import webbrowser

from . import __version__, db, emailer, pipeline, web


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
            print(result.get("reason") or f"E-mail enviado com {result['items']} notícias.")
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
    print(f"autoteste: {checks}, {sources} fontes -> {'OK' if ok else 'FALHOU'}")
    return 0 if ok else 1


def run_resident() -> int:
    from .resident import Resident

    app = web.create_app()
    server, url = _serve(app)
    try:
        return Resident(url).run()
    finally:
        server.shutdown()


def run_gui(collect: bool, use_browser: bool) -> int:
    app = web.create_app()
    server, url = _serve(app)
    if collect:
        pipeline.run_in_background()

    if not use_browser:
        try:
            import webview

            webview.create_window("Monitor de notícias", url, width=1360, height=880, min_size=(900, 600))
            webview.start()
            server.shutdown()
            return 0
        except Exception as exc:  # sem pywebview ou sem backend gráfico (comum no Linux)
            print(f"Janela própria indisponível ({exc}). Abrindo no navegador.")

    print(f"Monitor de notícias aberto em {url}  (Ctrl+C para sair)")
    webbrowser.open(url)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    server.shutdown()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="monitor", description="Monitor de notícias de tecnologia")
    parser.add_argument("--coletar", action="store_true", help="começa a coletar assim que abrir")
    parser.add_argument("--so-coletar", action="store_true", help="coleta sem abrir a janela")
    parser.add_argument("--email", action="store_true", help="com --so-coletar, envia o resumo por e-mail")
    parser.add_argument("--navegador", action="store_true", help="abre no navegador em vez da janela própria")
    parser.add_argument("--diagnostico", action="store_true", help="confere ambiente, rede, e-mail e banco")
    parser.add_argument("--residente", action="store_true", help="fica na bandeja e avisa sobre falhas críticas")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--autoteste", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    from . import logsetup

    logsetup.setup()

    if args.autoteste:
        return self_test()
    if args.diagnostico:
        from . import diagnostics

        return diagnostics.print_report()
    if args.residente:
        return run_resident()
    if args.so_coletar:
        return run_headless(args.email)
    return run_gui(args.coletar, args.navegador)


if __name__ == "__main__":
    sys.exit(main())
