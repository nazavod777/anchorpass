from __future__ import annotations

import asyncio
import concurrent.futures
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from anchorpass import AnchorpassError, AsyncClient, Client, updates


@pytest.fixture
def update_origin(signer):
    keys, sign = signer
    release = threading.Event()
    entered = threading.Event()
    state = {"requests": [], "body": sign("2"), "status": 200}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):
            state["requests"].append(dict(self.headers))
            entered.set()
            if not release.wait(3):
                return
            body = state["body"]
            try:
                self.send_response(state["status"])
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/bundle", keys, state, release, entered
    release.set()
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert updates._COORDINATOR.flights == {}
    assert updates._COORDINATOR.live == set()
    assert updates._COORDINATOR.waiters == 0


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


async def test_two_async_clients_download_once_with_individual_caches(update_origin, tmp_path):
    url, keys, state, release, entered = update_origin
    first_cache, second_cache = tmp_path / "first.json", tmp_path / "second.json"
    async with (
        AsyncClient(bundle_url=url, trusted_keys=keys, cache_path=first_cache) as first,
        AsyncClient(bundle_url=url, trusted_keys=keys, cache_path=second_cache) as second,
    ):
        tasks = [asyncio.create_task(client.prepare()) for client in (first, second)]
        await until(lambda: entered.is_set() and updates._COORDINATOR.waiters == 2)
        release.set()
        bundles = await asyncio.gather(*tasks)
        assert [bundle.revision for bundle in bundles] == ["2", "2"]
        assert all(bundle.envelope == state["body"] for bundle in bundles)
        assert first_cache.read_bytes() == second_cache.read_bytes() == state["body"]
        assert len(state["requests"]) == 1
        assert "Cookie" not in state["requests"][0]


@pytest.mark.parametrize("other_mode", ["sync", "async"])
async def test_shared_across_sync_clients_and_separate_event_loops(update_origin, other_mode):
    url, keys, state, release, entered = update_origin

    def other_client():
        if other_mode == "sync":
            with Client(bundle_url=url, trusted_keys=keys) as client:
                return client.prepare()

        async def prepare():
            async with AsyncClient(bundle_url=url, trusted_keys=keys) as client:
                return await client.prepare()

        return asyncio.run(prepare())

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        other = pool.submit(other_client)
        try:
            async with AsyncClient(bundle_url=url, trusted_keys=keys) as client:
                current = asyncio.create_task(client.prepare())
                await until(lambda: entered.is_set() and updates._COORDINATOR.waiters == 2)
                release.set()
                assert (await current).revision == "2"
                assert other.result(timeout=2).revision == "2"
                assert len(state["requests"]) == 1
        finally:
            release.set()


def test_two_sync_clients_download_once(update_origin):
    url, keys, state, release, entered = update_origin

    def prepare():
        with Client(bundle_url=url, trusted_keys=keys) as client:
            return client.prepare()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(prepare) for _ in range(2)]
        try:
            assert entered.wait(2)
            deadline = time.monotonic() + 2
            while updates._COORDINATOR.waiters != 2:
                if time.monotonic() >= deadline:
                    pytest.fail("Second synchronous client did not join refresh")
                release.wait(0.001)
            release.set()
            assert [task.result(timeout=2).revision for task in tasks] == ["2", "2"]
            assert len(state["requests"]) == 1
        finally:
            release.set()


@pytest.mark.parametrize("stop", ["cancel", "close"])
async def test_stopping_one_client_preserves_other_refresh(update_origin, stop):
    url, keys, state, release, entered = update_origin
    async with (
        AsyncClient(bundle_url=url, trusted_keys=keys) as first,
        AsyncClient(bundle_url=url, trusted_keys=keys) as second,
    ):
        first_task = asyncio.create_task(first.prepare())
        second_task = asyncio.create_task(second.prepare())
        await until(lambda: entered.is_set() and updates._COORDINATOR.waiters == 2)
        if stop == "cancel":
            first_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first_task
        else:
            await first.close()
            with pytest.raises(AnchorpassError, match="ClientClosed"):
                await first_task
        assert updates._COORDINATOR.waiters == 1
        flight = next(iter(updates._COORDINATOR.flights.values()))
        assert flight.thread.is_alive()
        release.set()
        assert (await second_task).revision == "2"
        assert len(state["requests"]) == 1


@pytest.mark.parametrize("stop", ["cancel", "close"])
async def test_stopping_last_waiter_cancels_and_joins_io(update_origin, stop):
    url, keys, _, _, entered = update_origin
    async with AsyncClient(bundle_url=url, trusted_keys=keys, update_timeout=30) as client:
        task = asyncio.create_task(client.prepare())
        await until(entered.is_set)
        flight = next(iter(updates._COORDINATOR.flights.values()))
        started = time.monotonic()
        if stop == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await client.close()
            with pytest.raises(AnchorpassError, match="ClientClosed"):
                await task
        assert time.monotonic() - started < 1
        assert flight.stopped.done()
        assert not flight.thread.is_alive()
        assert updates._COORDINATOR.flights == {}
        assert updates._COORDINATOR.live == set()


