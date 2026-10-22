"""Leitura e gravação das configurações e da lista de fontes.

Os arquivos do usuário ficam na pasta de dados (``data/`` ao lado do programa,
ou a pasta definida em ``MONITOR_DATA_DIR``). Na primeira execução eles são
copiados de ``monitor/defaults``.
"""

from __future__ import annotations

import copy
import os
import re
import shutil
import sys
import threading
from pathlib import Path

import yaml

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULTS_DIR = PACKAGE_DIR / "defaults"

SOURCE_TYPES = ["rss", "cisa_kev", "nvd", "github_advisories", "scrape"]
CATEGORIES = ["seguranca", "tecnologia", "games"]
GAMES = "games"  # categoria da área de games, separada das notícias de tecnologia


def area_of(category: str | None) -> str:
    """Área do leitor: "games" ou "tech" (segurança e tecnologia ficam juntas)."""
    return "games" if category == GAMES else "tech"


EMAIL_PRESETS = {
    "outlook": {"smtp_host": "smtp-mail.outlook.com", "smtp_port": 587, "security": "starttls"},
    "office365": {"smtp_host": "smtp.office365.com", "smtp_port": 587, "security": "starttls"},
    "gmail": {"smtp_host": "smtp.gmail.com", "smtp_port": 587, "security": "starttls"},
    "yahoo": {"smtp_host": "smtp.mail.yahoo.com", "smtp_port": 465, "security": "ssl"},
    "custom": {},
}

_lock = threading.Lock()


def is_android() -> bool:
    """Rodando dentro do app Android (definido pelo app em android/monitornoticias)."""
    return os.environ.get("MONITOR_PLATFORM") == "android"


INSTALL_MARK = "instalacao.ini"  # gravado pelo instalador do Windows ao lado do .exe
PORTABLE_NOTE = "versao-portatil.txt"  # onde a versão .zip guarda os dados (para migrar)
MOVED_NOTE = "DADOS-MOVIDOS.txt"


def is_installed() -> bool:
    """Executável instalado pelo instalador (e não a pasta extraída do .zip)."""
    return bool(getattr(sys, "frozen", False)) and (Path(sys.executable).resolve().parent / INSTALL_MARK).exists()


def user_data_dir() -> Path:
    """Pasta de dados do usuário no sistema (LOCALAPPDATA no Windows)."""
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share")
    return base / "MonitorNoticias"


def _default_data_dir() -> Path:
    if not getattr(sys, "frozen", False):
        return PACKAGE_DIR.parent / "data"
    if is_installed():
        # Instalado: o programa pode ser trocado por uma versão nova a qualquer
        # momento, então os dados ficam na pasta do usuário, longe dele.
        return user_data_dir()
    # Executável: a pasta "data" fica ao lado do .exe, para ser fácil de achar e
    # copiar. Se ali não der para gravar (ex.: Arquivos de Programas), usa a pasta do usuário.
    beside = Path(sys.executable).resolve().parent / "data"
    try:
        beside.mkdir(parents=True, exist_ok=True)
        probe = beside / ".teste-escrita"
        probe.write_text("ok")
        probe.unlink()
        return beside
    except OSError:
        return user_data_dir()


def data_dir() -> Path:
    path = Path(os.environ.get("MONITOR_DATA_DIR") or _default_data_dir())
    path.mkdir(parents=True, exist_ok=True)
    return path


def remember_portable() -> None:
    """A versão .zip anota onde estão os dados, para a versão instalada achar depois."""
    if not getattr(sys, "frozen", False) or is_installed() or os.environ.get("MONITOR_DATA_DIR"):
        return
    folder = data_dir().resolve()
    target = user_data_dir()
    if folder == target.resolve():
        return
    try:
        target.mkdir(parents=True, exist_ok=True)
        (target / PORTABLE_NOTE).write_text(str(folder), encoding="utf-8")
    except OSError:
        pass


migration_status = ""  # o que a migração fez ou por que não fez (vai para o log)


