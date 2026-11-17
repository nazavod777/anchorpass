"""Bounded adapters for public demonstration verifiers, never production forms."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from check_demo import DemoError
from curl_cffi.curl import CURL_WRITEFUNC_ERROR
from curl_cffi.requests import Session
from curl_cffi.requests.exceptions import RequestException

ROOT = Path(__file__).resolve().parents[1]
SITES = json.loads((ROOT / "tests/live/sites.json").read_text())
MAX_PAGE_BYTES = 1048576
MAX_VERIFY_BYTES = 65536
INVALID_TOKEN = "anchorpass-invalid-control"


class Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: list[str] = []
        self.pre: list[str] = []
        self.in_pre = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            value = dict(attrs).get("src")
            if value:
                self.scripts.append(value)
        if tag == "pre":
            self.in_pre = True
            self.pre.append("")

    def handle_endtag(self, tag: str) -> None:
        if tag == "pre":
            self.in_pre = False

    def handle_data(self, data: str) -> None:
        if self.in_pre:
            self.pre[-1] += data


def request(
    session: Session, url: str, deadline: float, *, limit: int = MAX_VERIFY_BYTES, **kwargs
) -> str:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DemoError("DeadlineExceeded")
    body = bytearray()
    exceeded = False

    def receive(chunk: bytes) -> int:
        nonlocal exceeded
        if len(body) + len(chunk) > limit:
            exceeded = True
            return CURL_WRITEFUNC_ERROR
        body.extend(chunk)
        return len(chunk)

    try:
        response = session.request(
            kwargs.pop("method", "GET"),
            url,
            timeout=remaining,
            allow_redirects=False,
            content_callback=receive,
            **kwargs,
        )
        if response.status_code != 200:
            raise DemoError("HttpStatusError")
        return body.decode("utf-8")
    except (RequestException, UnicodeError):
        raise DemoError("ResponseLimit" if exceeded else "DemoTransportError") from None


def discover(session: Session, site: dict, deadline: float) -> dict:
    """Check the active page and compiled widget, not the demo's stale tutorial key."""
    sources = {}

    def fetch_source(url: str) -> str:
        text = request(session, url, deadline, limit=MAX_PAGE_BYTES)
        sources[url] = hashlib.sha256(text.encode()).hexdigest()
        return text

    html = fetch_source(site["page"])
    page = Page()
    page.feed(html)
    if site["adapter"] == "two-captcha":
        entries = [s for s in page.scripts if re.fullmatch(r"/dist/web/assets/index-[\w-]+\.js", s)]
        if len(entries) != 1:
            raise DemoError("DemoEntryScriptNotFound")
        entry = fetch_source("https://2captcha.com" + entries[0])
        # This public module defines the demo endpoints immediately before its lazy component.
        endpoint = entry.find('url:"/api/v1/captcha-demo/"')
        match = re.search(r'import\("\./(index-[\w-]+\.js)"\)', entry[endpoint : endpoint + 3000])
        if endpoint < 0 or match is None:
            raise DemoError("DemoWidgetNotFound")
        widget = fetch_source("https://2captcha.com/dist/web/assets/" + match.group(1))
        key = site["site_key"]
        if key not in widget or site["action"] not in widget:
            raise DemoError("DemoConfigurationChanged")
    else:
        keys = [
            match.group(1)
            for src in page.scripts
            if (
                match := re.fullmatch(
                    r"https://www\.google\.com/recaptcha/api\.js\?render=([\w-]+)", src
                )
            )
        ]
        if len(keys) != 1 or not re.search(
            r"action\s*:\s*['\"]" + re.escape(site["action"]) + r"['\"]", html
        ):
            raise DemoError("DemoConfigurationChanged")
        key = keys[0]
    origin = urlsplit(site["page"])
    co = (
        base64.b64encode(f"{origin.scheme}://{origin.netloc}:443".encode())
        .decode()
        .replace("=", ".")
    )
    anchor = (
        "https://www.google.com/recaptcha/"
        + site["variant"]
        + "/anchor?"
        + urlencode({"ar": "1", "k": key, "co": co, "hl": "en", "size": "invisible"})
    )
    return {"anchor": anchor, "site_key": key, "source_sha256": sources}


def object_json(text: str) -> dict:
    def reject_constant(_: str) -> None:
        raise ValueError

    def unique(pairs: list[tuple[str, object]]) -> dict:
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError
            value[key] = item
        return value

    try:
        value = json.loads(text, parse_constant=reject_constant, object_pairs_hook=unique)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, RecursionError):
        raise DemoError("VerificationInvalidJson") from None


def verify(session: Session, site: dict, key: str, token: str, deadline: float) -> dict:
    if site["adapter"] == "google":
        text = request(
            session,
            site["verify"] + "?" + urlencode({"action": site["action"], "token": token}),
            deadline,
        )
    elif site["adapter"] == "two-captcha":
        field = "token" if site["variant"] == "enterprise" else "answer"
        text = request(
            session, site["verify"], deadline, method="POST", json={"siteKey": key, field: token}
        )
    else:
        text = request(session, site["verify"], deadline, method="POST", data={"token": token})
        page = Page()
        page.feed(text)
        candidates = [block for block in page.pre if block.lstrip().startswith("{")]
        if len(candidates) != 1:
            raise DemoError("VerificationJsonNotFound")
        text = candidates[0]
    return object_json(text)


def assess(site: dict, value: dict) -> dict:
    """Keep acceptance, action evidence and quota diagnostics separate."""
    enterprise = site["variant"] == "enterprise"
    properties = value.get("tokenProperties", {}) if enterprise else value
    risk = value.get("riskAnalysis", {}) if enterprise else value
    if not isinstance(properties, dict) or not isinstance(risk, dict):
        raise DemoError("VerificationInvalidShape")
    hostname = properties.get("hostname")
    action = properties.get("action")
    score = risk.get("score")
    errors = value.get("error-codes", [])
    if errors is None:
        errors = []
    if (
        not isinstance(errors, list)
        or len(errors) > 32
        or any(not isinstance(e, str) or len(e) > 1024 for e in errors)
    ):
        raise DemoError("VerificationInvalidShape")
    score_valid = type(score) in (int, float) and 0 <= score <= 1 and math.isfinite(score)
    action_reported = action is not None
    action_valid = action == site["action"]
    accepted = (
        value.get("success") is True
        and (not enterprise or properties.get("valid") is True)
        and hostname == urlsplit(site["page"]).hostname
        and score_valid
        and (action_valid or (enterprise and not action_reported))
    )
    status = "rejected"
    if accepted:
        status = (
            "degraded" if errors else "accepted" if action_valid else "accepted_action_unreported"
        )
    return {
        "status": status,
        "server_accepted": accepted,
        "action_confirmed": action_valid,
        "action": action if isinstance(action, str) and len(action) <= 256 else None,
        "hostname": hostname if isinstance(hostname, str) and len(hostname) <= 253 else None,
        "score": score if score_valid else None,
        "error_codes": errors,
    }
