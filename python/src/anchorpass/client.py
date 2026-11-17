from __future__ import annotations

import asyncio
import math
import threading
import time
from collections.abc import Generator, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Literal, NoReturn, Self
from urllib.parse import urlencode, urlsplit

import aiohttp
import pycurl
from aiohttp_socks import ProxyConnectionError, ProxyError, ProxyTimeoutError

from .bundle import Bundle, BundleStore
from .cookies import MAX_COOKIE_BYTES, MAX_COOKIES, OperationCookies
from .core import (
    SolveResult,
    anchor_token,
    resolve_origin,
    resolve_version,
    response_token,
    validate_overrides,
)
from .errors import AnchorpassError
from .proxy import normalize_proxy
from .transport import CONNECT_TIMEOUT, READ_CHUNK_BYTES, CurlPool, create_session, request_timeout
from .updates import UpdatePolicy, UpdateWaiter, subscribe

INHERIT = object()
MAX_RESPONSE_BYTES = 1048576


@dataclass(frozen=True)
class _Request:
    stage: str
    method: Literal["GET", "POST"]
    url: str
    body: bytes | None = None
    user_agent: str | None = None


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise AnchorpassError("DeadlineExceeded", "operation")
    return remaining


def _flow(
    anchor_url: str,
    action: str | None,
    bundle: Bundle,
    origin: str,
    started: float,
    bg: str | None,
    user_agent: str | None = None,
    hostname: str | None = None,
) -> Generator[_Request, str, SolveResult]:
    from .core import parse_anchor

    parsed = parse_anchor(anchor_url)
    params = dict(parsed.params)
    params["co"] = resolve_origin(params["co"], hostname)
    if action is None:
        action = params.get("sa")
    if action is not None and (not isinstance(action, str) or not action or len(action) > 256):
        raise AnchorpassError("InvalidInput", "action")
    api = "api" if parsed.endpoint == "api2" else "enterprise"
    script = yield _Request("version", "GET", f"{origin}/recaptcha/{api}.js", user_agent=user_agent)
    version = resolve_version(script)
    params["v"] = version
    if action is not None:
        params["sa"] = action
    html = yield _Request(
        "anchor",
        "GET",
        f"{origin}/recaptcha/{parsed.endpoint}/anchor?{urlencode(params)}",
        user_agent=user_agent,
    )
    fields = dict(bundle.fields)
    if bg is not None:
        fields["bg"] = bg
    fields.update(v=version, c=anchor_token(html), k=params["k"], co=params["co"])
    if action is not None:
        fields["sa"] = action
    text = yield _Request(
        "reload",
        "POST",
        f"{origin}/recaptcha/{parsed.endpoint}/reload?{urlencode({'k': params['k']})}",
        urlencode(fields).encode(),
        user_agent,
    )
    return SolveResult(response_token(text), bundle.revision, version, time.monotonic() - started)


