"""Local, sequential warm-session pilot; not a universal client ranking."""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import platform
import socket
import statistics
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import aiohttp
import httpx
import requests
from curl_cffi.requests import AsyncSession, Session

SAMPLES = 30
ROUNDS = 3
PAYLOAD = b"anchorpass-local-benchmark"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.connection.settimeout(5)

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.end_headers()
        self.wfile.write(PAYLOAD)

    def log_message(self, *args):
        pass


def measure_sync(factory, url):
    with factory() as client:
        assert client.get(url, timeout=5).content == PAYLOAD
        samples = []
        for _ in range(SAMPLES):
            started = time.perf_counter()
            assert client.get(url, timeout=5).content == PAYLOAD
            samples.append(time.perf_counter() - started)
        return samples


async def measure_async(factory, url):
    async with factory() as client:

        async def get():
            if isinstance(client, aiohttp.ClientSession):
                async with client.get(url) as response:
                    return await response.read()
            return (await client.get(url, timeout=5)).content

        assert await get() == PAYLOAD
        samples = []
        for _ in range(SAMPLES):
            started = time.perf_counter()
            assert await get() == PAYLOAD
            samples.append(time.perf_counter() - started)
        return samples


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    all_samples = {}
    try:
        sync = {
            "requests": requests.Session,
            "httpx-sync": lambda: httpx.Client(trust_env=False),
            "curl_cffi-sync": Session,
        }
        async_clients = {
            "aiohttp": lambda: aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)),
            "httpx-async": lambda: httpx.AsyncClient(trust_env=False),
            "curl_cffi-async": AsyncSession,
        }
        for round_index in range(ROUNDS):
            for group, run in (
                (sync, measure_sync),
                (async_clients, lambda f, u: asyncio.run(measure_async(f, u))),
            ):
                names = list(group)
                names = names[round_index:] + names[:round_index]
                for name in names:
                    all_samples.setdefault(name, []).extend(run(group[name], url))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    summaries = {}
    for name, values in all_samples.items():
        ordered = sorted(values)
        summaries[name] = {
            "count": len(values),
            "median_seconds": statistics.median(values),
            "p95_seconds": ordered[int(len(values) * 0.95) - 1],
        }
    result = {
        "scope": "local HTTP/1.1 sequential warm sessions; proxy/TLS/concurrency/RSS not benchmarked",
        "environment": platform.platform(),
        "python": platform.python_version(),
        "versions": {
            name: importlib.metadata.version(name)
            for name in ["requests", "aiohttp", "httpx", "curl_cffi"]
        },
        "samples_seconds": all_samples,
        "summary": summaries,
    }
    path = Path(__file__).with_name("reports") / "python-pilot.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(summaries, indent=2))


if __name__ == "__main__":
    main()
