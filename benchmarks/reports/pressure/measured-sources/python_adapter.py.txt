"""Public Python async API; finite JSON-line operations for the pressure driver."""

from __future__ import annotations

import asyncio
import json
import sys
import time

from anchorpass import AsyncClient


async def main():
    job = json.loads(open(sys.argv[1]).read())
    fixture = job["fixture"]
    client = None
    try:
        while line := await asyncio.to_thread(sys.stdin.readline):
            command = json.loads(line)
            op = command["op"]
            if op == "create":
                if client:
                    await client.close()
                client = AsyncClient(
                    proxy=fixture["proxies"][command["proxy_route"]],
                    timeout=command["timeout_ms"] / 1000,
                    update_timeout=command["timeout_ms"] / 1000,
                    max_in_flight=command["max_in_flight"],
                    refresh_interval=command["refresh_interval"],
                    offline=command["offline"],
                    cache_path=job["cache_path"],
                    bundle_url=command.get("bundle_url", fixture["bundle_url"]),
                    trusted_keys=fixture["trusted_keys"],
                    ca_bundle=fixture["ca_file"],
                )
                result = {"ok": True}
            elif op == "close":
                if client:
                    await client.close()
                    client = None
                result = {"ok": True}
            elif op == "settle":
                await asyncio.sleep(command["milliseconds"] / 1000)
                result = {"ok": True}
            else:
                count, concurrency = command["count"], command["concurrency"]
                if not 0 < count <= 128 or not 0 < concurrency <= 8:
                    raise ValueError("operation budget exceeded")
                samples = [None] * count
                next_index = 0

                async def worker(count=count, op=op, client=client, samples=samples):
                    nonlocal next_index
                    while next_index < count:
                        index = next_index
                        next_index += 1
                        started = time.perf_counter_ns()
                        error, value = None, None
                        try:
                            if op == "load":
                                value = await client.load_bg()
                            elif op == "refresh":
                                await client.refresh()
                            elif op == "solve":
                                value = (await client.solve(fixture["anchor_url"])).token
                            else:
                                raise ValueError("invalid operation")
                        except Exception as failure:
                            error = getattr(failure, "code", type(failure).__name__)
                        samples[index] = {
                            "elapsed_ns": time.perf_counter_ns() - started,
                            "error": error,
                            "value": value,
                        }

                await asyncio.gather(*(worker() for _ in range(min(count, concurrency))))
                result = {"ok": True, "samples": samples}
            print(json.dumps(result), flush=True)
    finally:
        if client:
            await client.close()


if __name__ == "__main__":
    asyncio.run(main())
