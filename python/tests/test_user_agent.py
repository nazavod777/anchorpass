from __future__ import annotations

import asyncio
from collections import Counter
from urllib.parse import parse_qs, urlsplit

import pytest

import anchorpass.client as client_module
from anchorpass import AnchorpassError, AsyncClient, Client
from anchorpass.core import validate_overrides

ANCHOR = "https://www.google.com/recaptcha/api2/anchor?k=test&co=origin"
USER_AGENT = "Anchorpass test client/1.0"
INVALID_USER_AGENTS = ("", "   ", "a" * 1025, "a\rb", "a\nb", "a\tb", "a\x00b", "a\x7fb", "é", 7)


def test_sync_user_agent_all_steps_next_call_and_updates(origin, signer):
    url, state = origin
    keys, sign = signer
    state["bundle"] = sign()
    with Client(bundle_url=url + "/bundle", trusted_keys=keys) as client:
        client._origin = url
        client.solve(ANCHOR, user_agent=USER_AGENT)
        client.solve(ANCHOR)
        client.refresh()
    steps = [headers for _, path, headers, _ in state["requests"] if path != "/bundle"]
    assert [headers.get("User-Agent") for headers in steps[:3]] == [USER_AGENT] * 3
    assert all(headers.get("User-Agent") != USER_AGENT for headers in steps[3:])
    assert all(
        headers.get("User-Agent") != USER_AGENT
        for _, path, headers, _ in state["requests"]
        if path == "/bundle"
    )


async def test_concurrent_user_agents_are_isolated(origin):
    url, state = origin
    agents = {f"action-{index}": f"Anchorpass concurrent/{index}" for index in range(4)}
    async with AsyncClient() as client:
        client._origin = url
        await asyncio.gather(
            *(
                client.solve(ANCHOR, action=action, user_agent=agent)
                for action, agent in agents.items()
            )
        )
        await client.solve(ANCHOR)
    version_agents = []
    for method, path, headers, body in state["requests"][:-3]:
        if path.endswith(".js"):
            version_agents.append(headers.get("User-Agent"))
        else:
            fields = parse_qs(body.decode() if method == "POST" else urlsplit(path).query)
            assert headers.get("User-Agent") == agents[fields["sa"][0]]
    assert Counter(version_agents) == Counter(agents.values())
    assert all(
        headers.get("User-Agent") not in agents.values()
        for _, _, headers, _ in state["requests"][-3:]
    )


def test_invalid_user_agent_rejected_before_updates(origin):
    url, state = origin
    with Client(bundle_url=url + "/bundle") as client:
        for value in INVALID_USER_AGENTS:
            with pytest.raises(AnchorpassError) as caught:
                client.solve(ANCHOR, user_agent=value)
            assert (caught.value.code, caught.value.stage) == ("InvalidInput", "overrides")
    assert state["requests"] == []


async def test_invalid_async_user_agent_rejected_before_updates(origin):
    url, state = origin
    async with AsyncClient(bundle_url=url + "/bundle") as client:
        for value in INVALID_USER_AGENTS:
            with pytest.raises(AnchorpassError) as caught:
                await client.solve(ANCHOR, user_agent=value)
            assert (caught.value.code, caught.value.stage) == ("InvalidInput", "overrides")
    assert state["requests"] == []


def test_user_agent_accepts_printable_ascii_boundary():
    validate_overrides(ANCHOR, None, None, "x" * 1024)
    validate_overrides(ANCHOR, None, None, "".join(chr(value) for value in range(0x20, 0x7F)))


def test_sync_helper_forwards_user_agent(origin, monkeypatch):
    url, state = origin

    def create(**options):
        assert "user_agent" not in options
        client = Client(**options)
        client._origin = url
        return client

    monkeypatch.setattr(client_module, "Client", create)
    client_module.solve(ANCHOR, user_agent=USER_AGENT)
    assert [headers.get("User-Agent") for _, _, headers, _ in state["requests"]] == [USER_AGENT] * 3


async def test_async_helper_forwards_user_agent(origin, monkeypatch):
    url, state = origin

    def create(**options):
        assert "user_agent" not in options
        client = AsyncClient(**options)
        client._origin = url
        return client

    monkeypatch.setattr(client_module, "AsyncClient", create)
    await client_module.asolve(ANCHOR, user_agent=USER_AGENT)
    assert [headers.get("User-Agent") for _, _, headers, _ in state["requests"]] == [USER_AGENT] * 3