async def test_waiter_timeout_does_not_shorten_shared_download(update_origin):
    url, keys, state, release, entered = update_origin
    async with (
        AsyncClient(bundle_url=url, trusted_keys=keys, timeout=0.05) as first,
        AsyncClient(bundle_url=url, trusted_keys=keys, timeout=2) as second,
    ):
        first_task = asyncio.create_task(first.refresh())
        await until(entered.is_set)
        second_task = asyncio.create_task(second.refresh())
        await until(lambda: updates._COORDINATOR.waiters == 2)
        with pytest.raises(AnchorpassError, match="DeadlineExceeded"):
            await first_task
        assert updates._COORDINATOR.waiters == 1
        release.set()
        assert (await second_task).revision == "2"
        assert len(state["requests"]) == 1


async def test_shared_raw_bytes_preserve_each_clients_trust_and_pin(update_origin, tmp_path):
    url, keys, state, release, entered = update_origin
    async with (
        AsyncClient(bundle_url=url, trusted_keys=keys, cache_path=tmp_path / "good") as good,
        AsyncClient(bundle_url=url, trusted_keys={}, cache_path=tmp_path / "bad") as bad,
        AsyncClient(bundle_url=url, trusted_keys=keys, pin="1") as pinned,
    ):
        tasks = [asyncio.create_task(client.refresh()) for client in (good, bad, pinned)]
        await until(lambda: entered.is_set() and updates._COORDINATOR.waiters == 3)
        release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert results[0].revision == "2"
        assert isinstance(results[1], AnchorpassError) and results[1].code == "ConfigInvalid"
        assert isinstance(results[2], AnchorpassError) and results[2].code == "ConfigUnavailable"
        assert good._store.current.envelope == state["body"]
        assert bad._store.current is None
        assert pinned._store.current.envelope == state["body"]
        with pytest.raises(AnchorpassError, match="ConfigUnavailable"):
            pinned._store.snapshot()
        assert not (tmp_path / "bad").exists()
        assert len(state["requests"]) == 1


async def test_forced_refresh_keeps_error_when_automatic_refresh_uses_snapshot(
    update_origin, signer
):
    url, keys, state, release, entered = update_origin
    _, sign = signer
    state["body"] = b"invalid envelope"
    async with AsyncClient(bundle_url=url, trusted_keys=keys) as client:
        client._store.accept(sign("1"))
        prepared = asyncio.create_task(client.prepare())
        await until(entered.is_set)
        forced = asyncio.create_task(client.refresh())
        await until(lambda: updates._COORDINATOR.waiters == 2)
        release.set()
        assert (await prepared).revision == "1"
        with pytest.raises(AnchorpassError, match="ConfigInvalid"):
            await forced
        assert len(state["requests"]) == 1


async def test_timeout_policies_do_not_share_downloads(update_origin):
    url, keys, state, release, _ = update_origin
    async with (
        AsyncClient(bundle_url=url, trusted_keys=keys, update_timeout=2) as first,
        AsyncClient(bundle_url=url, trusted_keys=keys, update_timeout=3) as second,
    ):
        tasks = [asyncio.create_task(client.prepare()) for client in (first, second)]
        await until(lambda: len(state["requests"]) == 2)
        assert len(updates._COORDINATOR.flights) == 2
        release.set()
        assert all(bundle.revision == "2" for bundle in await asyncio.gather(*tasks))


def test_policy_identity_includes_route_credentials_ca_contents_and_timeout(tmp_path):
    ca = tmp_path / "ca.pem"
    ca.write_bytes(b"first roots")
    url = "https://updates.test/bundle"
    proxy = "http://user:password@127.0.0.1:8080"
    original = updates.UpdatePolicy.create(url, proxy, 5, str(ca), None)
    assert original.key == updates.UpdatePolicy.create(url, proxy, 5.0, str(ca), None).key
    ca.write_bytes(b"changed roots")
    alternatives = [
        updates.UpdatePolicy(url + "?channel=other", proxy, 5.0, original.ca, None),
        updates.UpdatePolicy(url, proxy.replace("password", "other"), 5.0, original.ca, None),
        updates.UpdatePolicy(url, "", 5.0, original.ca, None),
        updates.UpdatePolicy(url, proxy, 6.0, original.ca, None),
        updates.UpdatePolicy.create(url, proxy, 5, str(ca), None),
        updates.UpdatePolicy.create(url, proxy, 5, None, str(ca)),
    ]
    assert all(policy.key != original.key for policy in alternatives)
    assert original.ca == b"first roots"
    assert "password" not in repr(original)
    assert "user" not in repr(original)


@pytest.mark.parametrize("kind", ["missing", "directory", "symlink", "fifo", "oversized"])
def test_ca_identity_rejects_unsafe_files_without_blocking(tmp_path, kind):
    path = tmp_path / "roots.pem"
    if kind == "directory":
        path.mkdir()
    elif kind == "symlink":
        target = tmp_path / "target.pem"
        target.write_bytes(b"roots")
        path.symlink_to(target)
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "oversized":
        path.write_bytes(b"x" * (updates.MAX_CA_BYTES + 1))
    started = time.monotonic()
    with pytest.raises(AnchorpassError, match="InvalidInput"):
        AsyncClient(bundle_url="https://updates.test/bundle", ca_bundle=str(path))
    assert time.monotonic() - started < 1


