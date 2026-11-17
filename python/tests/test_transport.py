from __future__ import annotations

import asyncio
import concurrent.futures
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pycurl
import pytest

from anchorpass import AnchorpassError, AsyncClient, Client, updates
from anchorpass.client import _Request
from anchorpass.cookies import OperationCookies

ANCHOR = "https://www.google.com/recaptcha/api2/anchor?k=test&co=origin"


def test_async_client_constructed_before_event_loop(origin):
    url, _ = origin
    client = AsyncClient()
    client._origin = url

    async def operation():
        async with client:
            assert (await client.solve(ANCHOR)).token

    asyncio.run(operation())


def test_shared_sync_client_reuses_a_bounded_pool_across_threads(origin):
    url, state = origin
    state["require_cookie"] = True
    state["delay"] = 0.02
    barrier = threading.Barrier(4)

    class ParallelClient(Client):
        def _fetch(self, request, deadline, cookies):
            if request.stage == "version":
                barrier.wait(timeout=2)
            return super()._fetch(request, deadline, cookies)

    with ParallelClient(max_in_flight=4) as client:
        client._origin = url
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [
                pool.submit(
                    client.solve, ANCHOR, action=f"operation-{index}", user_agent=f"UA/{index}"
                )
                for index in range(4)
            ]
            assert [future.result(timeout=3).token for future in futures] == [
                f"token:operation-{index}" for index in range(4)
            ]
        assert client._active == 0
        assert 1 < client._session._created <= client._max_in_flight
    assert client._session._created == 0


def test_sync_client_rejects_overload_and_releases_idle_handles(origin):
    url, _ = origin
    entered, release = threading.Event(), threading.Event()

    class HeldClient(Client):
        def _fetch(self, request, deadline, cookies):
            if request.stage == "version":
                entered.set()
                if not release.wait(2):
                    raise TimeoutError("test release deadline")
            return super()._fetch(request, deadline, cookies)

    client = HeldClient(max_in_flight=1)
    client._origin = url
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(client.solve, ANCHOR)
            try:
                assert entered.wait(2)
                with pytest.raises(AnchorpassError, match="ResourceLimit"):
                    client.solve(ANCHOR)
            finally:
                release.set()
            assert future.result(timeout=3).token
        assert client._active == 0
    finally:
        client.close()
    assert client._session._created == 0


def test_close_defers_busy_handle_cleanup_until_lease_returns():
    client = Client(max_in_flight=1)
    with client._session.lease() as handle:
        client.close()
        assert handle.getinfo(pycurl.RESPONSE_CODE) == 0
        assert client._session._created == 1
    assert client._session._created == 0
    with pytest.raises(AnchorpassError, match="ClientClosed"):
        with client._session.lease():
            pytest.fail("Closed pool returned a handle")


@pytest.mark.parametrize("empty", [False, True])
def test_consumed_reload_post_is_never_replayed_after_connection_reset(empty):
    posts = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def do_POST(self):
            posts.append(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            if len(posts) == 1:
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    body = b"" if empty else b"public-test-payload"
    try:
        with Client(timeout=2) as client:
            deadline = time.monotonic() + 2
            cookies = OperationCookies()
            client._fetch(_Request("anchor", "GET", base + "/anchor"), deadline, cookies)
            with pytest.raises(AnchorpassError, match="TransportError at reload"):
                client._fetch(_Request("reload", "POST", base + "/reload", body), deadline, cookies)
            assert posts == [body]
            assert (
                client._fetch(_Request("version", "GET", base + "/next"), deadline, cookies)
                == b"ok"
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


@pytest.mark.parametrize("scheme", ["http", "https", "socks5", "socks5h"])
async def test_proxy_refusal_is_a_public_error_for_solve_and_updates(scheme):
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    proxy = f"{scheme}://public-user:public-password@127.0.0.1:{port}"
    async with AsyncClient(proxy=proxy, timeout=0.5) as client:
        with pytest.raises(AnchorpassError) as caught:
            await client.solve(ANCHOR)
        assert caught.value.code == "TransportError"
        assert "public-password" not in str(caught.value)
    policy = updates.UpdatePolicy("https://updates.test/bundle", proxy, 0.5, None, None)
    with pytest.raises(AnchorpassError) as caught:
        await updates._download(policy)
    assert caught.value.code == "TransportError"
    assert "public-password" not in str(caught.value)
