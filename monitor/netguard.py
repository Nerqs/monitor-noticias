"""Proteções de rede da coleta.

Bloqueia endereços da sua rede interna (localhost, 192.168.x, 10.x...) para que
um feed, ou um redirecionamento dele, não faça o programa acessar o próprio
servidor local, o roteador ou outros aparelhos da casa. Quem precisar monitorar
um site da intranet libera em Configurações → Coleta.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit


class BlockedAddress(Exception):
    pass


def _is_internal(ip: ipaddress._BaseAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
            or ip.is_reserved or ip.is_unspecified)


def check_host(host: str) -> None:
    """Levanta ``BlockedAddress`` se o nome resolve para um endereço interno."""
    host = (host or "").strip("[]").lower()
    if not host:
        raise BlockedAddress("Endereço sem nome de site.")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan")):
        raise BlockedAddress(f"'{host}' é um endereço da rede local.")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        addresses = [literal]
    else:
        try:
            infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        except socket.gaierror:
            return  # o erro de DNS aparece depois, na própria requisição, com mensagem melhor
        addresses = [ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos]
    for ip in addresses:
        if _is_internal(ip):
            raise BlockedAddress(
                f"'{host}' aponta para {ip}, um endereço da rede interna. Por segurança a coleta não acessa "
                "esses endereços (libere em Configurações → Coleta se for um site da sua intranet)."
            )


def check_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise BlockedAddress("Só são aceitos endereços http:// e https://.")
    check_host(parts.hostname or "")
