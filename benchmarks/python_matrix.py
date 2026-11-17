"""Python adapters for the versioned local matrix job/result contract."""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import errno
import importlib.metadata
import json
import platform
import ssl
import tempfile
import time
from pathlib import Path


class ObservedError(Exception):
    def __init__(self, category: str):
        self.category = category
        super().__init__(category)


def resources():
    try:
        return {
            "fds": len(list(Path("/proc/self/fd").iterdir())),
            "threads": len(list(Path("/proc/self/task").iterdir())),
        }
    except OSError:
        return {"fds": None, "threads": None}


def settings(job):
    fixture, profile = job["fixture"], job["profile"]
    route = profile["route"]
    url = fixture["bad_target_url"] if route == "bad_target" else fixture["https_url"]
    proxy = (
        None
        if route == "direct"
        else fixture["proxies"]["https" if route == "bad_target" else route]
    )
    return url, proxy


def check_body(chunks, profile):
    size = 0
    for chunk in chunks:
        size += len(chunk)
        if size > profile["max_response_bytes"]:
            raise ObservedError("response_limit")
        if chunk != b"x" * len(chunk):
            raise ObservedError("invalid_body")
    if size != profile["expected_bytes"]:
        raise ObservedError("invalid_body")
    return size


def classify(error):
    if isinstance(error, ObservedError):
        return error.category
    code = error_code(error)
    if code == 28 or isinstance(error, TimeoutError) or "timeout" in type(error).__name__.lower():
        return "deadline"
    if code in {35, 51, 58, 60, 77, 83, 90, 91}:
        return "tls"
    name = type(error).__name__.lower()
    message = str(error).lower()
    if (
        "ssl" in name
        or "certificate_verify_failed" in message
        or "certificate verify failed" in message
    ):
        return "tls"
    if "407" in message or "proxy authentication" in message:
        return "proxy"
    if "proxy" in name or name == "maxretryerror":
        nested = [getattr(error, field, None) for field in ("reason", "original_error")]
        nested.extend(error.args)
        for cause in nested:
            if isinstance(cause, BaseException) and cause is not error:
                category = classify(cause)
                if category != "unclassified":
                    return category
        if "proxy" in name:
            return "proxy"
    if code in {5, 6, 7, 18, 52, 55, 56, 92, 95, 97}:
        return "transport"
    if isinstance(error, OSError) and error.errno in {
        errno.ECONNRESET,
        errno.EPIPE,
        errno.ECONNREFUSED,
        errno.ETIMEDOUT,
        errno.ENETUNREACH,
        errno.EHOSTUNREACH,
        errno.ECONNABORTED,
    }:
        return "transport"
    network_classes = {
        "ConnectionError",
        "ConnectError",
        "ReadError",
        "WriteError",
        "CloseError",
        "RemoteProtocolError",
        "ClientConnectionError",
        "ClientPayloadError",
        "ServerDisconnectedError",
        "ClientOSError",
        "ChunkedEncodingError",
        "RemoteDisconnected",
        "ProtocolError",
    }
    if any(base.__name__ in network_classes for base in type(error).__mro__):
        return "transport"
    return "unclassified"


def sample(profile, started, size=0, error=None):
    elapsed = time.perf_counter_ns() - started
    category = classify(error) if error else None
    expected = profile["expected_error"]
    ok = category == expected
    if expected == "deadline" and elapsed > (profile["timeout_ms"] + 100) * 1000000:
        ok = False
    item = {"elapsed_ns": elapsed, "ok": ok, "error": category, "bytes": size}
    if error:
        # Exception messages may embed credentials; type and curl code suffice here.
        item["detail"] = f"{type(error).__name__}: code={error_code(error)}"
    return item


def error_code(error):
    code = getattr(error, "code", None)
    if type(error).__module__ == "pycurl" and error.args:
        return error.args[0]
    return code


def remaining(deadline):
    timeout = deadline - time.monotonic()
    if timeout <= 0:
        raise TimeoutError("operation deadline")
    return timeout


def append_body(body, chunk, limit):
    if len(body) + len(chunk) > limit:
        raise ObservedError("response_limit")
    body.extend(chunk)


