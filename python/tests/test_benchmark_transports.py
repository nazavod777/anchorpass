"""Functional adapter checks without comparative performance measurements."""

from __future__ import annotations

import asyncio
import importlib.util
import time
from http.client import RemoteDisconnected
from pathlib import Path

import pytest

from anchorpass import AnchorpassError, AsyncClient, Client
from anchorpass.client import _Request
from anchorpass.cookies import OperationCookies


def load_benchmark(name):
    path = Path(__file__).parents[2] / "benchmarks" / f"{name}.py"
    specification = importlib.util.spec_from_file_location(f"benchmark_{name}", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


matrix = load_benchmark("python_matrix")
fixture_module = load_benchmark("fixture")
CANDIDATES = (
    "python-requests",
    "python-urllib3",
    "python-httpx-sync",
    "python-pycurl",
    "python-curl-sync",
    "python-aiohttp",
    "python-httpx-async",
    "python-curl-async",
)


@pytest.fixture
async def transport_fixture(tmp_path):
    fixture = fixture_module.Fixture(tmp_path)
    metadata = await fixture.start()
    try:
        yield fixture, metadata
    finally:
        await fixture.close()


def profile(**overrides):
    return {
        "kind": "flow",
        "route": "direct",
        "connection_mode": "warm",
        "concurrency": 4,
        "timeout_ms": 2000,
        "max_response_bytes": 65536,
        "expected_error": None,
        "expected_bytes": len(b"fixture-token"),
        "path": "/bytes?size=1024",
        **overrides,
    }


def test_proxy_wrappers_keep_reset_tls_and_auth_categories():
    import requests
    import urllib3

    for cause, expected in (
        (RemoteDisconnected("Remote end closed connection without response"), "transport"),
        (urllib3.exceptions.SSLError("certificate verify failed"), "tls"),
        (OSError("Tunnel connection failed: 407 Proxy Authentication Required"), "proxy"),
    ):
        wrapped = urllib3.exceptions.ProxyError("Unable to connect to proxy", cause)
        assert matrix.classify(wrapped) == expected
        wrapped = requests.exceptions.ProxyError(
            urllib3.exceptions.MaxRetryError(None, "/reset", reason=wrapped)
        )
        assert matrix.classify(wrapped) == expected


@pytest.mark.parametrize("candidate", CANDIDATES)
async def test_flow_candidates_preserve_cookies_and_proxy_dns(transport_fixture, candidate):
    fixture, metadata = transport_fixture
    for route in ("direct", "http", "https", "socks5", "socks5h"):
        selected = profile(route=route)
        job = {"candidate": candidate, "profile": selected, "fixture": metadata}
        before = dict(fixture.metrics)
        if candidate.endswith("async") or candidate == "python-aiohttp":
            transport = matrix.AsyncTransport(job)
            try:
                sizes = await asyncio.gather(*(transport.request(selected) for _ in range(4)))
                sizes.append(await transport.request(selected))
            finally:
                await transport.close()
        else:

            def worker(job, selected):
                transport = matrix.SyncTransport(job)
                try:
                    return [transport.request(selected) for _ in range(2)]
                finally:
                    transport.close()

            sizes = [
                size
                for group in await asyncio.gather(
                    asyncio.to_thread(worker, job, selected),
                    asyncio.to_thread(worker, job, selected),
                )
                for size in group
            ]
        assert all(size == len(b"fixture-token") for size in sizes)
        assert fixture.metrics["protocol_errors"] == 0
        assert fixture.metrics["credential_leaks"] == 0
        assert fixture.metrics["blocked_external_targets"] == 0
        if route == "socks5":
            assert fixture.metrics["socks_atyp:3"] == before.get("socks_atyp:3", 0)
            assert sum(fixture.metrics[f"socks_atyp:{kind}"] for kind in (1, 4)) > sum(
                before.get(f"socks_atyp:{kind}", 0) for kind in (1, 4)
            )
        elif route == "socks5h":
            assert fixture.metrics["socks_atyp:3"] > before.get("socks_atyp:3", 0)


async def test_pycurl_deadline_trust_body_limits_and_recovery(transport_fixture):
    _, metadata = transport_fixture
    controls = (
        {
            "route": "http",
            "path": "/bytes?size=64&drip_ms=20",
            "timeout_ms": 150,
            "expected_bytes": 64,
            "expected_error": "deadline",
        },
        {"route": "bad_auth", "expected_error": "proxy"},
        {"route": "bad_target", "expected_error": "tls"},
        {"route": "bad_tls", "expected_error": "tls"},
        {"route": "http", "path": "/gzip?size=262144", "expected_error": "response_limit"},
        {"route": "http", "path": "/bytes?size=1024&status=429", "expected_error": "http_status"},
        {"route": "http", "path": "/reset", "expected_error": "transport"},
    )
    for control in controls:
        selected = profile(kind="transport", **{"expected_bytes": 1024, **control})

        def worker(selected):
            transport = matrix.SyncTransport(
                {"candidate": "python-pycurl", "fixture": metadata, "profile": selected}
            )
            started = time.perf_counter_ns()
            try:
                with pytest.raises(Exception) as caught:
                    transport.request(selected)
                observed = matrix.sample(selected, started, error=caught.value)
                assert observed["ok"], observed
                if selected["route"] == "http":
                    assert transport.request(matrix.recovery_profile(selected)) == 1024
            finally:
                transport.close()

        await asyncio.to_thread(worker, selected)


async def test_aiohttp_package_backend_flow_deadline_cancel_and_recovery(
    transport_fixture, tmp_path
):
    fixture, metadata = transport_fixture
    job = {"candidate": "python-api-aiohttp", "fixture": metadata, "profile": profile(kind="api")}
    client = matrix.aiohttp_api_client(job, str(tmp_path / "cache.json"))
    client._store.accept(fixture.bundle)
    try:
        results = await asyncio.gather(*(client.solve(metadata["anchor_url"]) for _ in range(4)))
        assert all(result.token == "fixture-token" for result in results)
        request = _Request("version", "GET", metadata["https_url"] + "/bytes?size=64&drip_ms=20")
        with pytest.raises(AnchorpassError, match="DeadlineExceeded"):
            await client._fetch(request, time.monotonic() + 0.15, OperationCookies())
        before = fixture.metrics["path:/bytes"]
        transfer = asyncio.create_task(
            client._fetch(request, time.monotonic() + 2, OperationCookies())
        )
        async with asyncio.timeout(2):
            while fixture.metrics["path:/bytes"] == before:
                await asyncio.sleep(0.001)
        transfer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await transfer
        assert (await client.solve(metadata["anchor_url"])).token == "fixture-token"
        for method, expected_requests in (("GET", 2), ("POST", 1)):
            before = fixture.metrics["path:/reset"]
            request = _Request(
                "version" if method == "GET" else "reload",
                method,
                metadata["https_url"] + "/reset",
                b"k=test" if method == "POST" else None,
            )
            with pytest.raises(AnchorpassError, match="TransportError"):
                await client._fetch(request, time.monotonic() + 2, OperationCookies())
            assert fixture.metrics["path:/reset"] - before == expected_requests
        assert fixture.metrics["protocol_errors"] == 0
        assert fixture.metrics["credential_leaks"] == 0
    finally:
        await client.close()


@pytest.mark.parametrize("client_type", [Client, AsyncClient])
async def test_production_transports_routes_trust_limits_and_recovery(
    transport_fixture, client_type
):
    fixture, metadata = transport_fixture

    def create(route):
        selected_route = "https" if route == "bad_target" else route
        client = client_type(
            proxy=None if route == "direct" else metadata["proxies"][selected_route],
            ca_bundle=metadata["ca_file"],
            proxy_ca_bundle=metadata["proxy_ca_file"],
            trusted_keys=metadata["trusted_keys"],
            offline=True,
            max_in_flight=4,
            max_response_bytes=65536,
            timeout=2,
        )
        client._store.accept(fixture.bundle)
        client._origin = (
            metadata["bad_target_url"] if route == "bad_target" else metadata["https_url"]
        )
        return client

    async def call(method, *args):
        if client_type is Client:
            return await asyncio.to_thread(method, *args)
        return await method(*args)

    async def close(client):
        if client_type is Client:
            client.close()
        else:
            await client.close()

    for route in ("direct", "http", "https", "socks5", "socks5h"):
        client = create(route)
        before = dict(fixture.metrics)
        try:
            results = await asyncio.gather(
                *(call(client.solve, metadata["anchor_url"]) for _ in range(4))
            )
            assert all(result.token == "fixture-token" for result in results)
            assert (await call(client.solve, metadata["anchor_url"])).token == "fixture-token"
        finally:
            await close(client)
        if route == "socks5":
            assert fixture.metrics["socks_atyp:3"] == before.get("socks_atyp:3", 0)
            assert sum(fixture.metrics[f"socks_atyp:{kind}"] for kind in (1, 4)) > sum(
                before.get(f"socks_atyp:{kind}", 0) for kind in (1, 4)
            )
        elif route == "socks5h":
            assert fixture.metrics["socks_atyp:3"] > before.get("socks_atyp:3", 0)

    controls = (
        ("http", "/bytes?size=64&drip_ms=20", 0.15, "DeadlineExceeded"),
        ("bad_auth", "/bytes?size=1024", 2, "TransportError"),
        ("bad_target", "/bytes?size=1024", 2, "TransportError"),
        ("bad_tls", "/bytes?size=1024", 2, "TransportError"),
        ("http", "/gzip?size=262144", 2, "ResourceLimit"),
        ("http", "/bytes?size=1024&status=429", 2, "HttpStatusError"),
        ("http", "/reset", 2, "TransportError"),
    )
    for route, path, timeout, expected_error in controls:
        client = create(route)
        try:
            with pytest.raises(AnchorpassError, match=expected_error):
                await call(
                    client._fetch,
                    _Request("version", "GET", client._origin + path),
                    time.monotonic() + timeout,
                    OperationCookies(),
                )
            if route == "http":
                body = await call(
                    client._fetch,
                    _Request("version", "GET", client._origin + "/bytes?size=1024"),
                    time.monotonic() + 2,
                    OperationCookies(),
                )
                assert body == b"x" * 1024
        finally:
            await close(client)
    assert fixture.metrics["protocol_errors"] == 0
    assert fixture.metrics["credential_leaks"] == 0
    assert fixture.metrics["blocked_external_targets"] == 0
