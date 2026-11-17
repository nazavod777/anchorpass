from __future__ import annotations

import asyncio
import concurrent.futures
import gc
import json
import socket
import subprocess
import sys
import threading
import time
import weakref
from types import SimpleNamespace

import pytest
from test_proxy import proxy_server

from anchorpass import AnchorpassError, AsyncClient, Client, resolver, updates


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


@pytest.fixture
def blocked_dns(monkeypatch):
    original = socket.getaddrinfo
    state = SimpleNamespace(
        entered=threading.Event(),
        release=threading.Event(),
        calls=[],
        resolver=resolver._DNSResolver(),
        sessions=[],
    )

    def resolve(host, port, *args, **kwargs):
        if host not in ("resolver.invalid", "localhost"):
            return original(host, port, *args, **kwargs)
        state.calls.append(host)
        state.entered.set()
        if not state.release.wait(3):
            raise socket.gaierror(socket.EAI_AGAIN, "DNS fixture release deadline")
        if host == "localhost":
            return original("127.0.0.1", port, *args, **kwargs)
        raise socket.gaierror(socket.EAI_NONAME, "DNS fixture")

    create_session = updates.create_session

    def session(*args, **kwargs):
        result = create_session(*args, **kwargs)
        state.sessions.append((result, result.connector))
        return result

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(resolver, "_RESOLVER", state.resolver)
    monkeypatch.setattr(updates, "create_session", session)
    try:
        yield state
    finally:
        state.release.set()
        for thread in state.resolver.threads:
            thread.join(timeout=2)
            assert not thread.is_alive()
        assert updates._COORDINATOR.flights == {}
        assert updates._COORDINATOR.live == set()
        assert updates._COORDINATOR.waiters == 0
        assert all(session.closed and connector.closed for session, connector in state.sessions)


def route_options(route):
    if route == "direct":
        return {"bundle_url": "https://resolver.invalid/bundle"}
    return {
        "bundle_url": "https://127.0.0.1:9/bundle",
        "updates_proxy": f"{route}://resolver.invalid:1080",
    }


@pytest.mark.parametrize("route", ["direct", "http", "https", "socks5", "socks5h"])
def test_sync_refresh_deadline_does_not_join_blocked_dns(blocked_dns, route):
    started = time.monotonic()
    with Client(timeout=0.05, update_timeout=0.05, **route_options(route)) as client:
        with pytest.raises(AnchorpassError, match="DeadlineExceeded"):
            client.refresh()
    assert time.monotonic() - started < 0.5
    assert blocked_dns.entered.is_set()
    assert len(blocked_dns.calls) == 1
    assert all(thread.daemon and thread.is_alive() for thread in blocked_dns.resolver.threads)


@pytest.mark.parametrize("route", ["direct", "http", "https", "socks5", "socks5h"])
@pytest.mark.parametrize("stop", ["operation_timeout", "update_timeout", "cancel", "close"])
async def test_async_refresh_stops_before_blocked_dns_returns(blocked_dns, route, stop):
    loop = asyncio.get_running_loop()
    original_getaddrinfo = loop.getaddrinfo
    async with AsyncClient(
        timeout=0.05 if stop == "operation_timeout" else 30,
        update_timeout=0.05 if stop == "update_timeout" else 30,
        **route_options(route),
    ) as client:
        task = asyncio.create_task(client.refresh())
        await until(blocked_dns.entered.is_set)
        flight = next(iter(updates._COORDINATOR.live))
        update_loop = flight.loop
        started = time.monotonic()
        if stop == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif stop == "close":
            await client.close()
            with pytest.raises(AnchorpassError, match="ClientClosed"):
                await task
        else:
            with pytest.raises(AnchorpassError, match="DeadlineExceeded"):
                await task
        assert time.monotonic() - started < 0.5
        assert flight.stopped.done()
        assert not flight.thread.is_alive()
        assert update_loop.is_closed()
        assert update_loop._default_executor is None
        assert loop.getaddrinfo == original_getaddrinfo
        assert len(blocked_dns.calls) == 1
        assert all(thread.is_alive() for thread in blocked_dns.resolver.threads)


