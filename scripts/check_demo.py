"""Run one explicit public-API acceptance check against Google's public demo."""

from __future__ import annotations

import argparse
import base64
import json
import math
import re
import time
from urllib.parse import urlencode

from anchorpass import AnchorpassError, Client
from curl_cffi import CurlOpt
from curl_cffi.curl import CURL_WRITEFUNC_ERROR
from curl_cffi.requests import Session
from curl_cffi.requests.exceptions import RequestException

DEMO = "https://recaptcha-demo.appspot.com"
ACTION = "examples/v3scores"
MAX_BODY_BYTES = 65536


class DemoError(Exception):
    pass


def fetch(session: Session, url: str, deadline: float) -> str:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DemoError("DeadlineExceeded")
    body = bytearray()

    def receive(chunk: bytes) -> int:
        if len(body) + len(chunk) > MAX_BODY_BYTES:
            return CURL_WRITEFUNC_ERROR
        body.extend(chunk)
        return len(chunk)

    try:
        response = session.get(
            url, timeout=remaining, allow_redirects=False, content_callback=receive
        )
        if response.status_code != 200:
            raise DemoError("HttpStatusError")
        return body.decode("utf-8")
    except (RequestException, UnicodeError):
        raise DemoError("DemoTransportError") from None


def check(proxy: str | None, timeout: float) -> dict[str, object]:
    if not math.isfinite(timeout) or timeout <= 0:
        raise DemoError("InvalidTimeout")
    started = time.monotonic()
    deadline = started + timeout
    # Constructing the library client validates the explicit proxy before any I/O.
    with (
        Client(proxy=proxy, timeout=timeout) as client,
        Session(
            trust_env=False, curl_options={CurlOpt.PROXY: proxy or "", CurlOpt.NOPROXY: ""}
        ) as session,
    ):
        page = fetch(session, DEMO + "/recaptcha-v3-request-scores.php", deadline)
        match = re.search(r"api\.js\?render=([A-Za-z0-9_-]+)", page)
        if match is None:
            raise DemoError("DemoSiteKeyNotFound")
        origin = base64.b64encode((DEMO + ":443").encode()).decode().replace("=", ".")
        anchor = "https://www.google.com/recaptcha/api2/anchor?" + urlencode(
            {
                "ar": "1",
                "k": match.group(1),
                "co": origin,
                "hl": "en",
                "size": "invisible",
            }
        )
        client.timeout = deadline - time.monotonic()
        if client.timeout <= 0:
            raise DemoError("DeadlineExceeded")
        result = client.solve(anchor, action=ACTION)
        try:
            verified = json.loads(
                fetch(
                    session,
                    DEMO
                    + "/recaptcha-v3-verify.php?"
                    + urlencode(
                        {
                            "action": ACTION,
                            "token": result.token,
                        }
                    ),
                    deadline,
                )
            )
            if not isinstance(verified, dict):
                raise ValueError
        except (ValueError, RecursionError):
            raise DemoError("VerificationInvalidJson") from None
        score = verified.get("score")
        errors = verified.get("error-codes", [])
        if errors is None:
            errors = []
        if (
            not isinstance(errors, list)
            or len(errors) > 32
            or any(not isinstance(error, str) or len(error) > 1024 for error in errors)
        ):
            raise DemoError("VerificationInvalidShape")
        accepted = (
            verified.get("success") is True
            and verified.get("action") == ACTION
            and verified.get("hostname") == "recaptcha-demo.appspot.com"
            and type(score) in (int, float)
            and 0 <= score <= 1
        )
        return {
            "server_accepted": accepted,
            "status": "degraded" if accepted and errors else "accepted" if accepted else "rejected",
            "proxy_configured": proxy is not None,
            "bundle_revision": result.bundle_revision,
            "protocol_version": result.protocol_version,
            "score": score if type(score) in (int, float) and 0 <= score <= 1 else None,
            "action": verified.get("action"),
            "hostname": verified.get("hostname"),
            "error_codes": errors,
            "elapsed_seconds": time.monotonic() - started,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proxy")
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    try:
        result = check(args.proxy, args.timeout)
    except AnchorpassError as error:
        print(json.dumps({"error": error.code, "stage": error.stage}))
        return 1
    except DemoError as error:
        print(json.dumps({"error": str(error)}))
        return 1
    print(json.dumps(result))
    return 0 if result["status"] == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
