"""Bounded loopback-only HTTP/TLS/CONNECT/SOCKS fixture for the matrix runner."""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import datetime as dt
import gzip
import ipaddress
import json
import resource
import socket
import ssl
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from cryptography.x509.oid import NameOID

MAX_CONNECTIONS = 512
HEADER_LIMIT = 32768
BODY_LIMIT = 1048576
IO_TIMEOUT = 5
ROOT = Path(__file__).resolve().parents[1]


def certificates(directory: Path) -> dict[str, ssl.SSLContext | str]:
    now = dt.datetime.now(dt.UTC)

    def authority(name: str):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Anchorpass {name} CA")])
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=7))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
            )
            .add_extension(
                x509.KeyUsage(False, False, False, False, False, True, True, False, False),
                critical=True,
            )
            .sign(key, hashes.SHA256())
        )
        path = directory / f"{name}-ca.pem"
        path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        return key, cert, path

    origin_key, origin_ca, origin_path = authority("origin")
    proxy_key, proxy_ca, proxy_path = authority("proxy")

    def leaf(name: str, key, ca):
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
            .issuer_name(ca.subject)
            .public_key(private.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=7))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False
            )
            .add_extension(
                x509.KeyUsage(True, False, True, False, False, False, False, False, False),
                critical=True,
            )
            .add_extension(
                x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
            )
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.DNSName("localhost"),
                        x509.DNSName("www.google.com"),
                        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    ]
                ),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        cert_path, key_path = directory / f"{name}.pem", directory / f"{name}.key"
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            private.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        key_path.chmod(0o600)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_path, key_path)
        return context

    return {
        "origin": leaf("origin", origin_key, origin_ca),
        "proxy": leaf("proxy", proxy_key, proxy_ca),
        "bad_target": leaf("bad-target", proxy_key, proxy_ca),
        "bad_proxy": leaf("bad-proxy", origin_key, origin_ca),
        "ca_file": str(origin_path),
        "proxy_ca_file": str(proxy_path),
    }


