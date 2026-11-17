from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from anchorpass import AnchorpassError, AsyncClient, Client
from anchorpass.bundle import BundleStore, verify_bundle
from anchorpass.core import parse_anchor
from anchorpass.proxy import normalize_proxy

ANCHOR = "https://www.google.com/recaptcha/api2/anchor?k=test&co=origin&v=old"


@pytest.mark.parametrize(
    "case", json.loads((Path(__file__).parents[2] / "spec/vectors/proxies.json").read_text())
)
def test_shared_proxy_vectors(case):
    if case["valid"]:
        normalize_proxy(case["value"])
    else:
        with pytest.raises(AnchorpassError, match="InvalidProxy"):
            normalize_proxy(case["value"])


@pytest.mark.parametrize(
    "suffix", ["&k=duplicate", "#fragment", "&co=duplicate", "&bad=%xx", "&q=\ud800"]
)
def test_anchor_rejects_ambiguous_inputs(suffix):
    with pytest.raises(AnchorpassError, match="InvalidInput"):
        parse_anchor(ANCHOR + suffix)


def test_proxy_rejects_invalid_unicode_before_transport():
    with pytest.raises(AnchorpassError, match="InvalidProxy"):
        normalize_proxy("http://\ud800:8080")


def test_sync_flow_and_cookie_isolation(origin):
    url, state = origin
    with Client() as client:
        client._origin = url
        first = client.solve(ANCHOR, action="examples/v3scores")
        second = client.solve(ANCHOR + "&sa=url-action")
        assert first.token == "token:examples/v3scores"
        assert second.token == "token:url-action"
        assert "token:" not in repr(first)
    requests = state["requests"]
    assert [x[0] for x in requests] == ["GET", "GET", "POST"] * 2
    assert "Cookie" not in requests[3][2]
    assert requests[2][2].get("Cookie") == "operation=private"
    fields = parse_qs(requests[2][3].decode())
    assert fields["c"] == ["anchor&token"]
    assert fields["sa"] == ["examples/v3scores"]
    assert parse_qs(urlsplit(requests[1][1]).query)["sa"] == ["examples/v3scores"]
    with pytest.raises(AnchorpassError, match="ClientClosed"):
        client.solve(ANCHOR)


async def test_async_flow_and_cancel(origin):
    url, state = origin
    async with AsyncClient() as client:
        client._origin = url
        results = await asyncio.gather(*[client.solve(ANCHOR, action="test") for _ in range(4)])
        assert all(result.token == "token:test" for result in results)
        state["delay"] = 0.2
        task = asyncio.create_task(client.solve(ANCHOR))
        await asyncio.sleep(0.03)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert client._active == 0


def test_deadline_and_response_limit(origin):
    url, state = origin
    state["delay"] = 0.2
    with Client(timeout=0.03) as client:
        client._origin = url
        started = time.monotonic()
        with pytest.raises(AnchorpassError, match="DeadlineExceeded"):
            client.solve(ANCHOR)
        assert time.monotonic() - started < 1
    state["delay"] = 0
    state["body"] = b"x" * 4096
    with Client(max_response_bytes=128) as client:
        client._origin = url
        with pytest.raises(AnchorpassError, match="ResourceLimit"):
            client.solve(ANCHOR)