@pytest.mark.parametrize("route", ["direct", "http", "socks5h"])
def test_sync_close_joins_flight_without_waiting_for_dns(blocked_dns, route):
    client = Client(timeout=30, update_timeout=30, **route_options(route))
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(client.refresh)
        try:
            assert blocked_dns.entered.wait(2)
            flight = next(iter(updates._COORDINATOR.live))
            started = time.monotonic()
            client.close()
            assert time.monotonic() - started < 0.5
            with pytest.raises(AnchorpassError, match="ClientClosed"):
                task.result(timeout=0.5)
            assert flight.stopped.done()
            assert not flight.thread.is_alive()
            assert not blocked_dns.release.is_set()
        finally:
            client.close()


def test_cancelled_dns_keeps_slots_until_os_calls_return_then_recovers(
    blocked_dns, monkeypatch, origin, signer
):
    monkeypatch.setattr(resolver, "MAX_DNS_LOOKUPS", 2)
    with Client(
        timeout=0.05, update_timeout=0.05, bundle_url="https://resolver.invalid/bundle"
    ) as client:
        for _ in range(2):
            with pytest.raises(AnchorpassError, match="DeadlineExceeded"):
                client.refresh()
        assert len(blocked_dns.calls) == 2
        assert len(blocked_dns.resolver.threads) == 2
        started = time.monotonic()
        for _ in range(3):
            with pytest.raises(AnchorpassError, match="ResourceLimit"):
                client.refresh()
        assert time.monotonic() - started < 0.5
        assert len(blocked_dns.calls) == 2
        blocked_dns.release.set()
        for thread in blocked_dns.resolver.threads:
            thread.join(timeout=2)
            assert not thread.is_alive()

    url, state = origin
    keys, sign = signer
    state["bundle"] = sign("2")
    with Client(
        bundle_url=url.replace("127.0.0.1", "localhost") + "/bundle", trusted_keys=keys
    ) as client:
        assert client.refresh().revision == "2"
    assert blocked_dns.calls == ["resolver.invalid", "resolver.invalid", "localhost"]
    assert len(blocked_dns.resolver.threads) <= resolver.MAX_DNS_LOOKUPS


async def test_one_cancelled_subscriber_preserves_shared_dns(blocked_dns):
    async with (
        AsyncClient(timeout=30, **route_options("direct")) as first,
        AsyncClient(timeout=30, **route_options("direct")) as second,
    ):
        first_task = asyncio.create_task(first.refresh())
        second_task = asyncio.create_task(second.refresh())
        await until(lambda: blocked_dns.entered.is_set() and updates._COORDINATOR.waiters == 2)
        flight = next(iter(updates._COORDINATOR.live))
        first_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first_task
        assert flight.thread.is_alive()
        assert updates._COORDINATOR.waiters == 1
        assert len(blocked_dns.calls) == 1
        blocked_dns.release.set()
        with pytest.raises(AnchorpassError, match="TransportError"):
            await second_task
        assert not flight.thread.is_alive()


@pytest.mark.parametrize("scheme,lookups", [("http", 1), ("socks5", 2), ("socks5h", 1)])
async def test_proxy_dns_success_preserves_target_dns_policy(
    blocked_dns, origin, signer, scheme, lookups
):
    url, state = origin
    keys, sign = signer
    state["bundle"] = sign("2")
    blocked_dns.release.set()
    with proxy_server(state["server"].server_port, socks=scheme.startswith("socks")) as (
        port,
        records,
    ):
        async with AsyncClient(
            bundle_url=url.replace("127.0.0.1", "localhost") + "/bundle",
            trusted_keys=keys,
            updates_proxy=f"{scheme}://localhost:{port}",
        ) as client:
            assert (await client.refresh()).revision == "2"
        assert records
        if scheme == "socks5":
            assert records[0][0] == 1
        elif scheme == "socks5h":
            assert records[0][0] == 3
            assert records[0][1] == "localhost"
    assert blocked_dns.calls == ["localhost"] * lookups


@pytest.fixture
async def socks_greeting():
    closed = asyncio.Event()
    tasks = set()

    async def handle(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        try:
            version, count = await reader.readexactly(2)
            assert version == 5
            await reader.readexactly(count)
            writer.write(b"\x05\x00")
            await writer.drain()
            # socks5 resolves the target only after this greeting. A cancelled
            # lookup must close this already established proxy connection.
            assert await reader.read() == b""
            closed.set()
        finally:
            writer.close()
            await writer.wait_closed()
            tasks.discard(task)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1], closed
    finally:
        server.close()
        await server.wait_closed()
        pending = list(tasks)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