def flow_requests(job, base):
    from dataclasses import replace
    from types import SimpleNamespace

    from anchorpass.client import _flow

    flow = _flow(
        job["fixture"]["anchor_url"],
        None,
        SimpleNamespace(fields=job["fixture"]["flow_fields"], revision="fixture"),
        base,
        time.monotonic(),
        None,
    )
    request = next(flow)
    while True:
        delay = job["profile"].get("flow_delay_ms", 0)
        if delay:
            separator = "&" if "?" in request.url else "?"
            request = replace(request, url=f"{request.url}{separator}delay_ms={delay}")
        reply = yield request
        try:
            request = flow.send(reply)
        except StopIteration as finished:
            if finished.value.token != "fixture-token":
                raise ObservedError("invalid_body") from None
            return len(finished.value.token.encode())


def flow_headers(request, cookies):
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    cookie_header = cookies.header(request.url, request.stage)
    if cookie_header is not None:
        headers["Cookie"] = cookie_header
    return headers


def aiohttp_session(proxy, fixture, limit, *, disable_retry=True):
    import aiohttp

    origin_ssl = ssl.create_default_context(cafile=fixture["ca_file"])
    if proxy:
        from aiohttp_socks import ProxyConnector

        proxy_ssl = ssl.create_default_context(cafile=fixture["proxy_ca_file"])
        connector = ProxyConnector.from_url(
            proxy.replace("socks5h://", "socks5://").replace("https://", "http://"),
            rdns=not proxy.startswith("socks5:"),
            proxy_ssl=proxy_ssl if proxy.startswith("https:") else None,
            ssl=origin_ssl,
            limit=limit,
            limit_per_host=limit,
        )
    else:
        connector = aiohttp.TCPConnector(ssl=origin_ssl, limit=limit, limit_per_host=limit)
    client = aiohttp.ClientSession(
        connector=connector, trust_env=False, cookie_jar=aiohttp.DummyCookieJar()
    )
    if disable_retry:
        # aiohttp 3.14 has no public switch for its automatic GET retry.
        # Raw transport jobs require no implicit retry; package jobs retain it.
        client._retry_connection = False
    return client


