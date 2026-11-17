from __future__ import annotations

import base64
import contextlib
import datetime
import ipaddress
import select
import socket
import socketserver
import ssl
import struct
import threading

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from anchorpass import AnchorpassError, AsyncClient, Client

ANCHOR = "https://www.google.com/recaptcha/api2/anchor?k=test&co=origin"


@pytest.fixture
def certificate(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    return str(cert_path), context


@contextlib.contextmanager
def proxy_server(
    target_port,
    *,
    socks=False,
    tls=None,
    auth=False,
    credentials=("user", "p@ss"),
    connect_cookie=None,
):
    records = []

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(2)
            try:
                if socks:
                    version, count = self.rfile.read(2)
                    assert version == 5
                    methods = self.rfile.read(count)
                    method = 2 if auth else 0
                    assert method in methods
                    self.wfile.write(bytes([5, method]))
                    if auth:
                        version, size = self.rfile.read(2)
                        username = self.rfile.read(size)
                        size = self.rfile.read(1)[0]
                        password = self.rfile.read(size)
                        valid = (
                            username == credentials[0].encode()
                            and password == credentials[1].encode()
                        )
                        self.wfile.write(bytes([1, 0 if valid else 1]))
                        if not valid:
                            return
                    version, command, _, address_type = self.rfile.read(4)
                    assert version == 5 and command == 1
                    if address_type == 3:
                        host = self.rfile.read(self.rfile.read(1)[0]).decode()
                    elif address_type == 1:
                        host = socket.inet_ntoa(self.rfile.read(4))
                    else:
                        host = socket.inet_ntop(socket.AF_INET6, self.rfile.read(16))
                    port = struct.unpack("!H", self.rfile.read(2))[0]
                    records.append((address_type, host, port))
                    self.wfile.write(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00")
                else:
                    method, destination, _ = self.rfile.readline().decode().split()
                    headers = {}
                    while line := self.rfile.readline().decode().strip():
                        key, value = line.split(":", 1)
                        headers[key.lower()] = value.strip()
                    assert method == "CONNECT"
                    records.append((method, destination, headers))
                    expected = "Basic " + base64.b64encode(":".join(credentials).encode()).decode()
                    if auth and headers.get("proxy-authorization") != expected:
                        self.wfile.write(
                            b"HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n"
                        )
                        return
                    extra = f"Set-Cookie: {connect_cookie}\r\n".encode() if connect_cookie else b""
                    self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n" + extra + b"\r\n")
                with socket.create_connection(("127.0.0.1", target_port), timeout=2) as target:
                    peers = [self.connection, target]
                    while True:
                        readable, _, _ = select.select(peers, [], [], 2)
                        if not readable:
                            return
                        for peer in readable:
                            data = peer.recv(65536)
                            if not data:
                                return
                            (target if peer is self.connection else self.connection).sendall(data)
            except (OSError, ValueError, AssertionError):
                return

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True
        allow_reuse_address = True

        def get_request(self):
            connection, address = super().get_request()
            if tls is not None:
                connection.settimeout(2)
                connection = tls.wrap_socket(connection, server_side=True)
            return connection, address

    server = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield server.server_address[1], records
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


@pytest.mark.parametrize("tls_proxy", [False, True])
@pytest.mark.parametrize("unicode_ca_path", [False, True])
def test_connect_with_auth_and_tls(origin, certificate, tls_proxy, unicode_ca_path, tmp_path):
    _, state = origin
    cert_path, context = certificate
    if unicode_ca_path:
        from pathlib import Path

        unicode_directory = tmp_path / "сертификаты"
        unicode_directory.mkdir()
        unicode_certificate = unicode_directory / "корень.pem"
        unicode_certificate.write_bytes(Path(cert_path).read_bytes())
        cert_path = str(unicode_certificate)
    server = state["server"]
    server.socket = context.wrap_socket(server.socket, server_side=True)
    with proxy_server(server.server_port, tls=context if tls_proxy else None, auth=True) as (
        port,
        records,
    ):
        scheme = "https" if tls_proxy else "http"
        with Client(
            proxy=f"{scheme}://user:p%40ss@127.0.0.1:{port}",
            ca_bundle=cert_path,
            proxy_ca_bundle=cert_path,
        ) as client:
            client._origin = f"https://127.0.0.1:{server.server_port}"
            assert client.solve(ANCHOR, action="test").token == "token:test"
            client.solve(ANCHOR)
        assert records and all(r[0] == "CONNECT" for r in records)
        assert all("Proxy-Authorization" not in request[2] for request in state["requests"])
        assert len(records) == 1


@pytest.mark.parametrize("scheme,expected_type", [("socks5", (1, 4)), ("socks5h", (3,))])
def test_socks_dns_auth(origin, scheme, expected_type):
    url, _ = origin
    from urllib.parse import urlsplit

    with proxy_server(urlsplit(url).port, socks=True, auth=True) as (port, records):
        with Client(proxy=f"{scheme}://user:p%40ss@127.0.0.1:{port}") as client:
            client._origin = url.replace("127.0.0.1", "localhost")
            assert client.solve(ANCHOR).token
        assert records[0][0] in expected_type


async def test_socks_async_remote_dns(origin):
    url, _ = origin
    from urllib.parse import urlsplit

    with proxy_server(urlsplit(url).port, socks=True) as (port, records):
        async with AsyncClient(proxy=f"socks5h://127.0.0.1:{port}") as client:
            client._origin = url.replace("127.0.0.1", "proxy-only.invalid")
            assert (await client.solve(ANCHOR)).token
        assert records[0][1] == "proxy-only.invalid"
    with proxy_server(urlsplit(url).port, socks=True) as (port, records):
        with Client(proxy=f"socks5://127.0.0.1:{port}") as client:
            client._origin = url.replace("127.0.0.1", "proxy-only.invalid")
            with pytest.raises(AnchorpassError, match="TransportError"):
                client.solve(ANCHOR)
        assert records == []


def test_connect_response_cannot_inject_operation_cookies(origin, certificate):
    _, state = origin
    cert_path, context = certificate
    server = state["server"]
    server.socket = context.wrap_socket(server.socket, server_side=True)
    with proxy_server(server.server_port, connect_cookie="proxy-injected=public-test; Path=/") as (
        port,
        _,
    ):
        with Client(proxy=f"http://127.0.0.1:{port}", ca_bundle=cert_path) as client:
            client._origin = f"https://127.0.0.1:{server.server_port}"
            client.solve(ANCHOR)
        assert all(
            "proxy-injected" not in headers.get("Cookie", "")
            for _, _, headers, _ in state["requests"]
        )


@pytest.mark.parametrize("scheme", ["hTtPs", "SoCkS5", "SoCkS5h"])
async def test_async_mixed_case_proxy_scheme_preserves_credentials_and_dns(
    origin, certificate, scheme
):
    _, state = origin
    cert_path, context = certificate
    server = state["server"]
    server.socket = context.wrap_socket(server.socket, server_side=True)
    socks = scheme.lower().startswith("socks")
    with proxy_server(
        server.server_port,
        socks=socks,
        tls=None if socks else context,
        auth=True,
        credentials=("CaseUser", "CasePass"),
    ) as (port, records):
        async with AsyncClient(
            proxy=f"{scheme}://CaseUser:CasePass@127.0.0.1:{port}",
            ca_bundle=cert_path,
            proxy_ca_bundle=cert_path,
        ) as client:
            client._origin = f"https://localhost:{server.server_port}"
            assert (await client.solve(ANCHOR)).token
        if scheme.lower() == "socks5":
            assert records[0][0] in (1, 4)
        elif scheme.lower() == "socks5h":
            assert records[0][0] == 3
        else:
            assert records[0][0] == "CONNECT"
