"""One public Python API operation; only the supervising verifier receives the token."""

import asyncio
import json
import sys

from anchorpass import AnchorpassError, AsyncClient, Client


async def async_solve(value):
    async with AsyncClient(proxy=value.get("proxy"), timeout=value["timeout_ms"] / 1000) as client:
        return await client.solve(value["anchor"], action=value["action"])


def main():
    try:
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise ValueError
        value = json.loads(raw)
        if (
            value["mode"] not in {"python-sync", "python-async"}
            or type(value["timeout_ms"]) is not int
            or not 0 < value["timeout_ms"] <= 60000
        ):
            raise ValueError
        if value["mode"] == "python-async":
            result = asyncio.run(async_solve(value))
        else:
            with Client(proxy=value.get("proxy"), timeout=value["timeout_ms"] / 1000) as client:
                result = client.solve(value["anchor"], action=value["action"])
        print(json.dumps({"token": result.token, "bundle_revision": result.bundle_revision}))
        return 0
    except AnchorpassError as error:
        print(json.dumps({"error": error.code, "stage": error.stage}))
        return 1
    except (ValueError, KeyError, TypeError):
        print('{"error":"InvalidInput"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