class _Base:
    def __init__(
        self,
        *,
        proxy: str | None = None,
        timeout: float = 30,
        bundle_url: str | None = None,
        updates_proxy: Any = INHERIT,
        cache_path: str | Path | None = None,
        pin: str | None = None,
        offline: bool = False,
        trusted_keys: Mapping[str, str] | None = None,
        max_in_flight: int = 16,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        refresh_interval: float = 3600,
        update_timeout: float = 5,
        ca_bundle: str | None = None,
        proxy_ca_bundle: str | None = None,
    ) -> None:
        self._proxy = normalize_proxy(proxy)
        self._ca_bundle = ca_bundle
        self._proxy_ca_bundle = proxy_ca_bundle
        self._updates_proxy = (
            self._proxy if updates_proxy is INHERIT else normalize_proxy(updates_proxy)
        )
        for value in (timeout, update_timeout):
            if not math.isfinite(value) or value <= 0:
                raise AnchorpassError("InvalidInput", "configuration", "Invalid timeout")
        if not math.isfinite(refresh_interval) or refresh_interval < 0:
            raise AnchorpassError("InvalidInput", "configuration", "Invalid refresh interval")
        if type(max_in_flight) is not int or max_in_flight <= 0:
            raise AnchorpassError("InvalidInput", "configuration", "Invalid concurrency limit")
        if type(max_response_bytes) is not int or max_response_bytes <= 0:
            raise AnchorpassError("InvalidInput", "configuration", "Invalid response limit")
        if bundle_url is not None:
            try:
                if (
                    not isinstance(bundle_url, str)
                    or len(bundle_url) > 65536
                    or any(char.isspace() or ord(char) < 32 for char in bundle_url)
                    or "#" in bundle_url
                ):
                    raise ValueError
                bundle_url.encode("utf-8")
                u = urlsplit(bundle_url)
                if (
                    (
                        u.scheme != "https"
                        and not (
                            u.scheme == "http" and u.hostname in {"127.0.0.1", "localhost", "::1"}
                        )
                    )
                    or not u.hostname
                    or u.username is not None
                    or u.fragment
                ):
                    raise ValueError
                _ = u.port
            except (ValueError, UnicodeError):
                raise AnchorpassError("InvalidInput", "bundle_url") from None
        self.timeout = timeout
        self._bundle_url = bundle_url
        self._offline = offline
        self._store = BundleStore(keys=trusted_keys, cache_path=cache_path, pin=pin)
        self._origin = "https://www.google.com"
        self._max_in_flight = max_in_flight
        self._max_response_bytes = max_response_bytes
        self._refresh_interval = refresh_interval
        self._update_timeout = update_timeout
        self._update_policy = (
            UpdatePolicy.create(
                bundle_url, self._updates_proxy, update_timeout, ca_bundle, proxy_ca_bundle
            )
            if bundle_url is not None and not offline
            else None
        )
        self._next_refresh = 0.0
        self._closed = False
        self._active = 0
        self._guard = threading.Lock()
        self._update_waiters: set[UpdateWaiter] = set()
        self.last_update_error: AnchorpassError | None = None

    @contextmanager
    def _operation(self) -> Iterator[None]:
        with self._guard:
            if self._closed:
                raise AnchorpassError("ClientClosed", "client")
            if self._active >= self._max_in_flight:
                raise AnchorpassError("ResourceLimit", "client")
            self._active += 1
        try:
            yield
        finally:
            with self._guard:
                self._active -= 1

    def _needs_refresh(self, deadline: float, force: bool) -> bool:
        if self._offline or self._bundle_url is None:
            return False
        try:
            self._store.snapshot(_remaining(deadline))
        except AnchorpassError:
            return True
        return force or (self._store.pin is None and time.monotonic() >= self._next_refresh)

    def _update_failed(self, exc: AnchorpassError) -> None:
        self.last_update_error = exc
        self._next_refresh = time.monotonic() + self._refresh_interval

    def _subscribe_update(self) -> UpdateWaiter:
        with self._guard:
            if self._closed:
                raise AnchorpassError("ClientClosed", "client")
            assert self._update_policy is not None
            waiter = subscribe(self._update_policy)
            self._update_waiters.add(waiter)
            return waiter

    def _release_update(self, waiter: UpdateWaiter) -> None:
        with self._guard:
            self._update_waiters.discard(waiter)

    def _accept_update(self, raw: bytes) -> None:
        with self._guard:
            if self._closed:
                raise AnchorpassError("ClientClosed", "client")
            self._store.accept(raw)
            self.last_update_error = None

    def _snapshot(self, deadline: float) -> Bundle:
        with self._guard:
            if self._closed:
                raise AnchorpassError("ClientClosed", "client")
            return self._store.snapshot(_remaining(deadline))

    def _close_updates(self) -> list[UpdateWaiter] | None:
        with self._guard:
            if self._closed:
                return None
            self._closed = True
            waiters = list(self._update_waiters)
            self._update_waiters.clear()
            return waiters


