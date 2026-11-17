"""Exercise an installed wheel or sdist through the local CONNECT/TLS fixture."""

from __future__ import annotations

import asyncio
import importlib.metadata
import sys
from pathlib import Path

import anchorpass


def main() -> None:
    proxy, ca_file, checkout = sys.argv[1:]
    assert not Path(anchorpass.__file__).resolve().is_relative_to(Path(checkout).resolve())
    packaged = importlib.metadata.files("anchorpass") or []
    for suffix in ("data/bundle.json", "data/keys.json", "LICENSE", "NOTICE", "py.typed"):
        assert any(str(name).endswith(suffix) for name in packaged), suffix
    anchor = "https://www.google.com/recaptcha/api2/anchor?k=test&co=test&sa=url-action"
    options = {"proxy": proxy, "ca_bundle": ca_file, "timeout": 3}
    assert anchorpass.extract_action(anchor) == "url-action"
    baseline = anchorpass.load_bg(offline=True)
    assert baseline
    with anchorpass.Client(**options) as client:
        assert client.load_bg() == baseline
        assert client.solve(anchor, bg="packed-bg", action="packed-action").token == "fixture-token"
        assert client.solve(anchor).token == "fixture-token"
        assert client.load_bg() == baseline
    assert anchorpass.solve(anchor, **options).token == "fixture-token"

    async def check_async() -> None:
        assert await anchorpass.aload_bg(offline=True) == baseline
        async with anchorpass.AsyncClient(**options) as client:
            assert (await client.solve(anchor, bg="packed-async", action="async-action")).token
            assert (await client.solve(anchor)).token == "fixture-token"
            assert await client.load_bg() == baseline
        assert (await anchorpass.asolve(anchor, **options)).token == "fixture-token"

    asyncio.run(check_async())
    print("PASS: installed Python public API, embedded data, sync/async and isolated overrides")


if __name__ == "__main__":
    main()
