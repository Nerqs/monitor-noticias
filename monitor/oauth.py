"""Login moderno (OAuth2) da Microsoft para enviar e-mail pelo Outlook.

A Microsoft está desligando o login por senha no SMTP. Com OAuth2 o programa
nunca vê sua senha: você entra na página da Microsoft, digita um código curto e
o programa recebe uma autorização que só permite enviar e-mail. Essa
autorização (refresh token) fica no cofre do sistema.

Precisa de um "ID do aplicativo" registrado no portal do Azure (grátis, uma
vez só). O passo a passo está no README, seção E-mail.
"""

from __future__ import annotations

import threading
import time

import httpx

from . import config, secrets

SCOPE = "https://outlook.office.com/SMTP.Send offline_access"
SECRET = "ms_refresh_token"
_state_lock = threading.Lock()
_flow: dict = {}
_access: dict = {"token": None, "expires": 0.0}


class OAuthError(Exception):
    pass


def _settings(cfg: dict | None = None) -> tuple[str, str]:
    email_cfg = (cfg or config.load_config()).get("email", {})
    client_id = str(email_cfg.get("oauth_client_id") or "").strip()
    if not client_id:
        raise OAuthError("Informe o ID do aplicativo (Azure) em Configurações → E-mail. O passo a passo está no README.")
    tenant = str(email_cfg.get("oauth_tenant") or "").strip() or (
        "organizations" if email_cfg.get("provider") == "office365" else "consumers")
    return client_id, tenant


def _endpoint(tenant: str, path: str) -> str:
    return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/{path}"


def has_refresh_token() -> bool:
    return bool(secrets.get_secret(SECRET))


def start_device_flow(cfg: dict | None = None) -> dict:
    """Pede um código à Microsoft e começa a esperar você autorizar."""
    client_id, tenant = _settings(cfg)
    try:
        response = httpx.post(_endpoint(tenant, "devicecode"), data={"client_id": client_id, "scope": SCOPE},
                              timeout=20)
    except httpx.HTTPError as exc:
        raise OAuthError(f"Não foi possível falar com a Microsoft: {exc}")
    data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    if response.status_code != 200 or "device_code" not in data:
        raise OAuthError(f"A Microsoft recusou o pedido: {data.get('error_description') or response.text[:200]}")
    with _state_lock:
        _flow.clear()
        _flow.update(status="pending", user_code=data["user_code"], url=data["verification_uri"],
                     expires=time.time() + int(data.get("expires_in", 900)), message="")
    threading.Thread(target=_poll, args=(client_id, tenant, data["device_code"], int(data.get("interval", 5))),
                     daemon=True).start()
    return flow_status()


def _poll(client_id: str, tenant: str, device_code: str, interval: int) -> None:
    while time.time() < _flow.get("expires", 0):
        time.sleep(interval)
        try:
            response = httpx.post(_endpoint(tenant, "token"), timeout=20, data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": client_id, "device_code": device_code})
            data = response.json()
        except (httpx.HTTPError, ValueError):
            continue
        error = data.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        with _state_lock:
            if error:
                _flow.update(status="error", message=data.get("error_description", error).split("\r\n")[0])
                return
            if not data.get("refresh_token"):
                _flow.update(status="error", message="A Microsoft não devolveu autorização permanente.")
                return
            if not secrets.set_secret(SECRET, data["refresh_token"]):
                _flow.update(status="error", message="O cofre de senhas do sistema não está disponível.")
                return
            _access.update(token=data.get("access_token"), expires=time.time() + int(data.get("expires_in", 3600)) - 60)
            _flow.update(status="ok", message="Conta Microsoft conectada.")
            return
    with _state_lock:
        _flow.update(status="error", message="O código expirou. Clique em Conectar de novo.")


def flow_status() -> dict:
    with _state_lock:
        return {k: v for k, v in _flow.items() if k != "expires"} or {"status": "idle"}


def access_token(cfg: dict | None = None) -> str:
    if _access["token"] and time.time() < _access["expires"]:
        return _access["token"]
    refresh = secrets.get_secret(SECRET)
    if not refresh:
        raise OAuthError("Conta Microsoft não conectada. Clique em \"Conectar conta Microsoft\" nas configurações.")
    client_id, tenant = _settings(cfg)
    try:
        response = httpx.post(_endpoint(tenant, "token"), timeout=20, data={
            "grant_type": "refresh_token", "client_id": client_id, "refresh_token": refresh, "scope": SCOPE})
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OAuthError(f"Não foi possível renovar o login da Microsoft: {exc}")
    if "access_token" not in data:
        raise OAuthError("A Microsoft recusou a renovação do login "
                         f"({data.get('error_description', data.get('error', '?')).splitlines()[0]}). Conecte de novo.")
    if data.get("refresh_token") and data["refresh_token"] != refresh:
        secrets.set_secret(SECRET, data["refresh_token"])
    _access.update(token=data["access_token"], expires=time.time() + int(data.get("expires_in", 3600)) - 60)
    return data["access_token"]


def xoauth2_string(username: str, cfg: dict | None = None) -> str:
    return f"user={username}\x01auth=Bearer {access_token(cfg)}\x01\x01"


def disconnect() -> None:
    secrets.set_secret(SECRET, None)
    _access.update(token=None, expires=0.0)
    with _state_lock:
        _flow.clear()