class _Capture:
    def __init__(self, limit: int) -> None:
        self.body = bytearray()
        self.limit = limit
        self.oversized = False
        self.cookies: list[str] = []
        self.cookie_bytes = 0
        self.cookies_oversized = False

    def receive(self, chunk: bytes) -> int:
        if len(self.body) + len(chunk) > self.limit:
            self.oversized = True
            return 0
        self.body.extend(chunk)
        return len(chunk)

    def receive_header(self, line: bytes) -> int:
        if line.startswith(b"HTTP/"):
            self.cookies.clear()
            self.cookie_bytes = 0
        if line.lower().startswith(b"set-cookie:"):
            value = line.split(b":", 1)[1].strip().decode("iso-8859-1")
            self.cookie_bytes += len(value.encode("utf-8"))
            if len(self.cookies) >= MAX_COOKIES or self.cookie_bytes > MAX_COOKIE_BYTES:
                self.cookies_oversized = True
                return 0
            self.cookies.append(value)
        return len(line)


def _request_headers(request: _Request, cookies: OperationCookies) -> dict[str, str]:
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if request.user_agent is not None:
        headers["User-Agent"] = request.user_agent
    cookie_header = cookies.header(request.url, request.stage)
    if cookie_header is not None:
        headers["Cookie"] = cookie_header
    return headers


def _deny_rewind(offset: int, origin: int) -> int:
    # A consumed reload body must never be replayed on a new connection.
    return pycurl.SEEKFUNC_CANTSEEK


def _transfer_error(exc: pycurl.error, capture: _Capture, deadline: float, stage: str) -> NoReturn:
    if capture.oversized:
        raise AnchorpassError("ResourceLimit", stage, "Response body too large") from None
    if capture.cookies_oversized:
        raise AnchorpassError(
            "ResourceLimit", stage, "Response cookies exceed operation limits"
        ) from None
    if time.monotonic() >= deadline or exc.args[0] == pycurl.E_OPERATION_TIMEDOUT:
        raise AnchorpassError("DeadlineExceeded", stage) from None
    raise AnchorpassError("TransportError", stage) from None


