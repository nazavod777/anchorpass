from __future__ import annotations

from email.message import Message
from http.client import HTTPResponse
from http.cookiejar import CookieJar, DefaultCookiePolicy
from io import BytesIO
from typing import cast
from urllib.request import Request
from urllib.response import addinfourl

from .errors import AnchorpassError

MAX_COOKIES = 64
MAX_COOKIE_BYTES = 16384


class OperationCookies:
    def __init__(self) -> None:
        self._jar = CookieJar(
            policy=DefaultCookiePolicy(strict_ns_domain=DefaultCookiePolicy.DomainStrictNonDomain)
        )
        self._received_count = 0
        self._received_bytes = 0

    def receive(self, values: list[str], url: str, stage: str) -> None:
        self._received_count += len(values)
        self._received_bytes += sum(len(value.encode("utf-8")) for value in values)
        if self._received_count > MAX_COOKIES or self._received_bytes > MAX_COOKIE_BYTES:
            raise AnchorpassError(
                "ResourceLimit", stage, "Response cookies exceed operation limits"
            )
        headers = Message()
        for value in values:
            headers.add_header("Set-Cookie", value)
        with addinfourl(BytesIO(), headers, url) as response:
            # CookieJar consumes info(); addinfourl provides the same response protocol.
            self._jar.extract_cookies(cast(HTTPResponse, response), Request(url))
        self._jar.clear_expired_cookies()

    def header(self, url: str, stage: str) -> str | None:
        request = Request(url)
        self._jar.add_cookie_header(request)
        value = request.get_header("Cookie")
        if value is not None and len(value.encode("utf-8")) > MAX_COOKIE_BYTES:
            raise AnchorpassError("ResourceLimit", stage, "Cookie header exceeds its byte limit")
        return value