class SyncTransport:
    def __init__(self, job):
        self.job = job
        self.profile = job["profile"]
        self.name = job["candidate"]
        self.base, self.proxy = settings(job)
        fixture = job["fixture"]
        origin_ssl = ssl.create_default_context(cafile=fixture["ca_file"])
        proxy_ssl = ssl.create_default_context(cafile=fixture["proxy_ca_file"])
        if self.name == "python-requests":
            import requests
            from requests.adapters import HTTPAdapter

            class Adapter(HTTPAdapter):
                def proxy_manager_for(self, proxy, **kwargs):
                    if proxy.startswith("https:"):
                        kwargs["proxy_ssl_context"] = proxy_ssl
                    return super().proxy_manager_for(proxy, **kwargs)

            self.client = requests.Session()
            self.client.trust_env = False
            self.client.verify = fixture["ca_file"]
            self.client.mount(
                "https://",
                Adapter(pool_connections=1, pool_maxsize=1, max_retries=0, pool_block=True),
            )
            self.client.proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else {}
        elif self.name == "python-urllib3":
            import urllib3

            options = {
                "ssl_context": origin_ssl,
                "num_pools": 1,
                "maxsize": 1,
                "block": True,
            }
            if self.proxy and self.proxy.startswith("socks"):
                from urllib3.contrib.socks import SOCKSProxyManager

                self.client = SOCKSProxyManager(self.proxy, **options)
            elif self.proxy:
                from urllib.parse import unquote, urlsplit, urlunsplit

                parsed = urlsplit(self.proxy)
                host = parsed.netloc.rsplit("@", 1)[-1]
                proxy_url = urlunsplit((parsed.scheme, host, "", "", ""))
                proxy_headers = urllib3.make_headers(
                    proxy_basic_auth=f"{unquote(parsed.username or '')}:{unquote(parsed.password or '')}"
                )
                self.client = urllib3.ProxyManager(
                    proxy_url,
                    proxy_headers=proxy_headers,
                    proxy_ssl_context=proxy_ssl if parsed.scheme == "https" else None,
                    **options,
                )
            else:
                self.client = urllib3.PoolManager(**options)
        elif self.name == "python-httpx-sync":
            import httpx

            options = {
                "verify": origin_ssl,
                "trust_env": False,
                "limits": httpx.Limits(max_connections=1, max_keepalive_connections=1),
            }
            if self.proxy and self.proxy.startswith("socks"):
                from httpx_socks import SyncProxyTransport

                options["transport"] = SyncProxyTransport.from_url(
                    self.proxy.replace("socks5h://", "socks5://"),
                    rdns=self.proxy.startswith("socks5h:"),
                    **options,
                )
            elif self.proxy:
                options["proxy"] = httpx.Proxy(
                    self.proxy,
                    ssl_context=proxy_ssl if self.proxy.startswith("https:") else None,
                )
            self.client = httpx.Client(**options)
        elif self.name == "python-pycurl":
            import pycurl

            self.client = pycurl.Curl()
            for option, value in (
                (pycurl.PROXY, self.proxy or ""),
                (pycurl.NOPROXY, ""),
                (pycurl.CAINFO, fixture["ca_file"]),
                (pycurl.PROXY_CAINFO, fixture["proxy_ca_file"]),
                (pycurl.FOLLOWLOCATION, False),
                (pycurl.ACCEPT_ENCODING, ""),
                (pycurl.NOSIGNAL, True),
            ):
                self.client.setopt(option, value)
        elif self.name == "python-curl-sync":
            from curl_cffi import CurlOpt
            from curl_cffi.requests import Session

            self.client = Session(
                trust_env=False,
                allow_redirects=False,
                curl_options={
                    CurlOpt.PROXY: self.proxy or "",
                    CurlOpt.NOPROXY: "",
                    CurlOpt.CAINFO: fixture["ca_file"],
                    CurlOpt.PROXY_CAINFO: fixture["proxy_ca_file"],
                },
            )
        else:
            raise ValueError(f"unknown synchronous transport: {self.name}")

    def exchange(self, method, url, profile, deadline, headers=None, data=None):
        timeout = remaining(deadline)
        limit = profile["max_response_bytes"]
        body = bytearray()
        if self.name == "python-requests":
            # Keep cookies exclusively in the operation jar used by flow jobs.
            self.client.cookies.clear()
            with self.client.request(
                method,
                url,
                data=data,
                headers=headers,
                stream=True,
                timeout=timeout,
                allow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    raise ObservedError("http_status")
                for chunk in response.iter_content(16384):
                    append_body(body, chunk, limit)
                return bytes(body), response.raw.headers.getlist("set-cookie")
        if self.name == "python-urllib3":
            import urllib3

            response = self.client.request(
                method,
                url,
                body=data,
                headers=headers,
                preload_content=False,
                decode_content=True,
                redirect=False,
                retries=False,
                pool_timeout=timeout,
                timeout=urllib3.Timeout(total=timeout, connect=timeout, read=timeout),
            )
            try:
                if response.status != 200:
                    raise ObservedError("http_status")
                while chunk := response.read1(16384, decode_content=True):
                    append_body(body, chunk, limit)
                return bytes(body), response.headers.getlist("set-cookie")
            finally:
                if not response.isclosed():
                    response.close()
                response.release_conn()
        if self.name == "python-httpx-sync":
            self.client.cookies.clear()
            with self.client.stream(
                method,
                url,
                content=data,
                headers=headers,
                timeout=timeout,
                follow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    raise ObservedError("http_status")
                for chunk in response.iter_bytes():
                    append_body(body, chunk, limit)
                return bytes(body), response.headers.get_list("set-cookie")
        if self.name == "python-pycurl":
            import math

            import pycurl

            oversized = False
            response_cookies = []

            def receive(chunk):
                nonlocal oversized
                if len(body) + len(chunk) > limit:
                    oversized = True
                    return 0
                body.extend(chunk)
                return len(chunk)

            def receive_header(line):
                if line.lower().startswith(b"set-cookie:"):
                    response_cookies.append(line.split(b":", 1)[1].strip().decode("iso-8859-1"))
                return len(line)

            self.client.setopt(pycurl.URL, url)
            self.client.setopt(pycurl.TIMEOUT_MS, max(1, math.ceil(timeout * 1000)))
            self.client.setopt(pycurl.HTTPGET, True)
            if method == "POST":
                self.client.setopt(pycurl.POSTFIELDS, data or b"")
            self.client.setopt(
                pycurl.HTTPHEADER,
                [f"{name}: {value}" for name, value in (headers or {}).items()],
            )
            self.client.setopt(pycurl.WRITEFUNCTION, receive)
            self.client.setopt(pycurl.HEADERFUNCTION, receive_header)
            try:
                self.client.perform()
            except pycurl.error:
                if oversized:
                    raise ObservedError("response_limit") from None
                raise
            if self.client.getinfo(pycurl.RESPONSE_CODE) != 200:
                raise ObservedError("http_status")
            return bytes(body), response_cookies
        from curl_cffi.curl import CURL_WRITEFUNC_ERROR

        oversized = False

        def receive(chunk):
            nonlocal oversized
            if len(body) + len(chunk) > limit:
                oversized = True
                return CURL_WRITEFUNC_ERROR
            body.extend(chunk)
            return len(chunk)

        try:
            response = self.client.request(
                method,
                url,
                data=data,
                headers=headers,
                timeout=timeout,
                content_callback=receive,
                discard_cookies=True,
            )
        except Exception:
            if oversized:
                raise ObservedError("response_limit") from None
            raise
        if response.status_code != 200:
            raise ObservedError("http_status")
        return bytes(body), response.headers.get_list("set-cookie")

    def request(self, profile):
        deadline = time.monotonic() + profile["timeout_ms"] / 1000
        if profile["kind"] != "flow":
            body, _ = self.exchange("GET", self.base + profile["path"], profile, deadline)
            return check_body([body], profile)
        from anchorpass.cookies import OperationCookies

        cookies = OperationCookies()
        flow = flow_requests(self.job, self.base)
        request = next(flow)
        while True:
            body, received = self.exchange(
                request.method,
                request.url,
                profile,
                deadline,
                flow_headers(request, cookies),
                request.body,
            )
            cookies.receive(received, request.url, request.stage)
            try:
                request = flow.send(body.decode("utf-8"))
            except StopIteration as finished:
                remaining(deadline)
                return finished.value

    def close(self):
        if self.name == "python-urllib3":
            self.client.clear()
        else:
            self.client.close()


class AsyncTransport:
    def __init__(self, job):
        self.job = job
        self.name = job["candidate"]
        self.base, self.proxy = settings(job)
        fixture, profile = job["fixture"], job["profile"]
        limit = profile["concurrency"]
        if self.name == "python-aiohttp":
            self.client = aiohttp_session(self.proxy, fixture, limit)
        elif self.name == "python-httpx-async":
            import httpx

            origin_ssl = ssl.create_default_context(cafile=fixture["ca_file"])
            proxy_ssl = ssl.create_default_context(cafile=fixture["proxy_ca_file"])
            options = {
                "verify": origin_ssl,
                "trust_env": False,
                "limits": httpx.Limits(max_connections=limit, max_keepalive_connections=limit),
            }
            if self.proxy and self.proxy.startswith("socks"):
                from httpx_socks import AsyncProxyTransport

                options["transport"] = AsyncProxyTransport.from_url(
                    self.proxy.replace("socks5h://", "socks5://"),
                    rdns=self.proxy.startswith("socks5h:"),
                    **options,
                )
            elif self.proxy:
                options["proxy"] = httpx.Proxy(
                    self.proxy,
                    ssl_context=proxy_ssl if self.proxy.startswith("https:") else None,
                )
            self.client = httpx.AsyncClient(**options)
        elif self.name == "python-curl-async":
            from curl_cffi import CurlOpt
            from curl_cffi.requests import AsyncSession

            self.client = AsyncSession(
                max_clients=limit,
                trust_env=False,
                allow_redirects=False,
                curl_options={
                    CurlOpt.PROXY: self.proxy or "",
                    CurlOpt.NOPROXY: "",
                    CurlOpt.CAINFO: fixture["ca_file"],
                    CurlOpt.PROXY_CAINFO: fixture["proxy_ca_file"],
                },
            )
        else:
            raise ValueError(f"unknown asynchronous transport: {self.name}")

    async def exchange(self, method, url, profile, deadline, headers=None, data=None):
        timeout = remaining(deadline)
        body = bytearray()
        limit = profile["max_response_bytes"]
        if self.name == "python-aiohttp":
            import aiohttp

            async with self.client.request(
                method,
                url,
                data=data,
                headers=headers,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                if response.status != 200:
                    raise ObservedError("http_status")
                async for chunk in response.content.iter_chunked(16384):
                    append_body(body, chunk, limit)
                return bytes(body), response.headers.getall("set-cookie", [])
        if self.name == "python-httpx-async":
            # An explicit per-operation Cookie header owns the flow state.
            self.client.cookies.clear()
            async with self.client.stream(
                method,
                url,
                content=data,
                headers=headers,
                timeout=timeout,
                follow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    raise ObservedError("http_status")
                async for chunk in response.aiter_bytes():
                    append_body(body, chunk, limit)
                return bytes(body), response.headers.get_list("set-cookie")
        from curl_cffi.curl import CURL_WRITEFUNC_ERROR

        oversized = False

        def receive(chunk):
            nonlocal oversized
            if len(body) + len(chunk) > limit:
                oversized = True
                return CURL_WRITEFUNC_ERROR
            body.extend(chunk)
            return len(chunk)

        try:
            response = await self.client.request(
                method,
                url,
                data=data,
                headers=headers,
                timeout=timeout,
                content_callback=receive,
                discard_cookies=True,
            )
        except Exception:
            if oversized:
                raise ObservedError("response_limit") from None
            raise
        if response.status_code != 200:
            raise ObservedError("http_status")
        return bytes(body), response.headers.get_list("set-cookie")

    async def request(self, profile):
        deadline = time.monotonic() + profile["timeout_ms"] / 1000
        async with asyncio.timeout_at(deadline):
            if profile["kind"] != "flow":
                body, _ = await self.exchange("GET", self.base + profile["path"], profile, deadline)
                return check_body([body], profile)
            from anchorpass.cookies import OperationCookies

            cookies = OperationCookies()
            flow = flow_requests(self.job, self.base)
            request = next(flow)
            while True:
                body, received = await self.exchange(
                    request.method,
                    request.url,
                    profile,
                    deadline,
                    flow_headers(request, cookies),
                    request.body,
                )
                cookies.receive(received, request.url, request.stage)
                try:
                    request = flow.send(body.decode("utf-8"))
                except StopIteration as finished:
                    remaining(deadline)
                    return finished.value

    async def close(self):
        if self.name == "python-httpx-async":
            await self.client.aclose()
        else:
            await self.client.close()


def aiohttp_api_client(job, cache_path):
    """Alias retained for historical manifests; uses the current production backend."""
    from anchorpass import AsyncClient

    return AsyncClient(**api_options(job, cache_path))


def recovery_profile(profile):
    return {
        **profile,
        "path": "/bytes?size=1024",
        "expected_bytes": 1024,
        "expected_error": None,
        "timeout_ms": 2000,
    }


def api_options(job, cache_path):
    fixture, profile = job["fixture"], job["profile"]
    return {
        "proxy": fixture["proxies"]["http"],
        "timeout": profile["timeout_ms"] / 1000,
        "bundle_url": fixture["bundle_url"],
        "updates_proxy": None,
        "trusted_keys": fixture["trusted_keys"],
        "ca_bundle": fixture["ca_file"],
        "proxy_ca_bundle": fixture["proxy_ca_file"],
        "cache_path": cache_path,
        "max_in_flight": profile["concurrency"],
        "offline": True,
    }


def run_sync(job, cache_path):
    profile = job["profile"]
    api = profile["kind"] == "api"

    def worker(count, warmup):
        def create():
            if api:
                from anchorpass import Client

                return Client(**api_options(job, cache_path))
            return SyncTransport(job)

        client = None

        def operation(selected):
            started = time.perf_counter_ns()
            owned = client is None
            current = None
            try:
                current = create() if owned else client
                if api:
                    result = current.solve(job["fixture"]["anchor_url"])
                    if result.token != "fixture-token":
                        raise ObservedError("invalid_body")
                    size = 0
                else:
                    size = current.request(selected)
                if owned:
                    current.close()
                    current = None
                return sample(selected, started, size)
            except Exception as error:
                if owned and current is not None:
                    current.close()
                return sample(selected, started, error=error)

        try:
            if profile["connection_mode"] == "warm":
                client = create()
            for _ in range(warmup):
                observed = operation(profile)
                if not observed["ok"]:
                    raise RuntimeError(f"warmup failed: {observed}")
            # Barrier separates warmup from the timed window across worker threads.
            barrier.wait(timeout=10)
            if not start_event.wait(timeout=10):
                raise TimeoutError("measurement start deadline")
            observed = [operation(profile) for _ in range(count)]
            done.wait(timeout=30)
            if not recover_event.wait(timeout=10):
                raise TimeoutError("recovery start deadline")
            if profile["route"] in {"bad_auth", "bad_tls", "bad_target"}:
                if client is not None:
                    client.close()
                normal_job = {**job, "profile": {**profile, "route": "http"}}
                client = SyncTransport(normal_job)
            recovery = [operation(recovery_profile(profile)) for _ in range(recovery_count)]
            return observed, recovery
        except BaseException:
            barrier.abort()
            done.abort()
            raise
        finally:
            if client is not None:
                client.close()

    import threading

    concurrency = profile["concurrency"]
    barrier = threading.Barrier(concurrency + 1)
    done = threading.Barrier(concurrency + 1)
    start_event, recover_event = threading.Event(), threading.Event()
    recovery_count = profile["recovery_requests"] // concurrency
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [
            pool.submit(
                worker,
                profile["requests"] // concurrency + (index < profile["requests"] % concurrency),
                profile["warmup"] // concurrency + (index < profile["warmup"] % concurrency),
            )
            for index in range(concurrency)
        ]
        try:
            barrier.wait(timeout=10)
            started, cpu = time.perf_counter_ns(), time.process_time_ns()
            start_event.set()
            done.wait(timeout=30)
            wall, cpu = time.perf_counter_ns() - started, time.process_time_ns() - cpu
        except threading.BrokenBarrierError:
            for future in futures:
                future.result(timeout=30)
            raise
        finally:
            start_event.set()
            recover_event.set()
        results = [future.result(timeout=30) for future in futures]
    return (
        [item for result in results for item in result[0]],
        [item for result in results for item in result[1]],
        wall,
        cpu,
    )


async def run_async(job, cache_path):
    profile = job["profile"]
    api = profile["kind"] == "api"

    def create():
        if api:
            if job["candidate"] == "python-api-aiohttp":
                return aiohttp_api_client(job, cache_path)
            from anchorpass import AsyncClient

            return AsyncClient(**api_options(job, cache_path))
        return AsyncTransport(job)

    client = create() if profile["connection_mode"] == "warm" else None

    async def operation(selected):
        started = time.perf_counter_ns()
        owned = client is None
        current = None
        try:
            current = create() if owned else client
            if api:
                result = await current.solve(job["fixture"]["anchor_url"])
                if result.token != "fixture-token":
                    raise ObservedError("invalid_body")
                size = 0
            else:
                size = await current.request(selected)
            if owned:
                await current.close()
                current = None
            return sample(selected, started, size)
        except Exception as error:
            if owned and current is not None:
                await current.close()
            return sample(selected, started, error=error)
        except asyncio.CancelledError:
            if owned and current is not None:
                await current.close()
            raise

    async def batch(count, selected):
        index = 0

        async def worker():
            nonlocal index
            values = []
            while index < count:
                index += 1
                values.append(await operation(selected))
            return values

        return [
            item
            for group in await asyncio.gather(*(worker() for _ in range(profile["concurrency"])))
            for item in group
        ]

    try:
        warmup = await batch(profile["warmup"], profile)
        if any(not item["ok"] for item in warmup):
            raise RuntimeError(f"warmup failed: {warmup}")
        started, cpu = time.perf_counter_ns(), time.process_time_ns()
        observed = await batch(profile["requests"], profile)
        wall, cpu = time.perf_counter_ns() - started, time.process_time_ns() - cpu
        if profile["route"] in {"bad_auth", "bad_tls", "bad_target"}:
            if client is not None:
                await client.close()
            client = AsyncTransport({**job, "profile": {**profile, "route": "http"}})
        recovery = await batch(profile["recovery_requests"], recovery_profile(profile))
    finally:
        if client is not None:
            await client.close()
    return observed, recovery, wall, cpu


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    job = json.loads(args.job.read_text())
    name = job["candidate"]
    async_mode = name.endswith("async") or name in {
        "python-aiohttp",
        "python-api-aiohttp",
    }
    dependency = (
        "requests"
        if name == "python-requests"
        else "urllib3"
        if name == "python-urllib3"
        else "pycurl"
        if name in {"python-pycurl", "python-api-sync"}
        else "aiohttp"
        if name in {"python-aiohttp", "python-api-aiohttp", "python-api-async"}
        else "httpx"
        if "httpx" in name
        else "curl_cffi"
    )
    with tempfile.TemporaryDirectory(prefix="anchorpass-matrix-python-") as directory:
        cache_path = str(Path(directory) / "bundle.json")
        if job["profile"]["kind"] == "api":
            from anchorpass import Client

            options = api_options(job, cache_path)
            options["offline"] = False
            with Client(**options) as seed:
                seed.refresh()
        before = resources()
        result = (
            asyncio.run(run_async(job, cache_path)) if async_mode else run_sync(job, cache_path)
        )
        after = resources()
    observed, recovery, wall, cpu = result
    versions = {dependency: importlib.metadata.version(dependency)}
    notes = []
    if name in {"python-requests", "python-urllib3", "python-httpx-sync"}:
        notes.append(
            "Sync requests/urllib3/httpx timeouts are read-idle; "
            "total-deadline negative control measures this limitation."
        )
    if name in {"python-aiohttp", "python-api-aiohttp", "python-api-async"}:
        versions["aiohttp-socks"] = importlib.metadata.version("aiohttp-socks")
    if name == "python-aiohttp":
        notes.append("Pinned aiohttp adapter disables the private automatic GET retry switch.")
    if name in {"python-api-async", "python-api-aiohttp"}:
        notes.append(
            "Current production AsyncClient uses aiohttp for solves and updates; "
            "native bounded idempotent-method retry is enabled; POST is not retried."
        )
    if name == "python-api-aiohttp":
        notes.append(
            "Alias of the current python-api-async backend, retained for historical manifests."
        )
    if name == "python-api-sync":
        notes.append(
            "Current production Client uses a bounded PycURL handle pool; updates use aiohttp."
        )
    if name in {"python-pycurl", "python-api-sync"}:
        import pycurl

        versions["libcurl"] = pycurl.version_info()[1]
    output = {
        "schema_version": 1,
        "run_id": job["run_id"],
        "candidate": name,
        "status": "ok",
        "runtime": platform.python_version(),
        "versions": versions,
        "mode": "native-async" if async_mode else "bounded-threads",
        "notes": notes,
        "samples": observed,
        "recovery": recovery,
        "warmup_completed": job["profile"]["warmup"],
        "wall_ns": wall,
        "cpu_ns": cpu,
        "resources": {
            "fds_before": before["fds"],
            "fds_after": after["fds"],
            "threads_before": before["threads"],
            "threads_after": after["threads"],
        },
    }
    args.output.write_text(json.dumps(output, indent=2) + "\n")


if __name__ == "__main__":
    main()
