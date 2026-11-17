"""Bounded feasibility probe against Google's public demonstration site.

No token, proxy URL or response body is printed. This is an investigative
prototype, not the Anchorpass public API or a transport selection benchmark.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import re
import sys
import time
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlencode, urlsplit

from curl_cffi import CurlOpt
from curl_cffi.curl import CURL_WRITEFUNC_ERROR
from curl_cffi.requests import Session
from curl_cffi.requests.exceptions import RequestException

DEMO_ORIGIN = "https://recaptcha-demo.appspot.com"
DEMO_PAGE = DEMO_ORIGIN + "/recaptcha-v3-request-scores.php"
VERIFY_URL = DEMO_ORIGIN + "/recaptcha-v3-verify.php"
CAPTCHA_ORIGIN = "https://www.google.com"
ACTION = "examples/v3scores"
MAX_BODY_BYTES = 1048576
DEFAULT_TIMEOUT = 30.0
PROXY_SCHEMES = {"http", "https", "socks5", "socks5h"}
SOURCE_REVISION = "910c3def3d69e68d42d86ccba5a1b4cf105ee1ed"


class ProbeError(Exception):
    """An error with a safe, stable diagnostic message."""


def validate_proxy(value: str | None) -> str:
    """Validate without ever converting an invalid proxy to a direct route."""
    if value is None:
        return ""
    try:
        if not value or any(char.isspace() or ord(char) < 32 for char in value):
            raise ValueError
        parsed = urlsplit(value)
        if (
            parsed.scheme not in PROXY_SCHEMES
            or not parsed.hostname
            or parsed.port is None
            or parsed.port <= 0
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or "?" in value
            or "#" in value
            or re.search(r"%(?![0-9a-fA-F]{2})", value)
        ):
            raise ValueError
        for credential in (parsed.username, parsed.password):
            if credential is not None:
                decoded = unquote(credential, errors="strict")
                if not decoded.isascii() or any(ord(char) < 32 for char in decoded):
                    raise ValueError
    except (ValueError, UnicodeError):
        raise ProbeError("InvalidProxy") from None
    return value


class AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.token: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "input" and values.get("id") == "recaptcha-token":
            self.token = values.get("value")


def form_fields() -> dict[str, str]:
    path = Path(__file__).resolve().parents[1] / "data" / "upstream-fields.json"
    result: dict[str, str] = json.loads(path.read_text())
    return result


def fetch(
    session: Session,
    deadline: float,
    method: str,
    url: str,
    *,
    data: bytes | None = None,
) -> str:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ProbeError("DeadlineExceeded")
    body = bytearray()
    oversized = False

    def receive(chunk: bytes) -> int:
        nonlocal oversized
        if len(body) + len(chunk) > MAX_BODY_BYTES:
            oversized = True
            return CURL_WRITEFUNC_ERROR
        body.extend(chunk)
        return len(chunk)

    try:
        response = session.request(
            method,
            url,
            data=data,
            timeout=remaining,
            allow_redirects=False,
            content_callback=receive,
        )
    except RequestException:
        if oversized:
            raise ProbeError("ResponseTooLarge") from None
        if time.monotonic() >= deadline:
            raise ProbeError("DeadlineExceeded") from None
        raise ProbeError("TransportError") from None
    if response.status_code != 200:
        raise ProbeError(f"HttpStatusError:{response.status_code}")
    try:
        return body.decode("utf-8", errors="strict")
    except UnicodeError:
        raise ProbeError("ResponseInvalidUtf8") from None


def probe(
    proxy: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    *,
    include_action: bool = False,
) -> dict[str, object]:
    route = validate_proxy(proxy)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ProbeError("InvalidTimeout")
    started = time.monotonic()
    deadline = started + timeout
    stages: list[str] = []
    result: dict[str, object] = {
        "source_revision": SOURCE_REVISION,
        "mode": "action-field-probe" if include_action else "upstream",
        "proxy_configured": proxy is not None,
        "stages": stages,
    }
    with Session(
        trust_env=False,
        curl_options={CurlOpt.PROXY: route, CurlOpt.NOPROXY: ""},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    ) as session:
        page = fetch(session, deadline, "GET", DEMO_PAGE)
        stages.append("demo_page")
        match = re.search(r"api\.js\?render=([A-Za-z0-9_-]+)", page)
        if match is None:
            raise ProbeError("DemoSiteKeyNotFound")
        site_key = match.group(1)
        script = fetch(
            session,
            deadline,
            "GET",
            CAPTCHA_ORIGIN + "/recaptcha/api.js?" + urlencode({"render": site_key}),
        )
        stages.append("version")
        versions = re.findall(r"releases/([^/'\"\\)]+)/", script)
        if not versions:
            raise ProbeError("RenderVersionNotFound")
        version = Counter(versions).most_common(1)[0][0]
        origin = base64.b64encode((DEMO_ORIGIN + ":443").encode()).decode().replace("=", ".")
        params = {
            "ar": "1",
            "k": site_key,
            "co": origin,
            "hl": "en",
            "v": version,
            "size": "invisible",
            "cb": "anchorpass-feasibility",
        }
        if include_action:
            params["sa"] = ACTION
        anchor = fetch(
            session,
            deadline,
            "GET",
            CAPTCHA_ORIGIN + "/recaptcha/api2/anchor?" + urlencode(params),
        )
        stages.append("anchor")
        parser = AnchorParser()
        parser.feed(anchor)
        if not parser.token:
            raise ProbeError("AnchorTokenNotFound")
        fields = form_fields()
        fields.update({"v": version, "c": parser.token, "k": site_key, "co": origin})
        if include_action:
            fields["sa"] = ACTION
        reload_text = fetch(
            session,
            deadline,
            "POST",
            CAPTCHA_ORIGIN + "/recaptcha/api2/reload?" + urlencode({"k": site_key}),
            data=urlencode(fields).encode(),
        )
        stages.append("reload")
        cleaned = reload_text.removeprefix(")]}'").lstrip()
        try:
            payload = json.loads(cleaned)
        except (json.JSONDecodeError, RecursionError):
            raise ProbeError("ReloadInvalidJson") from None
        if not isinstance(payload, list) or len(payload) < 2 or payload[0] != "rresp":
            raise ProbeError("ReloadUnexpectedShape")
        token = payload[1]
        if not isinstance(token, str) or not token:
            result.update({"token_received": False, "server_accepted": False})
        else:
            try:
                verification = json.loads(
                    fetch(
                        session,
                        deadline,
                        "GET",
                        VERIFY_URL + "?" + urlencode({"action": ACTION, "token": token}),
                    )
                )
                if not isinstance(verification, dict):
                    raise ValueError
            except (ValueError, RecursionError):
                raise ProbeError("VerificationInvalidJson") from None
            stages.append("verification")
            result.update(
                {
                    "token_received": True,
                    "server_accepted": verification.get("success") is True,
                    "verification": verification,
                }
            )
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proxy", help="Proxy URL. No direct fallback on errors.")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--include-action", action="store_true")
    args = parser.parse_args()
    try:
        result = probe(args.proxy, args.timeout, include_action=args.include_action)
    except ProbeError as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result["server_accepted"] else 1


if __name__ == "__main__":
    sys.exit(main())