class Client(_Base):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._session = CurlPool(
            self._proxy, self._max_in_flight, self._ca_bundle, self._proxy_ca_bundle
        )

    def _fetch(
        self,
        request: _Request,
        deadline: float,
        cookies: OperationCookies,
    ) -> bytes:
        capture = _Capture(self._max_response_bytes)
        headers = _request_headers(request, cookies)
        try:
            with self._session.lease() as handle:
                remaining = _remaining(deadline)
                handle.setopt(pycurl.URL, request.url)
                handle.setopt(pycurl.TIMEOUT_MS, max(1, math.ceil(remaining * 1000)))
                handle.setopt(
                    pycurl.CONNECTTIMEOUT_MS,
                    max(1, math.ceil(min(CONNECT_TIMEOUT, remaining) * 1000)),
                )
                handle.setopt(pycurl.HTTPGET, True)
                if request.method == "POST":
                    data = request.body or b""
                    handle.setopt(pycurl.POST, True)
                    handle.setopt(pycurl.POSTFIELDSIZE, len(data))
                    handle.setopt(pycurl.READFUNCTION, BytesIO(data).read)
                    handle.setopt(pycurl.SEEKFUNCTION, _deny_rewind)
                    if not data:
                        # Empty uploads do not need rewinding, so avoid reused-connection retries.
                        handle.setopt(pycurl.FRESH_CONNECT, True)
                handle.setopt(
                    pycurl.HTTPHEADER, [f"{name}: {value}" for name, value in headers.items()]
                )
                handle.setopt(pycurl.WRITEFUNCTION, capture.receive)
                handle.setopt(pycurl.HEADERFUNCTION, capture.receive_header)
                handle.perform()
                status = handle.getinfo(pycurl.RESPONSE_CODE)
        except pycurl.error as exc:
            _transfer_error(exc, capture, deadline, request.stage)
        if status != 200:
            raise AnchorpassError("HttpStatusError", request.stage, str(status))
        cookies.receive(capture.cookies, request.url, request.stage)
        return bytes(capture.body)

    def _prepare(self, deadline: float, force: bool = False) -> Bundle:
        with self._guard:
            self._store.load_cache()
        if self._needs_refresh(deadline, force):
            waiter: UpdateWaiter | None = None
            try:
                waiter = self._subscribe_update()
                self._accept_update(waiter.wait(_remaining(deadline)))
            except AnchorpassError as exc:
                self._update_failed(exc)
                if force or exc.code == "ClientClosed" or exc.stage in {"cache", "cache_lock"}:
                    raise
            finally:
                if waiter is not None:
                    flight = waiter.release()
                    try:
                        if flight is not None:
                            flight.join()
                    finally:
                        self._release_update(waiter)
            self._next_refresh = time.monotonic() + self._refresh_interval
        return self._snapshot(deadline)

    def prepare(self) -> Bundle:
        with self._operation():
            return self._prepare(time.monotonic() + self.timeout)

    def refresh(self) -> Bundle:
        with self._operation():
            return self._prepare(time.monotonic() + self.timeout, force=True)

    def load_bg(self) -> str:
        """Load the current verified bg using the configured update policy."""
        return self.prepare().fields["bg"]

    def solve(
        self,
        anchor_url: str,
        *,
        action: str | None = None,
        bg: str | None = None,
        user_agent: str | None = None,
        hostname: str | None = None,
    ) -> SolveResult:
        started = time.monotonic()
        deadline = started + self.timeout
        validate_overrides(anchor_url, action, bg, user_agent, hostname)
        with self._operation():
            bundle = self._prepare(deadline)
            flow = _flow(
                anchor_url, action, bundle, self._origin, started, bg, user_agent, hostname
            )
            cookies = OperationCookies()
            request = next(flow)
            while True:
                try:
                    reply = self._fetch(request, deadline, cookies).decode("utf-8")
                except UnicodeError:
                    raise AnchorpassError("ProtocolError", request.stage) from None
                try:
                    request = flow.send(reply)
                except StopIteration as finished:
                    _remaining(deadline)
                    return finished.value  # type: ignore[no-any-return]

    def close(self) -> None:
        waiters = self._close_updates()
        if waiters is not None:
            for waiter in waiters:
                flight = waiter.release(closed=True)
                if flight is not None:
                    flight.join()
            self._session.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class AsyncClient(_Base):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._session: aiohttp.ClientSession | None = None

    async def _fetch(
        self,
        request: _Request,
        deadline: float,
        cookies: OperationCookies,
    ) -> bytes:
        headers = _request_headers(request, cookies)
        body = bytearray()
        try:
            async with asyncio.timeout(_remaining(deadline)):
                if self._session is None:
                    self._session = create_session(
                        self._proxy,
                        self._max_in_flight,
                        ca_file=self._ca_bundle,
                        proxy_ca_file=self._proxy_ca_bundle,
                    )
                async with self._session.request(
                    request.method,
                    request.url,
                    data=request.body,
                    headers=headers,
                    allow_redirects=False,
                    timeout=request_timeout(_remaining(deadline)),
                ) as response:
                    if response.status != 200:
                        raise AnchorpassError(
                            "HttpStatusError", request.stage, str(response.status)
                        )
                    async for chunk in response.content.iter_chunked(READ_CHUNK_BYTES):
                        if len(body) + len(chunk) > self._max_response_bytes:
                            raise AnchorpassError(
                                "ResourceLimit", request.stage, "Response body too large"
                            )
                        body.extend(chunk)
                    cookies.receive(
                        response.headers.getall("set-cookie", []), request.url, request.stage
                    )
        except (TimeoutError, ProxyTimeoutError):
            raise AnchorpassError("DeadlineExceeded", request.stage) from None
        except (aiohttp.ClientError, ProxyError, ProxyConnectionError, OSError, ValueError):
            raise AnchorpassError("TransportError", request.stage) from None
        return bytes(body)

    async def _prepare(self, deadline: float, force: bool = False) -> Bundle:
        with self._guard:
            self._store.load_cache()
        if self._needs_refresh(deadline, force):
            waiter: UpdateWaiter | None = None
            try:
                waiter = self._subscribe_update()
                raw = await waiter.await_result(_remaining(deadline))
                self._accept_update(raw)
            except AnchorpassError as exc:
                self._update_failed(exc)
                if force or exc.code == "ClientClosed" or exc.stage in {"cache", "cache_lock"}:
                    raise
            finally:
                if waiter is not None:
                    flight = waiter.release()
                    try:
                        if flight is not None:
                            await flight.ajoin()
                    finally:
                        self._release_update(waiter)
            self._next_refresh = time.monotonic() + self._refresh_interval
        return self._snapshot(deadline)

    async def prepare(self) -> Bundle:
        with self._operation():
            return await self._prepare(time.monotonic() + self.timeout)

    async def refresh(self) -> Bundle:
        with self._operation():
            return await self._prepare(time.monotonic() + self.timeout, force=True)

    async def load_bg(self) -> str:
        """Load the current verified bg using the configured update policy."""
        return (await self.prepare()).fields["bg"]

    async def solve(
        self,
        anchor_url: str,
        *,
        action: str | None = None,
        bg: str | None = None,
        user_agent: str | None = None,
        hostname: str | None = None,
    ) -> SolveResult:
        started = time.monotonic()
        deadline = started + self.timeout
        validate_overrides(anchor_url, action, bg, user_agent, hostname)
        with self._operation():
            bundle = await self._prepare(deadline)
            flow = _flow(
                anchor_url, action, bundle, self._origin, started, bg, user_agent, hostname
            )
            cookies = OperationCookies()
            request = next(flow)
            while True:
                try:
                    reply = (await self._fetch(request, deadline, cookies)).decode("utf-8")
                except UnicodeError:
                    raise AnchorpassError("ProtocolError", request.stage) from None
                try:
                    request = flow.send(reply)
                except StopIteration as finished:
                    _remaining(deadline)
                    return finished.value  # type: ignore[no-any-return]

    async def close(self) -> None:
        waiters = self._close_updates()
        if waiters is not None:
            flights = [flight for waiter in waiters if (flight := waiter.release(closed=True))]
            cancelled = False
            try:
                for flight in flights:
                    try:
                        await flight.ajoin()
                    except asyncio.CancelledError:
                        cancelled = True
            finally:
                if self._session is not None:
                    await self._session.close()
            if cancelled:
                raise asyncio.CancelledError

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()


def solve(
    anchor_url: str,
    *,
    action: str | None = None,
    bg: str | None = None,
    user_agent: str | None = None,
    hostname: str | None = None,
    **kwargs: Any,
) -> SolveResult:
    with Client(**kwargs) as client:
        return client.solve(
            anchor_url, action=action, bg=bg, user_agent=user_agent, hostname=hostname
        )


async def asolve(
    anchor_url: str,
    *,
    action: str | None = None,
    bg: str | None = None,
    user_agent: str | None = None,
    hostname: str | None = None,
    **kwargs: Any,
) -> SolveResult:
    async with AsyncClient(**kwargs) as client:
        return await client.solve(
            anchor_url, action=action, bg=bg, user_agent=user_agent, hostname=hostname
        )


def load_bg(**kwargs: Any) -> str:
    with Client(**kwargs) as client:
        return client.load_bg()


async def aload_bg(**kwargs: Any) -> str:
    async with AsyncClient(**kwargs) as client:
        return await client.load_bg()
