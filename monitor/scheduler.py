"""Agendamento da coleta diária e do modo residente.

Coleta diária — Windows: Agendador de Tarefas (schtasks). Linux: crontab.
Iniciar com o sistema — Windows: chave Run do registro do usuário. Linux: ~/.config/autostart.
Também avisa quando a coleta agendada de hoje não aconteceu e diz se ela já venceu.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path

from . import config, db

TASK_NAME = "MonitorNoticias"
RESIDENT_TASK = "MonitorNoticias-residente"  # versões até 0.4: tarefa ao entrar no Windows
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "MonitorNoticias"
CRON_MARK = "# monitor-noticias"
WINDOWS = sys.platform == "win32"


def program_command(*args: str) -> list[str]:
    """Comando que inicia este programa (executável ou Python + run.py)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    python = Path(sys.executable)
    if WINDOWS:
        quiet = python.with_name("pythonw.exe")  # sem janela de console piscando
        python = quiet if quiet.exists() else python
    return [str(python), str(config.PACKAGE_DIR.parent / "run.py"), *args]


def _run(cmd: list[str], input_text: str | None = None) -> subprocess.CompletedProcess:
    kwargs = {"capture_output": True, "text": True, "input": input_text}
    if WINDOWS:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(cmd, **kwargs)


def _parse_time(value: str) -> dtime:
    try:
        hour, minute = (int(x) for x in str(value or "07:00").split(":")[:2])
        return dtime(hour % 24, minute % 60)
    except ValueError:
        return dtime(7, 0)


def _quote(parts: list[str]) -> str:
    return " ".join(f'"{p}"' if " " in p else p for p in parts)


# --- Coleta diária ------------------------------------------------------------

def install(cfg: dict) -> str:
    if config.is_android():
        raise RuntimeError("No Android a coleta roda ao abrir o app. Para avisos no celular, use o Telegram.")
    sched = cfg.get("schedule", {})
    at = _parse_time(sched.get("time"))
    args = ["--so-coletar"] + (["--email"] if sched.get("send_email") else [])
    command = program_command(*args)
    weekdays = sched.get("weekdays_only", True)
    if WINDOWS:
        cmd = ["schtasks", "/Create", "/F", "/TN", TASK_NAME, "/TR", _quote(command), "/ST", at.strftime("%H:%M")]
        cmd += ["/SC", "WEEKLY", "/D", "MON,TUE,WED,THU,FRI"] if weekdays else ["/SC", "DAILY"]
        result = _run(cmd)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "schtasks falhou.")
    else:
        line = f"{at.minute} {at.hour} * * {'1-5' if weekdays else '*'} {_quote(command)} >/dev/null 2>&1 {CRON_MARK}"
        _write_cron(line)
    sched["installed"] = True
    cfg["schedule"] = sched
    config.save_config(cfg)
    days = "de segunda a sexta" if weekdays else "todos os dias"
    return f"Coleta agendada para {at.strftime('%H:%M')} {days}."


def uninstall(cfg: dict) -> str:
    if WINDOWS:
        _run(["schtasks", "/Delete", "/F", "/TN", TASK_NAME])
    else:
        _write_cron(None)
    cfg.setdefault("schedule", {})["installed"] = False
    config.save_config(cfg)
    return "Agendamento removido."


def _read_cron() -> list[str]:
    try:
        result = _run(["crontab", "-l"])
    except FileNotFoundError:
        raise RuntimeError("O comando crontab não está instalado (sudo apt install cron).")
    return result.stdout.splitlines() if result.returncode == 0 else []


def _write_cron(line: str | None) -> None:
    lines = [l for l in _read_cron() if CRON_MARK not in l]
    if line:
        lines.append(line)
    result = _run(["crontab", "-"], "\n".join(lines) + "\n")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Não foi possível gravar o crontab.")


def is_installed() -> bool:
    if config.is_android():
        return False
    try:
        if WINDOWS:
            return _run(["schtasks", "/Query", "/TN", TASK_NAME]).returncode == 0
        return any(CRON_MARK in l for l in _read_cron())
    except (OSError, RuntimeError):
        return False


