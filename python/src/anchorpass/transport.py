from __future__ import annotations

import os
import ssl
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import urlsplit

import aiohttp
import pycurl
from aiohttp_socks import ProxyConnector

from .cookies import MAX_COOKIE_BYTES
from .errors import AnchorpassError

READ_CHUNK_BYTES = 16384
CONNECT_TIMEOUT = 5.0
SYNC_CACHED_CONNECTIONS = 1


class CurlPool:
    def __init__(
        self, proxy: str, limit: int, ca_file: str | None, proxy_ca_file: str | None
    ) -> None:
        self._proxy = proxy
        self._limit = limit
        self._ca_file = ca_file
        self._proxy_ca_file = proxy_ca_file
        self._guard = threading.Lock()
        self._idle: list[pycurl.Curl] = []
        self._created = 0
        self._closed = False

    @contextmanager
    def lease(self) -> Iterator[pycurl.Curl]:
        with self._guard:
            if self._closed:
                raise AnchorpassError("ClientClosed", "client")
            if self._idle:
                handle = self._idle.pop()
            elif self._created < self._limit:
                handle = pycurl.Curl()
                self._created += 1
            else:
                raise AnchorpassError("ResourceLimit", "client")
        try:
            handle.setopt(pycurl.PROXY, self._proxy)
            handle.setopt(pycurl.NOPROXY, "")
            handle.setopt(pycurl.FOLLOWLOCATION, False)
            handle.setopt(pycurl.ACCEPT_ENCODING, "")
            handle.setopt(pycurl.NOSIGNAL, True)
            handle.setopt(pycurl.MAXCONNECTS, SYNC_CACHED_CONNECTIONS)
            if self._ca_file is not None:
                handle.setopt(pycurl.CAINFO, os.fsencode(self._ca_file))
            if self._proxy_ca_file is not None:
                handle.setopt(pycurl.PROXY_CAINFO, os.fsencode(self._proxy_ca_file))
            yield handle
        finally:
            # curl_easy_reset keeps connections while releasing headers, POST data
            # and callbacks holding operation response buffers.
            handle.reset()
            with self._guard:
                close = self._closed
                if close:
                    self._created -= 1
                else:
                    self._idle.append(handle)
            if close:
                handle.close()

    def close(self) -> None:
        with self._guard:
            self._closed = True
            idle, self._idle = self._idle, []
            self._created -= len(idle)
        for handle in idle:
            handle.close()


def _ssl_context(ca_file: str | None, ca_data: bytes | None) -> ssl.SSLContext:
    if ca_data is None:
        return ssl.create_default_context(cafile=ca_file)
    # ssl accepts PEM as text and DER as bytes. Custom roots replace system roots.
    data = ca_data.decode("ascii") if b"-----BEGIN CERTIFICATE-----" in ca_data else ca_data
    return ssl.create_default_context(cadata=data)


def create_session(
    proxy: str,
    max_connections: int,
    *,
    ca_file: str | None = None,
    proxy_ca_file: str | None = None,
    ca_data: bytes | None = None,
    proxy_ca_data: bytes | None = None,
    resolver: aiohttp.abc.AbstractResolver | None = None,
) -> aiohttp.ClientSession:
    origin_ssl = _ssl_context(ca_file, ca_data)
    connector: aiohttp.BaseConnector
    if proxy:
        parsed_proxy = urlsplit(proxy)
        scheme = parsed_proxy.scheme
        proxy_ssl = _ssl_context(proxy_ca_file, proxy_ca_data) if scheme == "https" else None
        connector = ProxyConnector.from_url(
            parsed_proxy._replace(
                scheme={"socks5h": "socks5", "https": "http"}.get(scheme, scheme)
            ).geturl(),
            rdns=scheme != "socks5",
            proxy_ssl=proxy_ssl,
            ssl=origin_ssl,
            limit=max_connections,
            limit_per_host=max_connections,
        )
    else:
        connector = aiohttp.TCPConnector(
            ssl=origin_ssl,
            limit=max_connections,
            limit_per_host=max_connections,
            resolver=resolver,
        )
    return aiohttp.ClientSession(
        connector=connector,
        trust_env=False,
        cookie_jar=aiohttp.DummyCookieJar(),
        read_bufsize=READ_CHUNK_BYTES,
        max_field_size=MAX_COOKIE_BYTES,
    )


def request_timeout(remaining: float) -> aiohttp.ClientTimeout:
    return aiohttp.ClientTimeout(
        total=remaining,
        sock_connect=min(CONNECT_TIMEOUT, remaining),
        sock_read=remaining,
    )
