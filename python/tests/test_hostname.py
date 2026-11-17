from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

import anchorpass.client as client_module
from anchorpass import AnchorpassError, AsyncClient, Client
from anchorpass.core import resolve_origin, validate_overrides

VECTORS = json.loads((Path(__file__).parents[2] / "spec/vectors/solve-options.json").read_text())
ORIGINAL_CO = "aHR0cHM6Ly9vbGQuZXhhbXBsZTo0NDM."
ANCHOR = "https://www.google.com/recaptcha/api2/anchor?" + urlencode(
    {"k": "test", "co": ORIGINAL_CO}
)


@pytest.mark.parametrize("case", VECTORS["hostname"])
def test_shared_hostname_vectors(case):
    if case["valid"]:
        assert resolve_origin(case["co"], case["hostname"]) == case["expected_co"]
    else:
        with pytest.raises(AnchorpassError, match="InvalidInput at overrides"):
            resolve_origin(case["co"], case["hostname"])


@pytest.mark.parametrize("case", VECTORS["user_agent"])
def test_shared_user_agent_vectors(case):
    if case["valid"]:
        validate_overrides(ANCHOR, None, None, case["value"])
    else:
        with pytest.raises(AnchorpassError, match="InvalidInput at overrides"):
            validate_overrides(ANCHOR, None, None, case["value"])


def fields_by_operation(requests):
    return [
        parse_qs(body.decode() if method == "POST" else urlsplit(path).query)
        for method, path, _, body in requests
        if not path.endswith(".js") and path != "/bundle"
    ]


def test_hostname_changes_anchor_and_reload_only_for_selected_call(origin, signer):
    url, state = origin
    keys, sign = signer
    state["bundle"] = sign()
    expected = resolve_origin(ORIGINAL_CO, "Example.COM")
    with Client(bundle_url=url + "/bundle", trusted_keys=keys) as client:
        client._origin = url
        client.solve(ANCHOR, hostname="Example.COM", user_agent="Hostname-Test/1.0")
        client.solve(ANCHOR)
        client.refresh()
    assert [fields["co"] for fields in fields_by_operation(state["requests"])] == [
        [expected],
        [expected],
        [ORIGINAL_CO],
        [ORIGINAL_CO],
    ]
    assert parse_qs(urlsplit(ANCHOR).query)["co"] == [ORIGINAL_CO]
    assert all(path == "/bundle" for _, path, _, _ in state["requests"] if "bundle" in path)


async def test_concurrent_hostname_overrides_are_isolated(origin):
    url, state = origin
    hosts = {f"operation-{index}": f"site-{index}.example" for index in range(4)}
    async with AsyncClient() as client:
        client._origin = url
        results = await asyncio.gather(
            *(client.solve(ANCHOR, action=action, hostname=host) for action, host in hosts.items())
        )
        assert [result.token for result in results] == [f"token:{action}" for action in hosts]
        await client.solve(ANCHOR)
    observed = fields_by_operation(state["requests"])
    for fields in observed[:-2]:
        assert fields["co"] == [resolve_origin(ORIGINAL_CO, hosts[fields["sa"][0]])]
    assert [fields["co"] for fields in observed[-2:]] == [[ORIGINAL_CO], [ORIGINAL_CO]]


def test_invalid_hostname_and_origin_rejected_before_network(origin):
    url, state = origin
    cases = [case for case in VECTORS["hostname"] if not case["valid"]]
    cases.extend({"co": ORIGINAL_CO, "hostname": value} for value in (7, [], "a" * 64 + ".com"))
    with Client(bundle_url=url + "/bundle") as client:
        for case in cases:
            anchor = "https://www.google.com/recaptcha/api2/anchor?" + urlencode(
                {"k": "test", "co": case["co"]}
            )
            with pytest.raises(AnchorpassError, match="InvalidInput"):
                client.solve(anchor, hostname=case["hostname"])
    assert state["requests"] == []


async def test_invalid_async_hostname_rejected_before_network(origin):
    url, state = origin
    async with AsyncClient(bundle_url=url + "/bundle") as client:
        with pytest.raises(AnchorpassError, match="InvalidInput at overrides"):
            await client.solve(ANCHOR, hostname="https://site.example")
    assert state["requests"] == []


def test_omitted_hostname_preserves_legacy_origin():
    for value in ("origin", "dGVzdA", "%%%"):
        assert resolve_origin(value, None) == value


def test_sync_helper_forwards_hostname(origin, monkeypatch):
    url, state = origin

    def create(**options):
        assert "hostname" not in options
        client = Client(**options)
        client._origin = url
        return client

    monkeypatch.setattr(client_module, "Client", create)
    client_module.solve(ANCHOR, hostname="site.example")
    assert all(
        fields["co"] == [resolve_origin(ORIGINAL_CO, "site.example")]
        for fields in fields_by_operation(state["requests"])
    )


async def test_async_helper_forwards_hostname(origin, monkeypatch):
    url, state = origin

    def create(**options):
        assert "hostname" not in options
        client = AsyncClient(**options)
        client._origin = url
        return client

    monkeypatch.setattr(client_module, "AsyncClient", create)
    await client_module.asolve(ANCHOR, hostname="site.example")
    assert all(
        fields["co"] == [resolve_origin(ORIGINAL_CO, "site.example")]
        for fields in fields_by_operation(state["requests"])
    )