class Fixture:
    def __init__(self, directory: Path):
        self.directory = directory
        self.certs = certificates(directory)
        self.servers: list[asyncio.Server] = []
        self.tasks: set[asyncio.Task] = set()
        self.writers: set[asyncio.StreamWriter] = set()
        self.metrics: Counter = Counter()
        self.operation_id = 0
        self.allowed_ports: set[int] = set()
        self.last_tick = time.monotonic()
        self.loop_lag_ns = 0
        self.cpu_baseline_ns = self.cpu_ns()
        self.wall_baseline_ns = time.perf_counter_ns()
        private = ed25519.Ed25519PrivateKey.generate()
        public = private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        sample = json.loads(
            base64.b64decode(json.loads((ROOT / "data/bundled/bundle.json").read_text())["payload"])
        )
        sample.update(
            issued_at=int(time.time()) - 10, expires_at=int(time.time()) + 3600, revision="2"
        )
        sample["fields"]["bg"] = "matrix-bg"
        self.flow_fields = dict(sample["fields"])
        payload = json.dumps(sample, separators=(",", ":")).encode()
        self.bundle = json.dumps(
            {
                "key_id": "fixture",
                "payload": base64.b64encode(payload).decode(),
                "signature": base64.b64encode(
                    private.sign(b"anchorpass.bundle.v1\0fixture\0" + payload)
                ).decode(),
            }
        ).encode()
        self.keys = {"fixture": base64.b64encode(public).decode()}

    async def start(self) -> dict:
        async def listen(kind: str, tls=None):
            server = await asyncio.start_server(
                lambda r, w: self.accept(kind, r, w),
                "127.0.0.1",
                0,
                ssl=tls,
                limit=HEADER_LIMIT,
                **({"ssl_handshake_timeout": IO_TIMEOUT} if tls else {}),
            )
            self.servers.append(server)
            return server.sockets[0].getsockname()[1]

        self.origin_port = await listen("origin", self.certs["origin"])
        bad_target = await listen("origin", self.certs["bad_target"])
        self.allowed_ports.update((self.origin_port, bad_target))
        http_proxy = await listen("connect")
        https_proxy = await listen("connect", self.certs["proxy"])
        bad_proxy = await listen("connect", self.certs["bad_proxy"])
        socks = await listen("socks")
        metadata = {
            "https_url": f"https://localhost:{self.origin_port}",
            "bad_target_url": f"https://localhost:{bad_target}",
            "ca_file": self.certs["ca_file"],
            "proxy_ca_file": self.certs["proxy_ca_file"],
            "proxies": {
                "http": f"http://bench:password@127.0.0.1:{http_proxy}",
                "https": f"https://bench:password@localhost:{https_proxy}",
                "socks5": f"socks5://bench:password@127.0.0.1:{socks}",
                "socks5h": f"socks5h://bench:password@127.0.0.1:{socks}",
                "bad_auth": f"http://bench:wrong@127.0.0.1:{http_proxy}",
                "bad_tls": f"https://bench:password@localhost:{bad_proxy}",
            },
            "anchor_url": "https://www.google.com/recaptcha/api2/anchor?k=test-key&co=dGVzdA&sa=matrix-action",
            "bundle_url": f"https://localhost:{self.origin_port}/bundle",
            "trusted_keys": self.keys,
            "flow_fields": self.flow_fields,
        }
        self.lag_task = asyncio.create_task(self.measure_lag())
        return metadata

    async def measure_lag(self):
        interval = 0.01
        while True:
            started = time.monotonic()
            await asyncio.sleep(interval)
            self.loop_lag_ns = max(
                self.loop_lag_ns, int(max(0, time.monotonic() - started - interval) * 1e9)
            )

    async def accept(self, kind: str, reader, writer):
        if len(self.tasks) >= MAX_CONNECTIONS:
            self.metrics["shed_connections"] += 1
            writer.close()
            return
        task = asyncio.current_task()
        self.tasks.add(task)
        self.writers.add(writer)
        self.metrics[f"{kind}_connections"] += 1
        self.metrics["peak_connections"] = max(self.metrics["peak_connections"], len(self.tasks))
        writer.get_extra_info("socket").setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            if kind == "origin":
                await self.origin(reader, writer)
            elif kind == "connect":
                await self.connect(reader, writer)
            else:
                await self.socks(reader, writer)
        except (
            OSError,
            ValueError,
            TimeoutError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ):
            self.metrics["connection_errors"] += 1
        finally:
            self.tasks.discard(task)
            self.writers.discard(writer)
            writer.close()

    async def headers(self, reader):
        async with asyncio.timeout(IO_TIMEOUT):
            raw = await reader.readuntil(b"\r\n\r\n")
        lines = raw.decode("iso-8859-1").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) != 3:
            raise ValueError("invalid request line")
        headers = {}
        for line in lines[1:]:
            if line:
                name, value = line.split(":", 1)
                headers[name.lower()] = value.strip()
        return parts, headers

    async def origin(self, reader, writer):
        for _ in range(4096):
            (method, target, _), headers = await self.headers(reader)
            length = int(headers.get("content-length", "0"))
            if not 0 <= length <= BODY_LIMIT:
                raise ValueError("request body limit")
            async with asyncio.timeout(IO_TIMEOUT):
                body = await reader.readexactly(length)
            url = urlsplit(target)
            params = parse_qs(url.query)
            self.metrics["requests"] += 1
            self.metrics[f"path:{url.path}"] += 1
            if "proxy-authorization" in headers:
                self.metrics["credential_leaks"] += 1
            if url.path == "/reset":
                writer.transport.abort()
                return
            status, encoding, cookie = 200, None, None
            if url.path in {"/bytes", "/gzip"}:
                size = int(params.get("size", ["1024"])[0])
                if not 0 <= size <= BODY_LIMIT:
                    raise ValueError("response size limit")
                data = b"x" * size
                status = int(params.get("status", ["200"])[0])
                if url.path == "/gzip":
                    data, encoding = gzip.compress(data), "gzip"
            elif url.path == "/bundle":
                data = self.bundle
            elif url.path.endswith(".js"):
                if self.operation_cookie(headers):
                    self.metrics["protocol_errors"] += 1
                    status = 400
                self.operation_id += 1
                cookie = f"operation={self.operation_id}; Path=/"
                data = b"https://www.gstatic.com/recaptcha/releases/matrix-version/recaptcha__en.js"
            elif url.path.endswith("/anchor"):
                operation = self.operation_cookie(headers) or "stateless"
                valid = bool(self.operation_cookie(headers)) and params.get("sa") == [
                    "matrix-action"
                ]
                if not valid:
                    self.metrics["protocol_errors"] += 1
                    status = 400
                data = f'<input id="recaptcha-token" value="anchor-{operation}">'.encode()
            elif url.path.endswith("/reload"):
                fields = parse_qs(body.decode())
                operation = self.operation_cookie(headers) or "stateless"
                expected = {
                    **self.flow_fields,
                    "c": f"anchor-{operation}",
                    "bg": "matrix-bg",
                    "sa": "matrix-action",
                    "k": "test-key",
                    "co": "dGVzdA",
                    "v": "matrix-version",
                }
                if (
                    method != "POST"
                    or not self.operation_cookie(headers)
                    or any(fields.get(k) != [v] for k, v in expected.items())
                ):
                    self.metrics["protocol_errors"] += 1
                    status = 400
                data = b')]}\'\n["rresp","fixture-token"]'
            else:
                status, data = 404, b"missing"
            await asyncio.sleep(int(params.get("delay_ms", ["0"])[0]) / 1000)
            extra = b""
            if encoding:
                extra += f"Content-Encoding: {encoding}\r\n".encode()
            if cookie:
                extra += f"Set-Cookie: {cookie}\r\n".encode()
            writer.write(
                f"HTTP/1.1 {status} Fixture\r\nContent-Length: {len(data)}\r\n".encode()
                + extra
                + b"\r\n"
            )
            drip = int(params.get("drip_ms", ["0"])[0])
            if drip:
                for byte in data:
                    writer.write(bytes([byte]))
                    await writer.drain()
                    await asyncio.sleep(drip / 1000)
            else:
                writer.write(data)
                await writer.drain()
            if headers.get("connection", "").lower() == "close":
                return

    @staticmethod
    def operation_cookie(headers):
        for item in headers.get("cookie", "").split(";"):
            if item.strip().startswith("operation="):
                return item.strip().split("=", 1)[1]
        return ""

    def target(self, host, port):
        if host == "www.google.com" and port == 443:
            return "127.0.0.1", self.origin_port
        if host in {"localhost", "127.0.0.1", "::1"} and port in self.allowed_ports:
            return "127.0.0.1", port
        self.metrics["blocked_external_targets"] += 1
        raise ValueError("fixture target rejected")

    async def connect(self, reader, writer):
        (method, target, _), headers = await self.headers(reader)
        authorization = headers.get("proxy-authorization", "").split(" ", 1)
        valid_auth = (
            len(authorization) == 2
            and authorization[0].lower() == "basic"
            and authorization[1] == base64.b64encode(b"bench:password").decode()
        )
        if method != "CONNECT" or not valid_auth:
            self.metrics["proxy_auth_rejected"] += 1
            writer.write(b"HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            return
        parsed = urlsplit("http://" + target)
        host, port = self.target(parsed.hostname, parsed.port)
        self.metrics["connect_tunnels"] += 1
        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), IO_TIMEOUT
        )
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        await self.relay(reader, writer, upstream_reader, upstream_writer)

    async def socks(self, reader, writer):
        async with asyncio.timeout(IO_TIMEOUT):
            version, methods = await reader.readexactly(2)
            offered = await reader.readexactly(methods)
            if version != 5 or 2 not in offered:
                writer.write(b"\x05\xff")
                return
            writer.write(b"\x05\x02")
            await writer.drain()
            version, length = await reader.readexactly(2)
            username = await reader.readexactly(length)
            length = (await reader.readexactly(1))[0]
            password = await reader.readexactly(length)
            if version != 1 or username != b"bench" or password != b"password":
                self.metrics["proxy_auth_rejected"] += 1
                writer.write(b"\x01\x01")
                return
            writer.write(b"\x01\x00")
            await writer.drain()
            version, command, reserved, kind = await reader.readexactly(4)
            if version != 5 or command != 1 or reserved:
                raise ValueError("invalid SOCKS request")
            if kind == 3:
                length = (await reader.readexactly(1))[0]
                host = (await reader.readexactly(length)).decode()
            elif kind in (1, 4):
                host = str(ipaddress.ip_address(await reader.readexactly(4 if kind == 1 else 16)))
            else:
                raise ValueError("invalid SOCKS address")
            port = int.from_bytes(await reader.readexactly(2))
            self.metrics[f"socks_atyp:{kind}"] += 1
            host, port = self.target(host, port)
            upstream_reader, upstream_writer = await asyncio.open_connection(host, port)
            writer.write(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00")
            await writer.drain()
        await self.relay(reader, writer, upstream_reader, upstream_writer)

    async def relay(self, reader, writer, upstream_reader, upstream_writer):
        async def copy(source, destination):
            while chunk := await asyncio.wait_for(source.read(65536), IO_TIMEOUT):
                destination.write(chunk)
                await asyncio.wait_for(destination.drain(), IO_TIMEOUT)

        tasks = [
            asyncio.create_task(copy(reader, upstream_writer)),
            asyncio.create_task(copy(upstream_reader, writer)),
        ]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            upstream_writer.close()

    async def command(self, command):
        if command == "reset":
            deadline = time.monotonic() + IO_TIMEOUT
            while self.tasks and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            if self.tasks:
                raise RuntimeError("fixture connections did not drain before reset")
            self.metrics.clear()
            self.loop_lag_ns = 0
            self.cpu_baseline_ns = self.cpu_ns()
            self.wall_baseline_ns = time.perf_counter_ns()
        return {
            **self.metrics,
            "active_connections": len(self.tasks),
            "loop_lag_peak_ns": self.loop_lag_ns,
            "cpu_ns": self.cpu_ns() - self.cpu_baseline_ns,
            "wall_ns": time.perf_counter_ns() - self.wall_baseline_ns,
        }

    @staticmethod
    def cpu_ns():
        usage = resource.getrusage(resource.RUSAGE_SELF)
        return int((usage.ru_utime + usage.ru_stime) * 1000000000)

    async def close(self):
        self.lag_task.cancel()
        for server in self.servers:
            server.close()
        for writer in tuple(self.writers):
            writer.close()
        for task in tuple(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, self.lag_task, return_exceptions=True)
        await asyncio.gather(*(server.wait_closed() for server in self.servers))


async def main(directory):
    fixture = Fixture(directory)
    metadata = await fixture.start()
    (directory / "fixture.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(json.dumps({"ready": True, "fixture": metadata}), flush=True)
    try:
        while line := await asyncio.to_thread(sys.stdin.readline):
            command = json.loads(line)["command"]
            if command == "stop":
                break
            print(json.dumps(await fixture.command(command)), flush=True)
    finally:
        await fixture.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(BrokenPipeError):
        asyncio.run(main(args.directory.resolve()))
