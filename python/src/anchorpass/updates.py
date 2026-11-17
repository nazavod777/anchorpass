from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import os
import stat
import threading
from dataclasses import dataclass

import aiohttp
from aiohttp_socks import ProxyConnectionError, ProxyError, ProxyTimeoutError

from .bundle import MAX_BUNDLE_BYTES
from .errors import AnchorpassError
from .resolver import create_update_loop
from .transport import READ_CHUNK_BYTES, create_session, request_timeout

# Initial process limits: no queued downloads and no persistent worker threads.
MAX_SHARED_REFRESHES = 16
MAX_SHARED_WAITERS = 256
MAX_CA_BYTES = 1048576


def _read_ca(path: str | None) -> bytes | None:
    if path is None:
        return None
    try:
        fd = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError
            raw = handle.read(MAX_CA_BYTES + 1)
        if not raw or len(raw) > MAX_CA_BYTES:
            raise ValueError
        return raw
    except (OSError, TypeError, ValueError):
        raise AnchorpassError("InvalidInput", "configuration", "Invalid CA bundle") from None


@dataclass(frozen=True, repr=False)
class UpdatePolicy:
    url: str
    proxy: str
    timeout: float
    ca: bytes | None
    proxy_ca: bytes | None

    @classmethod
    def create(
        cls, url: str, proxy: str, timeout: float, ca: str | None, proxy_ca: str | None
    ) -> UpdatePolicy:
        return cls(url, proxy, float(timeout), _read_ca(ca), _read_ca(proxy_ca))

    @property
    def key(self) -> bytes:
        digest = hashlib.sha256()
        for value in (self.url.encode(), self.proxy.encode(), self.timeout.hex().encode()):
            digest.update(len(value).to_bytes(8, "big"))
            digest.update(value)
        for certificate in (self.ca, self.proxy_ca):
            digest.update(b"0" if certificate is None else b"1")
            if certificate is not None:
                digest.update(hashlib.sha256(certificate).digest())
        return digest.digest()


async def _download(policy: UpdatePolicy) -> bytes:
    body = bytearray()
    # The worker uses exactly the CA bytes in its registry key, even if a caller
    # replaces the original file while the download is in progress.
    try:
        async with asyncio.timeout(policy.timeout):
            async with create_session(
                policy.proxy,
                1,
                ca_data=policy.ca,
                proxy_ca_data=policy.proxy_ca,
                resolver=aiohttp.ThreadedResolver(),
            ) as session:
                async with session.get(
                    policy.url, timeout=request_timeout(policy.timeout), allow_redirects=False
                ) as response:
                    if response.status != 200:
                        raise AnchorpassError("HttpStatusError", "updates", str(response.status))
                    async for chunk in response.content.iter_chunked(READ_CHUNK_BYTES):
                        if len(body) + len(chunk) > MAX_BUNDLE_BYTES:
                            raise AnchorpassError(
                                "ResourceLimit", "updates", "Response body too large"
                            )
                        body.extend(chunk)
    except (TimeoutError, ProxyTimeoutError):
        raise AnchorpassError("DeadlineExceeded", "updates") from None
    except (aiohttp.ClientError, ProxyError, ProxyConnectionError, OSError, ValueError):
        raise AnchorpassError("TransportError", "updates") from None
    return bytes(body)


