"""Solve a reCAPTCHA v3 anchor URL with the anchorpass Python client.

Usage:
    python examples/python_solve.py ANCHOR_URL [PROXY_URL]

PROXY_URL is any absolute http/https/socks5/socks5h URL with an explicit host
and port; omit it for a direct route. The action defaults to the URL's `sa`.
"""

import asyncio
import sys

from anchorpass import AsyncClient, Client


def solve_sync(anchor_url: str, proxy: str | None) -> str:
    with Client(proxy=proxy) as client:
        # action="homepage" overrides the URL's sa when the site requires one.
        result = client.solve(anchor_url)
        return result.token


async def solve_async(anchor_url: str, proxy: str | None) -> str:
    async with AsyncClient(proxy=proxy) as client:
        result = await client.solve(anchor_url)
        return result.token


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: python examples/python_solve.py ANCHOR_URL [PROXY_URL]")
    anchor_url = sys.argv[1]
    proxy = sys.argv[2] if len(sys.argv) > 2 else None

    token = solve_sync(anchor_url, proxy)
    print(f"sync  token: {token[:16]}…")
    token = asyncio.run(solve_async(anchor_url, proxy))
    print(f"async token: {token[:16]}…")
    print("verify the token server-side in your application")


if __name__ == "__main__":
    main()
