from __future__ import annotations

import asyncio
import time

import pytest

from anchorpass import AnchorpassError, AsyncClient, Client
from anchorpass.cookies import MAX_COOKIE_BYTES, MAX_COOKIES, OperationCookies

ANCHOR = "https://www.google.com/recaptcha/api2/anchor?k=test&co=origin"
COOKIE_URL = "https://www.google.com/recaptcha/api2/anchor"
RELOAD_URL = "https://www.google.com/recaptcha/api2/reload"


def test_cookie_scope_and_expiry():
    cookies = OperationCookies()
    cookies.receive(
        [
            "host=allowed; Path=/recaptcha/",
            "domain=allowed; Domain=.google.com; Path=/recaptcha/",
            "default=allowed",
            "outside=hidden; Path=/other/",
            "boundary=hidden; Path=/recaptcha/api",
            "unrelated=hidden; Domain=unrelated.invalid; Path=/",
            "tls=allowed; Secure; Path=/recaptcha/",
            "expired=hidden; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Path=/",
            "zero=hidden; Max-Age=0; Expires=Thu, 01 Jan 2099 00:00:00 GMT; Path=/",
            "negative=hidden; Max-Age=-1; Path=/",
            "fresh=allowed; Max-Age=60; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Path=/",
        ],
        COOKIE_URL,
        "anchor",
    )
    header = cookies.header(RELOAD_URL, "reload")
    assert header is not None
    assert set(header.split("; ")) == {
        "host=allowed",
        "domain=allowed",
        "default=allowed",
        "tls=allowed",
        "fresh=allowed",
    }
    assert "tls=" not in cookies.header(RELOAD_URL.replace("https:", "http:"), "reload")
    assert cookies.header("https://sub.www.google.com/recaptcha/api2/reload", "reload") == (
        "domain=allowed"
    )
    assert cookies.header("https://unrelated.invalid/recaptcha/api2/reload", "reload") is None
    assert "default=" not in cookies.header("https://www.google.com/recaptcha/api.js", "version")


def test_cookie_replacement_deletion_and_elapsed_expiry(monkeypatch):
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now)
    cookies = OperationCookies()
    cookies.receive(["id=wide; Path=/", "id=narrow; Path=/recaptcha/"], COOKIE_URL, "anchor")
    assert cookies.header(RELOAD_URL, "reload") == "id=narrow; id=wide"
    cookies.receive(["id=removed; Path=/recaptcha/; Max-Age=0"], COOKIE_URL, "anchor")
    assert cookies.header(RELOAD_URL, "reload") == "id=wide"
    cookies.receive(["id=short; Path=/; Max-Age=1"], COOKIE_URL, "anchor")
    assert cookies.header(RELOAD_URL, "reload") == "id=short"
    now += 2
    assert cookies.header(RELOAD_URL, "reload") is None


def test_cookie_limits_are_cumulative_across_responses():
    cookies = OperationCookies()
    cookies.receive(["one=value"] * MAX_COOKIES, COOKIE_URL, "version")
    with pytest.raises(AnchorpassError, match="ResourceLimit"):
        cookies.receive(["one=replaced"], COOKIE_URL, "anchor")
    cookies = OperationCookies()
    cookies.receive(["a=" + "x" * (MAX_COOKIE_BYTES - len("a="))], COOKIE_URL, "version")
    assert len(cookies.header(RELOAD_URL, "reload").encode()) == MAX_COOKIE_BYTES
    with pytest.raises(AnchorpassError, match="ResourceLimit"):
        cookies.receive(["b=value"], COOKIE_URL, "anchor")
    cookies = OperationCookies()
    cookies.receive(
        [f"c{index}=" + "x" * (MAX_COOKIE_BYTES // 4 - len(f"c{index}=")) for index in range(4)],
        COOKIE_URL,
        "version",
    )
    with pytest.raises(AnchorpassError, match="ResourceLimit"):
        cookies.header(RELOAD_URL, "reload")


def test_sync_cookie_required_and_next_operation_and_updates_are_isolated(origin, signer):
    url, state = origin
    keys, sign = signer
    state["bundle"] = sign()
    state["require_cookie"] = True
    state["cookie_headers"]["/bundle"] = ["update=hidden; Path=/"]
    with Client(bundle_url=url + "/bundle", trusted_keys=keys) as client:
        client._origin = url
        assert client.solve(ANCHOR, action="first").token == "token:first"
        assert client.solve(ANCHOR, action="second").token == "token:second"
        client.refresh()
    for method, path, headers, _ in state["requests"]:
        if method == "GET":
            assert "Cookie" not in headers, path
    assert state["connections"] <= 3


async def test_concurrent_cookie_required_flows_are_isolated(origin):
    url, state = origin
    state["require_cookie"] = True
    async with AsyncClient() as client:
        client._origin = url
        actions = [f"operation-{index}" for index in range(4)]
        results = await asyncio.gather(*(client.solve(ANCHOR, action=action) for action in actions))
        assert [result.token for result in results] == [f"token:{action}" for action in actions]
        assert (await client.solve(ANCHOR, action="next")).token == "token:next"
    for method, path, headers, _ in state["requests"]:
        if method == "GET":
            assert "Cookie" not in headers, path


@pytest.mark.parametrize("limit", ["count", "bytes"])
def test_cookie_limits_stop_the_flow(origin, limit):
    url, state = origin
    if limit == "count":
        state["cookie_headers"]["/recaptcha/api.js"] = ["one=value"] * MAX_COOKIES
    else:
        state["cookie_headers"]["/recaptcha/api.js"] = ["one=" + "x" * (MAX_COOKIE_BYTES // 2)]
        state["cookie_headers"]["/recaptcha/api2/anchor"] = ["two=" + "x" * (MAX_COOKIE_BYTES // 2)]
    with Client() as client:
        client._origin = url
        with pytest.raises(AnchorpassError, match="ResourceLimit"):
            client.solve(ANCHOR)
    assert [request[0] for request in state["requests"]] == ["GET", "GET"]