class _Flight:
    def __init__(self, coordinator: _Coordinator, policy: UpdatePolicy) -> None:
        self.coordinator = coordinator
        self.policy = policy
        self.waiters: set[UpdateWaiter] = set()
        self.stopped: concurrent.futures.Future[None] = concurrent.futures.Future()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.task: asyncio.Task[bytes] | None = None
        self.cancelled = False
        self.thread = threading.Thread(target=self._execute, name="anchorpass-update")

    async def _run(self) -> bytes:
        with self.coordinator.guard:
            self.loop = asyncio.get_running_loop()
            self.task = asyncio.current_task()
            if self.cancelled:
                raise asyncio.CancelledError
        return await _download(self.policy)

    def _execute(self) -> None:
        raw: bytes | None = None
        error: AnchorpassError | None = None
        try:
            with asyncio.Runner(loop_factory=create_update_loop) as runner:
                raw = runner.run(self._run())
        except asyncio.CancelledError:
            error = AnchorpassError("Cancelled", "updates")
        except AnchorpassError as exc:
            error = exc
        except Exception:
            error = AnchorpassError("TransportError", "updates")
        finally:
            # Publish only after the runner has drained cancellation and closed
            # the session/event loop. Uninterruptible OS DNS is separately bounded
            # and never holds the flight or its waiters open.
            with self.coordinator.guard:
                self.loop = None
                self.task = None
                self.coordinator.flights.pop(self.policy.key, None)
                for waiter in self.waiters:
                    if error is not None:
                        waiter.result.set_exception(error)
                    else:
                        assert raw is not None
                        waiter.result.set_result(raw)
            self.stopped.set_result(None)

    def join(self) -> None:
        if threading.current_thread() is self.thread:
            raise RuntimeError("An update worker cannot join itself")
        self.thread.join()
        with self.coordinator.guard:
            self.coordinator.live.discard(self)

    async def ajoin(self) -> None:
        if threading.current_thread() is self.thread:
            raise RuntimeError("An update worker cannot join itself")
        future = asyncio.wrap_future(self.stopped)
        cancelled = False
        while not future.done():
            try:
                await asyncio.shield(future)
            except asyncio.CancelledError:
                cancelled = True
        # No I/O remains after stopped; this only joins the thread's return.
        self.join()
        if cancelled:
            raise asyncio.CancelledError


class UpdateWaiter:
    def __init__(self, flight: _Flight) -> None:
        self.flight = flight
        self.result: concurrent.futures.Future[bytes] = concurrent.futures.Future()
        self.released = False

    def release(self, *, closed: bool = False) -> _Flight | None:
        coordinator = self.flight.coordinator
        with coordinator.guard:
            if self.released:
                return self.flight if self.flight.cancelled else None
            self.released = True
            coordinator.waiters -= 1
            self.flight.waiters.remove(self)
            if not self.result.done():
                self.result.set_exception(
                    AnchorpassError("ClientClosed", "client")
                    if closed
                    else AnchorpassError("Cancelled", "updates")
                )
            if self.flight.waiters:
                return None
            self.flight.cancelled = True
            loop, task = self.flight.loop, self.flight.task
            if loop is not None and task is not None:
                try:
                    loop.call_soon_threadsafe(task.cancel)
                except RuntimeError:
                    # The runner may already be closing the completed loop.
                    pass
            return self.flight

    def wait(self, timeout: float) -> bytes:
        try:
            return self.result.result(timeout)
        except concurrent.futures.TimeoutError:
            raise AnchorpassError("DeadlineExceeded", "prepare") from None

    async def await_result(self, timeout: float) -> bytes:
        future = asyncio.wrap_future(self.result)
        # A cancelled shield must not leave an unobserved exception on the
        # wrapper when close() subsequently wakes this particular waiter.
        future.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        try:
            async with asyncio.timeout(timeout):
                return await asyncio.shield(future)
        except TimeoutError:
            raise AnchorpassError("DeadlineExceeded", "prepare") from None


class _Coordinator:
    def __init__(self) -> None:
        self.guard = threading.Lock()
        self.flights: dict[bytes, _Flight] = {}
        self.live: set[_Flight] = set()
        self.waiters = 0

    def subscribe(self, policy: UpdatePolicy) -> UpdateWaiter:
        with self.guard:
            if self.waiters >= MAX_SHARED_WAITERS:
                raise AnchorpassError("ResourceLimit", "updates")
            flight = self.flights.get(policy.key)
            if flight is not None and flight.cancelled:
                raise AnchorpassError("ResourceLimit", "updates")
            new = flight is None
            if flight is None:
                if len(self.live) >= MAX_SHARED_REFRESHES:
                    raise AnchorpassError("ResourceLimit", "updates")
                flight = _Flight(self, policy)
                self.flights[policy.key] = flight
                self.live.add(flight)
            waiter = UpdateWaiter(flight)
            flight.waiters.add(waiter)
            self.waiters += 1
            if new:
                try:
                    flight.thread.start()
                except RuntimeError:
                    self.flights.pop(policy.key)
                    self.live.discard(flight)
                    self.waiters -= 1
                    raise AnchorpassError("ResourceLimit", "updates") from None
            return waiter


_COORDINATOR = _Coordinator()


def subscribe(policy: UpdatePolicy) -> UpdateWaiter:
    return _COORDINATOR.subscribe(policy)