async def test_download_uses_exact_ca_snapshot_and_closes_session(monkeypatch, tmp_path):
    path = tmp_path / "roots.pem"
    path.write_bytes(b"original CA bytes")
    policy = updates.UpdatePolicy.create("https://updates.test/bundle", "", 5, str(path), None)
    path.write_bytes(b"replacement CA bytes")
    captured = []

    class Content:
        async def iter_chunked(self, size):
            yield b" exact raw bytes "

    class Response:
        status = 200
        content = Content()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class Session:
        def __init__(self, proxy, limit, **options):
            assert proxy == "" and limit == 1
            assert options["ca_data"] == b"original CA bytes"
            assert options["proxy_ca_data"] is None
            self.closed = False
            captured.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            self.closed = True

        def get(self, url, **options):
            assert options["allow_redirects"] is False
            return Response()

    monkeypatch.setattr(updates, "create_session", Session)
    assert await updates._download(policy) == b" exact raw bytes "
    assert captured[0].closed


@pytest.mark.parametrize("failure", ["thread_start", "download"])
def test_unexpected_failures_are_normalized_and_do_not_retain_flights(monkeypatch, failure):
    credential = "public-test-password"

    async def broken_download(policy):
        raise RuntimeError(credential)

    def broken_start(thread):
        raise RuntimeError(credential)

    if failure == "thread_start":
        monkeypatch.setattr(threading.Thread, "start", broken_start)
    else:
        monkeypatch.setattr(updates, "_download", broken_download)
    coordinator = updates._Coordinator()
    policy = updates.UpdatePolicy.create(
        "https://updates.test/bundle", f"http://user:{credential}@127.0.0.1:8080", 5, None, None
    )
    waiter = None
    try:
        with pytest.raises(AnchorpassError) as caught:
            waiter = coordinator.subscribe(policy)
            waiter.wait(2)
        assert caught.value.code == (
            "ResourceLimit" if failure == "thread_start" else "TransportError"
        )
        assert credential not in str(caught.value)
        assert credential not in repr(caught.value)
    finally:
        if waiter is not None:
            flight = waiter.release()
            if flight is not None:
                flight.join()
    assert coordinator.flights == {}
    assert coordinator.live == set()
    assert coordinator.waiters == 0


async def test_errors_do_not_accumulate_flights_or_workers(update_origin):
    url, keys, state, release, _ = update_origin
    state["status"] = 503
    release.set()
    async with AsyncClient(bundle_url=url, trusted_keys=keys) as client:
        for _ in range(5):
            with pytest.raises(AnchorpassError, match="HttpStatusError"):
                await client.refresh()
            assert updates._COORDINATOR.flights == {}
            assert updates._COORDINATOR.live == set()
            assert updates._COORDINATOR.waiters == 0


@pytest.mark.parametrize("limit", ["MAX_SHARED_REFRESHES", "MAX_SHARED_WAITERS"])
def test_coordinator_rejects_overload_without_queuing(monkeypatch, limit):
    async def pending_download(policy):
        await asyncio.Future()

    monkeypatch.setattr(updates, "_download", pending_download)
    monkeypatch.setattr(updates, limit, 2)
    coordinator = updates._Coordinator()
    base = updates.UpdatePolicy.create("https://updates.test/1", "", 5, None, None)
    other = updates.UpdatePolicy.create("https://updates.test/2", "", 5, None, None)
    excess = updates.UpdatePolicy.create("https://updates.test/3", "", 5, None, None)
    waiters = []
    try:
        waiters.append(coordinator.subscribe(base))
        waiters.append(coordinator.subscribe(other if limit == "MAX_SHARED_REFRESHES" else base))
        with pytest.raises(AnchorpassError, match="ResourceLimit"):
            coordinator.subscribe(excess if limit == "MAX_SHARED_REFRESHES" else base)
        assert coordinator.waiters == 2
    finally:
        for waiter in waiters:
            flight = waiter.release()
            if flight is not None:
                flight.join()
    assert coordinator.flights == {}
    assert coordinator.live == set()
    assert coordinator.waiters == 0


def test_sync_snapshot_requires_full_remaining_operation_duration(signer):
    keys, sign = signer
    with Client(timeout=30, update_timeout=1, trusted_keys=keys) as client:
        client._store.accept(sign(expires=int(time.time()) + 10))
        with pytest.raises(AnchorpassError, match="ConfigExpired"):
            client.prepare()


@pytest.mark.parametrize("client_type", [Client, AsyncClient])
@pytest.mark.parametrize(
    "url", ["https://updates.test/\ud800", "https://\ud800/bundle", b"https://updates.test", 1]
)
def test_invalid_update_url_fails_before_coordinator(client_type, url):
    with pytest.raises(AnchorpassError, match="InvalidInput"):
        client_type(bundle_url=url)
