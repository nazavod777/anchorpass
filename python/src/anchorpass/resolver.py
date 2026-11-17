from __future__ import annotations

import asyncio
import socket
import threading
from collections.abc import Callable
from functools import partial
from typing import Generic, Literal, TypeVar

from .errors import AnchorpassError

# Match the shared update limit. Cancelled OS calls retain their slots, with no
# queue and no replacement workers until those calls actually finish.
MAX_DNS_LOOKUPS = 16
_T = TypeVar("_T")
_Address = tuple[str, int] | tuple[str, int, int, int]
_AddrInfo = (
    tuple[Literal[socket.AddressFamily.AF_INET], socket.SocketKind, int, str, tuple[str, int]]
    | tuple[
        Literal[socket.AddressFamily.AF_INET6],
        socket.SocketKind,
        int,
        str,
        tuple[str, int, int, int] | tuple[int, bytes],
    ]
)


class _DNSCall(Generic[_T]):
    def __init__(self, function: Callable[[], _T], loop: asyncio.AbstractEventLoop) -> None:
        self.function = function
        self.guard = threading.Lock()
        self.loop: asyncio.AbstractEventLoop | None = loop
        self.future: asyncio.Future[_T] | None = loop.create_future()

    def detach(self) -> None:
        # An uninterruptible OS call must not retain a closed loop or its tasks.
        with self.guard:
            self.loop = None
            self.future = None

    def run(self) -> None:
        value: _T | None = None
        error: Exception | None = None
        try:
            value = self.function()
        except Exception as exc:
            error = exc
        with self.guard:
            if self.loop is not None:
                try:
                    self.loop.call_soon_threadsafe(self._deliver, value, error)
                except RuntimeError:
                    # Cancellation may already have closed the update loop.
                    pass

    def _deliver(self, value: _T | None, error: Exception | None) -> None:
        future = self.future
        if future is not None and not future.done():
            if error is not None:
                future.set_exception(error)
            else:
                # DNS functions always return a non-None result on success.
                assert value is not None
                future.set_result(value)


class _DNSResolver:
    def __init__(self) -> None:
        self.guard = threading.Lock()
        self.threads: set[threading.Thread] = set()

    async def _call(self, function: Callable[[], _T]) -> _T:
        call = _DNSCall(function, asyncio.get_running_loop())
        assert call.future is not None
        future = call.future
        try:
            with self.guard:
                # Keep slots until the actual threads have returned, including
                # after cancellation. The retained set itself is also bounded.
                self.threads = {thread for thread in self.threads if thread.is_alive()}
                if len(self.threads) >= MAX_DNS_LOOKUPS:
                    raise AnchorpassError("ResourceLimit", "updates", "DNS lookup limit reached")
                thread = threading.Thread(target=call.run, name="anchorpass-dns", daemon=True)
                self.threads.add(thread)
                try:
                    thread.start()
                except RuntimeError:
                    self.threads.discard(thread)
                    raise AnchorpassError("ResourceLimit", "updates") from None
            return await future
        finally:
            call.detach()

    async def getaddrinfo(
        self,
        host: str | bytes | None,
        port: str | bytes | int | None,
        *,
        family: int = 0,
        type: int = 0,
        proto: int = 0,
        flags: int = 0,
    ) -> list[_AddrInfo]:
        return await self._call(partial(socket.getaddrinfo, host, port, family, type, proto, flags))

    async def getnameinfo(self, sockaddr: _Address, flags: int = 0) -> tuple[str, str]:
        return await self._call(partial(socket.getnameinfo, sockaddr, flags))


_RESOLVER = _DNSResolver()


def create_update_loop() -> asyncio.AbstractEventLoop:
    loop = asyncio.new_event_loop()
    # Confine the overrides to this owned loop. Both aiohttp's ThreadedResolver
    # and python-socks (proxy host and local SOCKS target DNS) use these methods.
    # No library globals or application event loops are modified.
    loop.getaddrinfo = _RESOLVER.getaddrinfo  # type: ignore[method-assign]
    loop.getnameinfo = _RESOLVER.getnameinfo  # type: ignore[method-assign]
    return loop