def migrate_portable(target: Path | None = None) -> Path | None:
    """Na primeira vez da versão instalada, traz os dados da versão .zip.

    Copia (não move) tudo menos os pacotes de atualização baixados e deixa um
    aviso na pasta antiga. Só roda se a pasta nova ainda não tem banco.
    """
    global migration_status
    target = target or data_dir()
    note = target / PORTABLE_NOTE
    if not note.exists():
        migration_status = f"sem anotação da versão .zip em {target}"
        return None
    if (target / "monitor.db").exists():
        migration_status = f"{target} já tem banco; dados da versão .zip não copiados"
        return None
    source = Path(note.read_text(encoding="utf-8").strip())
    # Banco ou configurações: quem só abriu a versão .zip uma vez pode não ter banco ainda.
    has_data = any((source / name).exists() for name in ("monitor.db", "config.yaml", "sources.yaml"))
    if not has_data or source.resolve() == target.resolve():
        migration_status = f"nada para copiar de {source}"
        return None
    shutil.copytree(source, target, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("atualizacoes", ".teste-escrita", MOVED_NOTE))
    note.unlink(missing_ok=True)
    try:
        (source / MOVED_NOTE).write_text(
            f"Os dados desta pasta foram copiados para a versão instalada, em:\n{target}\n\n"
            "Esta pasta (e a do programa .zip) pode ser apagada.\n", encoding="utf-8")
    except OSError:
        pass
    return source


def _user_file(name: str) -> Path:
    path = data_dir() / name
    if not path.exists():
        shutil.copy(DEFAULTS_DIR / name, path)
    return path


def _read_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _write_yaml(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)
    tmp.replace(path)


def _merge(defaults: dict, user: dict) -> dict:
    """Completa as configurações do usuário com chaves novas dos padrões."""
    result = copy.deepcopy(defaults)
    for key, value in (user or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


# --- Configurações gerais -------------------------------------------------

def load_config() -> dict:
    with _lock:
        defaults = _read_yaml(DEFAULTS_DIR / "config.yaml")
        return _merge(defaults, _read_yaml(_user_file("config.yaml")))


def save_config(config: dict) -> None:
    with _lock:
        _write_yaml(_user_file("config.yaml"), config)


# --- Fontes ---------------------------------------------------------------

# Pacotes de fontes padrão que chegaram depois da primeira versão. Quem já tem
# um sources.yaml recebe cada pacote uma vez só; se depois remover uma fonte
# dele, ela não volta.
SEEDS = {"games": lambda source: source.get("category") == GAMES}


def _seed(data: dict) -> bool:
    seeded = list(data.get("seeded") or [])
    missing = [name for name in SEEDS if name not in seeded]
    if not missing:
        return False
    defaults = _read_yaml(DEFAULTS_DIR / "sources.yaml").get("sources", [])
    sources = data.setdefault("sources", [])
    existing = {s.get("id") for s in sources}
    for name in missing:
        sources += [copy.deepcopy(s) for s in defaults if SEEDS[name](s) and s["id"] not in existing]
        seeded.append(name)
    data["seeded"] = seeded
    return True


def load_sources() -> list[dict]:
    with _lock:
        path = _user_file("sources.yaml")
        data = _read_yaml(path)
        if _seed(data):
            _write_yaml(path, data)
        return data.get("sources", [])


def save_sources(sources: list[dict]) -> None:
    with _lock:
        path = _user_file("sources.yaml")
        seeded = _read_yaml(path).get("seeded") or []
        _write_yaml(path, {"sources": sources, "seeded": seeded})


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "fonte"


def validate_source(source: dict) -> list[str]:
    """Devolve a lista de problemas encontrados (vazia se estiver tudo certo)."""
    problems = []
    if not str(source.get("name", "")).strip():
        problems.append("Informe um nome.")
    url = str(source.get("url", "")).strip()
    if not re.match(r"^https?://", url):
        problems.append("A URL precisa começar com http:// ou https://.")
    if source.get("type") not in SOURCE_TYPES:
        problems.append("Tipo de fonte inválido.")
    if source.get("category") not in CATEGORIES:
        problems.append("Categoria inválida.")
    if source.get("type") == "scrape":
        for key in ("item_selector", "title_selector"):
            if not str(source.get(key, "")).strip():
                problems.append(f"Para raspagem, preencha '{key}'.")
    return problems


def upsert_source(source: dict, original_id: str | None = None) -> dict:
    """Inclui uma fonte nova ou atualiza uma existente."""
    sources = load_sources()
    clean = {k: v for k, v in source.items() if v not in (None, "")}
    clean["enabled"] = bool(source.get("enabled", True))
    if original_id:
        clean["id"] = original_id
        sources = [clean if s["id"] == original_id else s for s in sources]
    else:
        base = slugify(clean.get("id") or clean["name"])
        existing = {s["id"] for s in sources}
        new_id, n = base, 2
        while new_id in existing:
            new_id, n = f"{base}-{n}", n + 1
        clean["id"] = new_id
        sources.append(clean)
    save_sources(sources)
    return clean


def delete_source(source_id: str) -> None:
    save_sources([s for s in load_sources() if s["id"] != source_id])


def get_source(source_id: str) -> dict | None:
    return next((s for s in load_sources() if s["id"] == source_id), None)