def status(cfg: dict | None = None, now: datetime | None = None) -> dict:
    """Diz se o agendamento existe e se a coleta de hoje deveria ter rodado e não rodou."""
    cfg = cfg or config.load_config()
    if config.is_android():
        return {"installed": False, "time": "", "missed": False, "android": True,
                "message": "No Android a coleta roda quando você abre o app."}
    sched = cfg.get("schedule", {})
    installed = is_installed()
    at = _parse_time(sched.get("time"))
    now = now or datetime.now().astimezone()
    result = {"installed": installed, "time": at.strftime("%H:%M"), "missed": False,
              "message": f"Coleta agendada para {at.strftime('%H:%M')}." if installed else "Sem agendamento."}
    expected = installed or sched.get("installed")
    if not expected or not sched.get("alert_missed", True):
        return result
    if sched.get("weekdays_only", True) and now.weekday() >= 5:
        return result
    due = now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    if now < due + timedelta(minutes=30):
        return result
    last = last_run_time()
    if last is None or last < due - timedelta(minutes=5):
        hours = (now - last).total_seconds() / 3600 if last else None
        result["missed"] = True
        result["message"] = (f"A coleta das {at.strftime('%H:%M')} de hoje não rodou."
                             + (f" Última coleta há {hours:.0f} h." if hours is not None else ""))
        if not installed:
            result["fix"] = "O agendamento sumiu do sistema: clique em \"Agendar coleta diária\" de novo."
        else:
            result["fix"] = ("Causas comuns: computador desligado ou em suspensão no horário, sem internet, "
                             "ou o programa mudou de pasta (agende de novo).")
    return result


def last_run_time() -> datetime | None:
    run = db.last_run()
    if not run:
        return None
    return datetime.strptime(run["started_at"][:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def last_slot(cfg: dict, now: datetime | None = None) -> datetime:
    """Horário agendado mais recente que já chegou (hoje ou num dia útil anterior)."""
    sched = cfg.get("schedule", {})
    at = _parse_time(sched.get("time"))
    now = now or datetime.now().astimezone()
    slot = now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    if slot > now:
        slot -= timedelta(days=1)
    while sched.get("weekdays_only", True) and slot.weekday() >= 5:
        slot -= timedelta(days=1)
    return slot


def collection_due(cfg: dict, now: datetime | None = None) -> bool:
    """True quando há coleta agendada cujo horário já passou e ela ainda não rodou.

    Usado ao iniciar com o sistema (o computador estava desligado no horário) e
    pelo programa na bandeja, que roda a coleta do dia sozinho.
    """
    if config.is_android() or not cfg.get("schedule", {}).get("installed"):
        return False
    last = last_run_time()
    return last is None or last < last_slot(cfg, now) - timedelta(minutes=1)


# --- Iniciar com o sistema ------------------------------------------------------

def _autostart_file() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "autostart" / "monitor-noticias.desktop"


def _old_task_installed() -> bool:
    return WINDOWS and _run(["schtasks", "/Query", "/TN", RESIDENT_TASK]).returncode == 0


def _registry(command: str | None) -> None:
    """Grava (ou apaga, com None) o programa na lista do que abre ao entrar no Windows."""
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if command is None:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass
        else:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, command)


def _registry_has_value() -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
            return True
    except OSError:
        return False


def install_resident() -> str:
    """Faz o programa abrir sozinho (na bandeja) quando você entra no sistema."""
    if config.is_android():
        raise RuntimeError("Iniciar com o sistema é só para o computador.")
    command = program_command("--inicio-sistema")
    if WINDOWS:
        try:
            _registry(subprocess.list2cmdline(command))
        except OSError as exc:
            raise RuntimeError(f"Não foi possível gravar no registro do Windows: {exc}")
        if _old_task_installed():  # troca a tarefa antiga do modo de alertas pela chave do registro
            _run(["schtasks", "/Delete", "/F", "/TN", RESIDENT_TASK])
    else:
        path = _autostart_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        icon = config.PACKAGE_DIR / "static" / "icone.png"
        path.write_text("[Desktop Entry]\nType=Application\nName=Monitor de notícias\n"
                        f"Exec={_quote(command)}\nIcon={icon}\nX-GNOME-Autostart-enabled=true\n"
                        "X-GNOME-Autostart-Delay=10\n", encoding="utf-8")
    return "O programa vai abrir sozinho, perto do relógio, quando você entrar no sistema."


def uninstall_resident() -> str:
    if WINDOWS:
        try:
            _registry(None)
        except OSError:
            pass
        _run(["schtasks", "/Delete", "/F", "/TN", RESIDENT_TASK])
    else:
        _autostart_file().unlink(missing_ok=True)
    return "O programa não vai mais abrir sozinho com o sistema."


def resident_installed() -> bool:
    """True quando o programa abre sozinho ao entrar no sistema."""
    if config.is_android():
        return False
    try:
        if WINDOWS:
            return _registry_has_value() or _old_task_installed()
        return _autostart_file().exists()
    except OSError:
        return False
