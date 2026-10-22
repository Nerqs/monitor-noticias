"""Abre o leitor do Monitor de notícias dentro do app Android.

O mesmo servidor local do PC (Flask) roda numa thread, só para o próprio
aparelho (127.0.0.1), e a tela é um WebView apontando para ele. Os dados ficam
na pasta privada do app.
"""

from __future__ import annotations

import os
import threading

import toga
from toga.style import Pack


def _open_url_android(url: str) -> bool:
    """Abre links no navegador do celular (o webbrowser do Python não funciona no Android)."""
    from android.content import Intent
    from android.net import Uri
    from org.beeware.android import MainActivity

    intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
    intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    MainActivity.singletonThis.startActivity(intent)
    return True


class MonitorApp(toga.App):
    def startup(self):
        os.environ["MONITOR_DATA_DIR"] = str(self.paths.data)
        os.environ["MONITOR_PLATFORM"] = "android"

        from werkzeug.serving import make_server

        from monitor import config, logsetup, pipeline, web

        logsetup.setup()
        web.open_url = _open_url_android
        app = web.create_app()
        server = make_server("127.0.0.1", 0, app, threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.server = server

        if config.load_config().get("android", {}).get("collect_on_open", True):
            pipeline.run_in_background()

        self.webview = toga.WebView(url=f"http://127.0.0.1:{server.server_port}/", style=Pack(flex=1))
        self.main_window = toga.MainWindow(title=self.formal_name)
        self.main_window.content = self.webview
        self.main_window.show()


def main():
    return MonitorApp("Monitor de notícias", "io.github.nerqs.monitornoticias")