def test_invalid_proxy_never_reaches_origin(origin, monkeypatch):
    url, state = origin
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(key, "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "*")
    with Client() as client:
        client._origin = url
        assert client.solve(ANCHOR).token
    state["requests"].clear()
    with Client(proxy="http://127.0.0.1:1", timeout=0.2) as client:
        client._origin = url
        with pytest.raises(AnchorpassError, match="TransportError"):
            client.solve(ANCHOR)
    assert state["requests"] == []


def test_signed_updates_cache_and_rollback(origin, signer, tmp_path):
    url, state = origin
    keys, sign = signer
    state["bundle"] = sign("2")
    path = tmp_path / "cache.json"
    with Client(bundle_url=url + "/bundle", trusted_keys=keys, cache_path=path) as client:
        assert client.prepare().revision == "2"
        state["bundle"] = sign("1")
        with pytest.raises(AnchorpassError, match="ConfigRollback"):
            client.refresh()
        assert client.prepare().revision == "2"
    with Client(trusted_keys=keys, cache_path=path, offline=True, pin="2") as client:
        assert client.prepare().revision == "2"
    raw = state["bundle"]
    envelope = json.loads(raw)
    envelope["signature"] = "AA=="
    with pytest.raises(AnchorpassError, match="ConfigInvalid"):
        verify_bundle(json.dumps(envelope).encode(), keys)


async def test_async_updates(origin, signer):
    url, state = origin
    keys, sign = signer
    state["bundle"] = sign("3")
    async with AsyncClient(bundle_url=url + "/bundle", trusted_keys=keys) as client:
        assert (await client.prepare()).revision == "3"


def test_expiry_duplicate_keys_and_immutable_fields(signer):
    keys, sign = signer
    store = BundleStore(keys=keys)
    with pytest.raises(AnchorpassError, match="ConfigExpired"):
        store.accept(sign(expires=int(time.time()) - 1))
    bundle = store.accept(sign("2"))
    with pytest.raises(TypeError):
        bundle.fields["bg"] = "changed"
    envelope = sign()
    duplicate = b'{"key_id":"wrong",' + envelope[1:]
    with pytest.raises(AnchorpassError, match="ConfigInvalid"):
        verify_bundle(duplicate, keys)


def test_updates_route_inherits_broken_proxy(origin, signer):
    url, state = origin
    keys, sign = signer
    state["bundle"] = sign()
    with Client(proxy="http://127.0.0.1:1", bundle_url=url + "/bundle", trusted_keys=keys) as c:
        with pytest.raises(AnchorpassError, match="ConfigUnavailable"):
            c.prepare()
    assert state["requests"] == []
    with Client(
        proxy="http://127.0.0.1:1",
        updates_proxy=None,
        bundle_url=url + "/bundle",
        trusted_keys=keys,
    ) as c:
        assert c.prepare().revision == "1"


def test_bg_override_and_extraction(origin):
    from anchorpass import extract_action

    assert extract_action(ANCHOR) is None
    assert extract_action(ANCHOR + "&sa=one%2Ftwo") == "one/two"
    url, state = origin
    with Client() as client:
        client._origin = url
        original = client.load_bg()
        client.solve(ANCHOR, bg="custom&bg=literal", action="custom")
        client.solve(ANCHOR)
        forms = [parse_qs(r[3].decode()) for r in state["requests"] if r[0] == "POST"]
        assert forms[0]["bg"] == ["custom&bg=literal"]
        assert forms[1]["bg"] == [original]
        assert client.load_bg() == original


@pytest.mark.parametrize("options", [{"action": ""}, {"bg": ""}, {"bg": "x" * 16385}])
def test_invalid_override_before_updates(origin, options):
    url, state = origin
    with Client(bundle_url=url + "/bundle") as client:
        with pytest.raises(AnchorpassError, match="InvalidInput"):
            client.solve(ANCHOR, **options)
    assert state["requests"] == []


async def test_first_refresh_shared_and_waiter_cancelled(origin, signer):
    url, state = origin
    keys, sign = signer
    state["bundle"] = sign("5")
    state["delay"] = 0.08
    async with AsyncClient(bundle_url=url + "/bundle", trusted_keys=keys) as client:
        first = asyncio.create_task(client.prepare())
        second = asyncio.create_task(client.prepare())
        await asyncio.sleep(0.01)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert (await second).revision == "5"
        assert len(state["requests"]) == 1


def test_malformed_shapes_are_public_errors(signer):
    from anchorpass.core import response_token

    keys, sign = signer
    with pytest.raises(AnchorpassError, match="ConfigInvalid"):
        verify_bundle(sign(fields=["reason", "hl", "size", "chr", "vh", "bg"]), keys)
    nested = b"[" * 10000 + b"0" + b"]" * 10000
    with pytest.raises(AnchorpassError, match="ConfigInvalid"):
        verify_bundle(nested, keys)
    with pytest.raises(AnchorpassError, match="ProtocolError"):
        response_token(nested.decode())


async def test_explicit_refresh_is_not_lost_when_prepare_is_pending(origin, signer):
    url, state = origin
    keys, sign = signer
    state["bundle"] = sign("2")
    async with AsyncClient(bundle_url=url + "/bundle", trusted_keys=keys) as client:
        client._store.accept(sign("1"))
        client._next_refresh = time.monotonic() + 1000
        prepared, refreshed = await asyncio.gather(client.prepare(), client.refresh())
        assert prepared.revision == "1"
        assert refreshed.revision == "2"
        assert len(state["requests"]) == 1


async def test_async_snapshot_covers_full_operation_deadline(signer):
    keys, sign = signer
    async with AsyncClient(timeout=30, update_timeout=1, trusted_keys=keys) as client:
        client._store.accept(sign(expires=int(time.time()) + 10))
        with pytest.raises(AnchorpassError, match="ConfigExpired"):
            await client.prepare()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO regression")
@pytest.mark.parametrize("lock", [False, True])
def test_nonregular_cache_fails_without_blocking(signer, tmp_path, lock):
    keys, sign = signer
    path = tmp_path / "cache.json"
    os.mkfifo(path.with_name(path.name + ".lock") if lock else path)
    store = BundleStore(keys=keys, cache_path=path)
    started = time.monotonic()
    with pytest.raises(AnchorpassError, match="ConfigInvalid"):
        store.accept(sign()) if lock else store.load_cache()
    assert time.monotonic() - started < 1
