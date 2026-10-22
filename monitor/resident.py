"""Modo residente: fica na bandeja do sistema e avisa quando surge algo grave.

``python run.py --residente`` coleta a cada X minutos (Configurações → Alertas)
e mostra uma notificação da área de trabalho para cada notícia crítica nova
(falha explorada, CVSS alto, sua tecnologia ou sua versão afetada). O ícone da
bandeja tem atalhos para abrir o leitor, coletar agora e pausar os alertas.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import threading
import webbrowser

from . import config, db, notify, pipeline

log = logging.getLogger(__name__)


class Resident:
    def __init__(self, url: str):
        self.url = url
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.paused = False
        self.icon = None

    # --- Notificações ---------------------------------------------------------

    def notify(self, title: str, message: str) -> None:
        message = message[:250]
        if sys.platform.startswith("linux") and shutil.which("notify-send"):
            try:
                subprocess.run(["notify-send", "-a", "Monitor de notícias", "-u", "critical", title, message],
                               timeout=10, check=False)
                return
            except (OSError, subprocess.SubprocessError):
                pass
        if self.icon is not None and getattr(self.icon, "HAS_NOTIFICATION", False):
            try:
                self.icon.notify(message, title)
                return
            except Exception as exc:
                log.warning("notificação falhou: %s", exc)
        print(f"[ALERTA] {title}: {message}")

    def alert_new(self, cfg: dict) -> int:
        alerts = cfg.get("alerts", {})
        items = db.items_for_alert("notified", limit=20)
        if alerts.get("only_mine"):
            items = [i for i in items if i["matched_tech"] or i["version_verdict"] == "afeta"]
        if not items:
            return 0
        db.mark_flag_many([i["id"] for i in items], "notified")
        if self.paused:
            return 0
        for item in items[:3]:
            why = item["reasons"].split(" · ")[0] if item["reasons"] else item["source_name"]
            self.notify(f"Crítico: {item['title'][:90]}", why)
        if len(items) > 3:
            self.notify("Monitor de notícias", f"Mais {len(items) - 3} notícias críticas. Abra o leitor para ver.")
        if alerts.get("chat") and cfg.get("chat", {}).get("enabled"):
            try:
                notify.send(cfg, "Alerta: notícias críticas novas", items[:10])
                db.mark_flag_many([i["id"] for i in items[:10]], "chatted")
            except notify.ChatError as exc:
                log.warning("alerta por chat falhou: %s", exc)
        return len(items)

    # --- Laço de coleta -------------------------------------------------------

    def loop(self) -> None:
        while not self.stop.is_set():
            cfg = config.load_config()
            interval = max(10, int(cfg.get("alerts", {}).get("interval_minutes", 60)))
            if not self.paused:
                sources = None
                if cfg.get("alerts", {}).get("scope", "criticas") == "criticas":
                    sources = [s["id"] for s in config.load_sources() if s.get("category") == "seguranca"]
                summary = pipeline.run_collection(sources)
                if "error" not in summary:
                    log.info("residente: %s novas", summary.get("new_items"))
                    self.alert_new(cfg)
            self.wake.wait(interval * 60)
            self.wake.clear()

    # --- Bandeja --------------------------------------------------------------

    def open_reader(self, *_):
        webbrowser.open(self.url)

    def collect_now(self, *_):
        self.wake.set()

    def toggle_pause(self, *_):
        self.paused = not self.paused
        if self.icon is not None:
            self.icon.update_menu()

    def quit(self, *_):
        self.stop.set()
        self.wake.set()
        if self.icon is not None:
            self.icon.stop()

    def run(self) -> int:
        threading.Thread(target=self.loop, daemon=True).start()
        try:
            import pystray
            from PIL import Image

            image = Image.open(config.PACKAGE_DIR / "static" / "icone.png")
            menu = pystray.Menu(
                pystray.MenuItem("Abrir leitor", self.open_reader, default=True),
                pystray.MenuItem("Coletar agora", self.collect_now),
                pystray.MenuItem("Pausar alertas", self.toggle_pause, checked=lambda _: self.paused),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Sair", self.quit),
            )
            self.icon = pystray.Icon("monitor-noticias", image, "Monitor de notícias", menu)
            self.icon.run()
        except Exception as exc:  # sem bandeja (ex.: Linux sem AppIndicator): segue no terminal
            log.warning("bandeja indisponível (%s); alertas seguem ativos. Ctrl+C para sair.", exc)
            print(f"Modo de alertas ativo. Leitor em {self.url}  (Ctrl+C para sair)")
            try:
                self.stop.wait()
            except KeyboardInterrupt:
                pass
        return 0