@pytest.mark.parametrize("stop", ["timeout", "cancel", "close", "sync_timeout"])
async def test_local_socks_dns_stops_promptly_and_closes_proxy_socket(
    blocked_dns, socks_greeting, stop
):
    port, closed = socks_greeting
    options = {
        "bundle_url": "https://resolver.invalid/bundle",
        "updates_proxy": f"socks5://127.0.0.1:{port}",
        "timeout": 0.05 if "timeout" in stop else 30,
        "update_timeout": 30,
    }
    if stop == "sync_timeout":
        with Client(**options) as client:
            with pytest.raises(AnchorpassError, match="DeadlineExceeded"):
                await asyncio.wait_for(asyncio.to_thread(client.refresh), timeout=0.5)
    else:
        async with AsyncClient(**options) as client:
            task = asyncio.create_task(client.refresh())
            await until(blocked_dns.entered.is_set)
            started = time.monotonic()
            if stop == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            elif stop == "close":
                await client.close()
                with pytest.raises(AnchorpassError, match="ClientClosed"):
                    await task
            else:
                with pytest.raises(AnchorpassError, match="DeadlineExceeded"):
                    await task
            assert time.monotonic() - started < 0.5
    await asyncio.wait_for(closed.wait(), timeout=0.5)
    assert not blocked_dns.release.is_set()
    assert blocked_dns.calls == ["resolver.invalid"]


def test_numeric_name_lookup_also_avoids_default_executor(monkeypatch, blocked_dns):
    def getnameinfo(*args):
        blocked_dns.entered.set()
        blocked_dns.release.wait(3)
        return "fe80::1%1", "443"

    monkeypatch.setattr(socket, "getnameinfo", getnameinfo)

    async def lookup():
        async with asyncio.timeout(0.05):
            return await asyncio.get_running_loop().getnameinfo(("fe80::1", 443, 0, 1), 0)

    started = time.monotonic()
    with asyncio.Runner(loop_factory=resolver.create_update_loop) as runner:
        with pytest.raises(TimeoutError):
            runner.run(lookup())
    assert time.monotonic() - started < 0.5
    assert blocked_dns.entered.is_set()


def test_cancelled_dns_does_not_retain_closed_update_loop(blocked_dns):
    async def lookup():
        async with asyncio.timeout(0.05):
            await asyncio.get_running_loop().getaddrinfo("resolver.invalid", 443)

    with asyncio.Runner(loop_factory=resolver.create_update_loop) as runner:
        reference = weakref.ref(runner.get_loop())
        with pytest.raises(TimeoutError):
            runner.run(lookup())
    gc.collect()
    assert reference() is None
    assert all(thread.is_alive() for thread in blocked_dns.resolver.threads)


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_process_exits_while_os_dns_remains_blocked(mode):
    script = """
import asyncio
import json
import socket
import sys
import threading
import time
from anchorpass import AnchorpassError, AsyncClient, Client, updates

def blocked(*args, **kwargs):
    threading.Event().wait()

socket.getaddrinfo = blocked
options = dict(timeout=.05, update_timeout=.05, bundle_url='https://resolver.invalid/bundle')

async def run():
    async with AsyncClient(**options) as client:
        await client.refresh()

started = time.monotonic()
try:
    if sys.argv[1] == 'sync':
        with Client(**options) as client:
            client.refresh()
    else:
        asyncio.run(run())
except AnchorpassError as exc:
    assert exc.code == 'DeadlineExceeded'
else:
    raise AssertionError('Refresh should time out')
assert not updates._COORDINATOR.live
assert not updates._COORDINATOR.flights
workers = [thread for thread in threading.enumerate() if thread.name == 'anchorpass-dns']
assert workers and all(thread.daemon for thread in workers)
print(json.dumps(dict(elapsed=time.monotonic() - started, dns_workers=len(workers))))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, mode], capture_output=True, text=True, timeout=3
    )
    assert result.returncode == 0, result.stderr
    measured = json.loads(result.stdout)
    assert measured["elapsed"] < 0.5
    assert measured["dns_workers"] == 1
