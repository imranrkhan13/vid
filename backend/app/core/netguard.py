"""Outbound request guard for user-supplied endpoints: resolve once, refuse non-public addresses, and connect to that exact
address (with the original Host header and TLS server name). Validation and the connection use the same lookup, so a DNS
answer that changes between the two (DNS rebinding) cannot reach a private address."""
from __future__ import annotations
import asyncio
import ipaddress
import socket

import httpx

from app.core.config import settings


class BlockedAddress(Exception):
    pass


def _public(ip: str) -> bool:
    a = ipaddress.ip_address(ip)
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    return not (a.is_private or a.is_loopback or a.is_link_local or a.is_reserved or a.is_multicast or a.is_unspecified)


async def resolve_public(host: str, port: int) -> str:
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise BlockedAddress("host does not resolve") from e
    ips = [i[4][0] for i in infos]
    if not ips or not all(_public(ip) for ip in ips):
        raise BlockedAddress("host resolves to a non-public address")
    return ips[0]


class PinnedTransport(httpx.AsyncBaseTransport):
    """Per request: resolve, validate every answer, then connect to the first validated IP."""

    def __init__(self):
        self._inner = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if settings.allow_private_provider_hosts:        # local tests only; never set in production
            return await self._inner.handle_async_request(request)
        host = request.url.host
        try:
            ipaddress.ip_address(host)
            ip = host if _public(host) else None
        except ValueError:
            ip = await resolve_public(host, request.url.port or (443 if request.url.scheme == "https" else 80))
        if ip is None:
            raise BlockedAddress("non-public address")
        original = request.url.host
        request.url = request.url.copy_with(host=ip)
        request.headers["Host"] = original if not request.url.port else f"{original}:{request.url.port}"
        request.extensions = {**request.extensions, "sni_hostname": original.encode()}
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def pinned_client(**kw) -> httpx.AsyncClient:
    kw.setdefault("follow_redirects", False)
    return httpx.AsyncClient(transport=PinnedTransport(), **kw)
