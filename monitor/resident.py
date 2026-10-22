"""Programa residente: ícone na bandeja, coleta de tempo em tempo e notificações.

Fechar a janela deixa o programa perto do relógio (Configurações → Bandeja e
notificações). Enquanto isso ele coleta a cada X minutos, roda a coleta
agendada do dia se o horário passou e mostra uma notificação da área de
trabalho quando chegam notícias novas. O ícone da bandeja tem atalhos para
abrir a janela, coletar agora, pausar e sair.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime

from . import config, db, emailer, notify, pipeline, scheduler

log = logging.getLogger(__name__)
TICK = 30  # segundos entre as conferências do laço


class Desktop:
    def __init__(self, url: str, window=None, catch_up_now: bool = False):
        self.url = url
        self.window = window  # janela do pywebview; None no modo navegador
        self.catch_up_now = catch_up_now
        self.stop = threading.Event()
        self.paused = False
        self.hidden = False
        self.minimized = False
        self.quitting = False
        self.told_hidden = False
        self.icon = None
        self.on_quit = None  # modo sem janela: como encerrar o programa
        self._seen_run = None

    # --- Notificações ---------------------------------------------------------

    def notify(self, title: str, message: str, urgent: bool = False) -> None:
        message = message[:250]
        if sys.platform.startswith("linux") and shutil.which("notify-send"):
            try:
                icon = str(config.PACKAGE_DIR / "static" / "icone.png")
                subprocess.run(["notify-send", "-a", "Monitor de notícias", "-i", icon,
                                "-u", "critical" if urgent else "normal", title, message], timeout=10, check=False)
                return
            except (OSError, subprocess.SubprocessError):
                pass
        if self.icon is not None and getattr(self.icon, "HAS_NOTIFICATION", False):
            try:
                self.icon.notify(message, title)
                return
            except Exception as exc:
                log.warning("notificação falhou: %s", exc)
        log.info("[aviso] %s: %s", title, message)

    def notify_new(self, cfg: dict) -> int:
        """Avisa das notícias que chegaram desde o último aviso. Devolve quantas eram."""
        alerts = cfg.get("alerts", {})
        items = db.items_to_notify()
        if not items:
            return 0
        db.mark_flag_many([i["id"] for i in items], "notified")
        mode = alerts.get("notify", "todas")
        if self.paused or mode == "nenhuma":
            return len(items)
        critical = [i for i in items if i["level"] == "critico" and i["category"] != config.GAMES]
        if alerts.get("only_mine"):
            critical = [i for i in critical if i["matched_tech"] or i["version_verdict"] == "afeta"]
        for item in critical[:3]:
            why = item["reasons"].split(" · ")[0] if item["reasons"] else item["source_name"]
            self.notify(f"Crítico: {item['title'][:90]}", why, urgent=True)
        if mode == "todas":
            self.notify("Monitor de notícias", summary_text(items, len(critical)))
        elif len(critical) > 3:
            self.notify("Monitor de notícias", f"Mais {len(critical) - 3} notícias críticas. Abra o programa para ver.")
        if critical and alerts.get("chat") and cfg.get("chat", {}).get("enabled"):
            try:
                notify.send(cfg, "Alerta: notícias críticas novas", critical[:10])
                db.mark_flag_many([i["id"] for i in critical[:10]], "chatted")
            except notify.ChatError as exc:
                log.warning("alerta por chat falhou: %s", exc)
        return len(items)

    def check_finished(self, cfg: dict) -> None:
        """Depois de cada coleta (de qualquer origem), decide se mostra a notificação."""
        snap = pipeline.progress.snapshot()
        run = db.last_run()
        if snap["running"] or not run or run["id"] == self._seen_run or not run.get("finished_at"):
            return
        self._seen_run = run["id"]
        # Coleta pedida na janela aberta: a própria janela avisa.
        if snap.get("origin") == "manual" and not self.hidden:
            db.mark_flag_many([i["id"] for i in db.items_to_notify()], "notified")
            return
        self.notify_new(cfg)

    # --- Coletas --------------------------------------------------------------

    def run_scheduled(self, send_email: bool | None = None) -> dict:
        """A coleta do horário agendado (ou a que ficou para trás com o computador desligado)."""
        cfg = config.load_config()
        if send_email is None:
            send_email = bool(cfg.get("schedule", {}).get("send_email"))
        summary = pipeline.run_collection(origin="agendada")
        if "error" in summary:
            return summary
        log.info("coleta agendada: %s novas", summary.get("new_items"))
        if send_email and "email" not in summary:
            try:
                emailer.send_digest()
            except emailer.EmailError as exc:
                log.warning("coleta agendada: e-mail não enviado: %s", exc)
        self.check_finished(cfg)
        return summary

    def run_background(self, cfg: dict) -> None:
        sources = None
        if cfg.get("alerts", {}).get("scope", "criticas") == "criticas":
            sources = [s["id"] for s in config.load_sources() if s.get("category") == "seguranca"]
        summary = pipeline.run_collection(sources, origin="automatica")
        if "error" not in summary:
            log.info("coleta automática: %s novas", summary.get("new_items"))
            self.check_finished(cfg)

    def background_due(self, cfg: dict) -> bool:
        alerts = cfg.get("alerts", {})
        if not alerts.get("background", True):
            return False
        interval = max(10, int(alerts.get("interval_minutes") or 60))
        last = scheduler.last_run_time()
        return last is None or (datetime.now().astimezone() - last).total_seconds() >= interval * 60

    def loop(self) -> None:
        self._seen_run = (db.last_run() or {}).get("id")
        db.mark_all_notified()
        cfg = config.load_config()
        try:
            if self.catch_up_now and cfg.get("desktop", {}).get("catch_up", True) and scheduler.collection_due(cfg):
                log.info("coleta agendada ficou para trás: rodando agora")
                self.run_scheduled()
        except Exception:
            log.exception("coleta ao iniciar falhou")
        while not self.stop.is_set():
            if self.stop.wait(TICK):
                break
            try:
                cfg = config.load_config()
                self.check_finished(cfg)
                if self.paused:
                    continue
                if cfg.get("desktop", {}).get("catch_up", True) and scheduler.collection_due(cfg):
                    self.run_scheduled()
                elif self.background_due(cfg):
                    self.run_background(cfg)
            except Exception:
                log.exception("laço do programa residente")

    def start_loop(self) -> None:
        threading.Thread(target=self.loop, daemon=True, name="residente").start()

    def collect_async(self, send_email: bool | None = None, scheduled: bool = False) -> None:
        if scheduled:
            threading.Thread(target=self.run_scheduled, args=(send_email,), daemon=True).start()
        else:
            pipeline.run_in_background(origin="bandeja")

    # --- Janela ---------------------------------------------------------------

    def show_window(self, *_):
        if self.window is None:
            webbrowser.open(self.url)
            return
        self.hidden = False
        try:
            self.window.show()
            if self.minimized:  # restore() tiraria a janela de maximizada; só quando minimizada
                self.window.restore()
        except Exception as exc:
            log.warning("não foi possível mostrar a janela: %s", exc)

    def hide_window(self, *_):
        if self.window is None:
            return
        self.hidden = True
        self.window.hide()
        if not self.told_hidden:
            self.told_hidden = True
            self.notify("O Monitor de notícias continua aberto",
                        "Ele segue coletando perto do relógio. Clique no ícone para abrir; para fechar de vez, use Sair.")

    def _ask_close(self):
        try:
            asked = self.window.evaluate_js("window.monitorAskClose ? window.monitorAskClose() : false")
        except Exception:
            asked = False
        if asked:
            self.told_hidden = True  # o aviso já explicou a bandeja
        else:
            self.hide_window()

    def on_closing(self) -> bool:
        """Evento da janela ao clicar no X. False cancela o fechamento."""
        if self.quitting:
            return True
        desktop = config.load_config().get("desktop", {})
        if not desktop.get("close_to_tray", True) or self.icon is None:
            self.quitting = True
            return True
        # O evento roda segurando a janela: mexer nela daqui travaria.
        target = self._ask_close if desktop.get("ask_on_close", True) else self.hide_window
        threading.Thread(target=target, daemon=True).start()
        return False

    def quit(self, *_):
        self.quitting = True
        self.stop.set()
        self.stop_tray()
        if self.window is not None:
            self.window.destroy()  # webview.start volta e o programa encerra
        elif self.on_quit:
            self.on_quit()

    # --- Bandeja --------------------------------------------------------------

    def toggle_pause(self, *_):
        self.paused = not self.paused
        if self.icon is not None:
            self.icon.update_menu()

    def _make_icon(self):
        import pystray
        from PIL import Image

        image = Image.open(config.PACKAGE_DIR / "static" / "icone.png")
        menu = pystray.Menu(
            pystray.MenuItem("Abrir o Monitor de notícias", self.show_window, default=True),
            pystray.MenuItem("Coletar agora", lambda *_: self.collect_async()),
            pystray.MenuItem("Pausar coleta automática e avisos", self.toggle_pause, checked=lambda _: self.paused),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Sair", self.quit),
        )
        return pystray.Icon("monitor-noticias", image, "Monitor de notícias", menu)

    def start_tray(self, gtk_main_loop: bool = False) -> bool:
        """Mostra o ícone sem tomar o laço principal (que é da janela). False se não deu."""
        try:
            self.icon = self._make_icon()
            backend = type(self.icon).__module__
            if "gtk" in backend or "appindicator" in backend:
                if gtk_main_loop:  # a janela já roda o laço do GTK: o ícone entra nele
                    from gi.repository import GLib

                    GLib.idle_add(lambda: self.icon.run_detached() and False)
                else:
                    threading.Thread(target=self.icon.run, daemon=True).start()
            else:
                self.icon.run_detached()  # Windows e X11: o próprio pystray abre uma thread
            return True
        except Exception as exc:
            log.warning("bandeja indisponível: %s", exc)
            self.icon = None
            return False

    def stop_tray(self):
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass

    def run_without_window(self) -> int:
        """Modo sem janela (navegador): o ícone toma o laço principal."""
        self.start_loop()
        try:
            self.icon = self._make_icon()
            self.icon.run()
        except Exception as exc:  # sem bandeja (ex.: Linux sem AppIndicator): segue no terminal
            self.icon = None
            log.warning("bandeja indisponível (%s); coleta e avisos seguem ativos. Ctrl+C para sair.", exc)
            print(f"Monitor de notícias ativo. Leitor em {self.url}  (Ctrl+C para sair)")
            try:
                self.stop.wait()
            except KeyboardInterrupt:
                pass
        return 0


def summary_text(items: list[dict], critical: int) -> str:
    games = sum(1 for i in items if i["category"] == config.GAMES)
    tech = len(items) - games
    total = len(items)
    text = f"{total} notícia{'s' if total > 1 else ''} nova{'s' if total > 1 else ''}"
    if critical:
        text += f", {critical} crítica{'s' if critical > 1 else ''}"
    if games and tech:
        text += f" ({tech} de tecnologia, {games} de games)"
    elif games:
        text += " de games"
    titles = [i["title"][:70] for i in items[:2]]
    return text + ". " + " · ".join(titles)
