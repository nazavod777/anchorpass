from __future__ import annotations

import base64
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


@pytest.fixture
def signer():
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    keys = {"test": base64.b64encode(public).decode()}
    fields = json.loads((Path(__file__).parents[2] / "data/upstream-fields.json").read_text())

    def sign(revision="1", expires=None, **overrides):
        payload = {
            "schema_version": 1,
            "protocol_family": "recaptcha-v3/1",
            "revision": revision,
            "issued_at": int(time.time()) - 60,
            "expires_at": int(time.time()) + 3600 if expires is None else expires,
            "fields": fields,
        }
        payload.update(overrides)
        raw = json.dumps(payload, separators=(",", ":")).encode()
        return json.dumps(
            {
                "key_id": "test",
                "payload": base64.b64encode(raw).decode(),
                "signature": base64.b64encode(
                    key.sign(b"anchorpass.bundle.v1\0test\0" + raw)
                ).decode(),
            }
        ).encode()

    return keys, sign


@pytest.fixture
def origin():
    state = {
        "requests": [],
        "bundle": b"{}",
        "delay": 0.0,
        "body": None,
        "cookie_headers": {},
        "require_cookie": False,
        "connections": 0,
    }

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            state["connections"] += 1
            self.connection.settimeout(3)
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        def log_message(self, *args):
            pass

        def do_GET(self):
            state["requests"].append(("GET", self.path, dict(self.headers), b""))
            path = urlsplit(self.path).path
            time.sleep(state["delay"])
            if path.endswith(".js"):
                body = b"https://www.gstatic.com/recaptcha/releases/test-version/recaptcha__en.js"
            elif path.endswith("/anchor"):
                body = b"<input value='anchor&amp;token' id='recaptcha-token'>"
            elif path == "/bundle":
                body = state["bundle"]
            else:
                body = b"ok"
            if state["body"] is not None:
                body = state["body"]
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            cookies = []
            if path.endswith("/anchor"):
                value = (
                    parse_qs(urlsplit(self.path).query).get("sa", [""])[0]
                    if state["require_cookie"]
                    else "private"
                )
                cookies = [f"operation={value}; Path=/"]
            for cookie in state["cookie_headers"].get(path, cookies):
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            state["requests"].append(("POST", self.path, dict(self.headers), body))
            fields = parse_qs(body.decode())
            response = json.dumps(["rresp", "token:" + fields.get("sa", [""])[0]]).encode()
            expected = "operation=" + fields.get("sa", [""])[0]
            status = (
                403 if state["require_cookie"] and self.headers.get("Cookie") != expected else 200
            )
            self.send_response(status)
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state["server"] = server
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", state
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
    assert not thread.is_alive()
