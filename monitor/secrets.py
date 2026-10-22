"""Guarda a senha do e-mail fora dos arquivos de configuração.

Usa o cofre do sistema (Gerenciador de Credenciais no Windows, Secret Service
no Linux) via ``keyring``. Se o cofre não estiver disponível, a senha pode
vir da variável de ambiente ``MONITOR_SMTP_PASSWORD``.
"""

from __future__ import annotations

import os
from functools import lru_cache

SERVICE = "monitor-noticias"
ENV_VAR = "MONITOR_SMTP_PASSWORD"


@lru_cache(maxsize=1)
def _keyring():
    try:
        import keyring
        from keyring.backends import fail

        if isinstance(keyring.get_keyring(), fail.Keyring):
            return None
        return keyring
    except Exception:
        return None


def keyring_available() -> bool:
    return _keyring() is not None


def get_password(username: str) -> str | None:
    if os.environ.get(ENV_VAR):
        return os.environ[ENV_VAR]
    kr = _keyring()
    if kr and username:
        try:
            return kr.get_password(SERVICE, username)
        except Exception:
            return None
    return None


def set_password(username: str, password: str) -> bool:
    """Salva a senha no cofre. Devolve False se não houver cofre disponível."""
    kr = _keyring()
    if not kr or not username:
        return False
    try:
        kr.set_password(SERVICE, username, password)
        return True
    except Exception:
        return False


def has_password(username: str) -> bool:
    return bool(get_password(username))
