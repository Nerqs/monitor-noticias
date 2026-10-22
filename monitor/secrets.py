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


# --- Outros segredos (tokens de chat, webhooks, login Microsoft) ---------------

def _secret_account(name: str) -> str:
    return f"segredo:{name}"


def get_secret(name: str) -> str | None:
    env = os.environ.get(f"MONITOR_{name.upper()}")
    if env:
        return env
    kr = _keyring()
    if kr:
        try:
            return kr.get_password(SERVICE, _secret_account(name))
        except Exception:
            return None
    return None


def set_secret(name: str, value: str | None) -> bool:
    kr = _keyring()
    if not kr:
        return False
    try:
        if value:
            kr.set_password(SERVICE, _secret_account(name), value)
        else:
            try:
                kr.delete_password(SERVICE, _secret_account(name))
            except Exception:
                pass
        return True
    except Exception:
        return False
