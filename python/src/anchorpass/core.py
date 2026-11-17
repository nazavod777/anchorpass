from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlsplit

from .errors import AnchorpassError


@dataclass(frozen=True)
class ParsedAnchor:
    endpoint: str
    params: dict[str, str]


@dataclass(frozen=True)
class SolveResult:
    token: str = field(repr=False)
    bundle_revision: str
    protocol_version: str
    elapsed_seconds: float


def parse_anchor(value: str) -> ParsedAnchor:
    try:
        if (
            not isinstance(value, str)
            or len(value) > 65536
            or re.search(r"%(?![\da-fA-F]{2})", value)
        ):
            raise ValueError
        value.encode("utf-8")
        url = urlsplit(value)
        path = re.fullmatch(r"/recaptcha/(api2|enterprise)/anchor", url.path)
        if (
            url.scheme != "https"
            or url.hostname not in {"google.com", "www.google.com"}
            or url.port not in {None, 443}
            or url.username is not None
            or "#" in value
            or path is None
            or any(c.isspace() or ord(c) < 32 for c in value)
        ):
            raise ValueError
        pairs = parse_qsl(url.query, keep_blank_values=True, errors="strict", max_num_fields=64)
        params = dict(pairs)
        if len(params) != len(pairs) or not params.get("k") or not params.get("co"):
            raise ValueError
        return ParsedAnchor(path.group(1), params)
    except (ValueError, UnicodeError):
        raise AnchorpassError("InvalidInput", "anchor_url") from None


def resolve_version(script: str) -> str:
    versions: list[str] = re.findall(r"releases/([^/'\"\\)]+)/", script)
    if not versions:
        raise AnchorpassError("ProtocolError", "version", "No release version in script")
    return Counter(versions).most_common(1)[0][0]


def extract_action(anchor_url: str) -> str | None:
    """Extract the action supplied by an anchor URL without making a request."""
    return parse_anchor(anchor_url).params.get("sa")


def _normalize_hostname(value: str) -> str:
    if not isinstance(value, str) or not value or not value.isascii():
        raise ValueError
    address = value
    bracketed = value.startswith("[") and value.endswith("]")
    if bracketed:
        address = value[1:-1]
    if ":" in address:
        if "%" in address:
            raise ValueError
        parsed = ipaddress.IPv6Address(address)
        if parsed.ipv4_mapped is not None:
            mapped = int(parsed.ipv4_mapped)
            return f"[::ffff:{mapped >> 16:x}:{mapped & 0xFFFF:x}]"
        return f"[{parsed.compressed}]"
    if bracketed:
        raise ValueError
    try:
        return str(ipaddress.IPv4Address(address))
    except ipaddress.AddressValueError:
        pass
    if len(value) > 253 or not all(
        re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
        for label in value.removesuffix(".").split(".")
    ):
        raise ValueError
    return value.lower()


def resolve_origin(encoded_origin: str, hostname: str | None) -> str:
    if hostname is None:
        return encoded_origin
    try:
        selected = _normalize_hostname(hostname)
        if not re.fullmatch(r"[a-zA-Z0-9+/_-]+[=.]{0,2}", encoded_origin):
            raise ValueError
        if encoded_origin.endswith(("=", ".")) and (
            len(encoded_origin) % 4 != 0 or ("=" in encoded_origin and "." in encoded_origin)
        ):
            raise ValueError
        normalized = encoded_origin.translate(str.maketrans("-_.", "+/="))
        normalized += "=" * (-len(normalized) % 4)
        decoded_bytes = base64.b64decode(normalized, validate=True)
        if base64.b64encode(decoded_bytes).decode("ascii") != normalized:
            raise ValueError
        decoded = decoded_bytes.decode("utf-8")
        origin = urlsplit(decoded)
        if (
            origin.scheme not in {"http", "https"}
            or not origin.hostname
            or origin.username is not None
            or origin.password is not None
            or origin.netloc.endswith(":")
            or origin.path not in {"", "/"}
            or "?" in decoded
            or "#" in decoded
            or any(character.isspace() or ord(character) < 32 for character in decoded)
        ):
            raise ValueError
        _normalize_hostname(origin.hostname)
        port = origin.port
        if port is None:
            port = {"http": 80, "https": 443}[origin.scheme]
        if not 1 <= port <= 65535:
            raise ValueError
        serialized = f"{origin.scheme}://{selected}:{port}"
        return base64.b64encode(serialized.encode("ascii")).decode("ascii").replace("=", ".")
    except (ValueError, UnicodeError, binascii.Error):
        raise AnchorpassError("InvalidInput", "overrides") from None


def validate_overrides(
    anchor_url: str,
    action: str | None,
    bg: str | None,
    user_agent: str | None = None,
    hostname: str | None = None,
) -> None:
    parsed = parse_anchor(anchor_url)
    resolve_origin(parsed.params["co"], hostname)
    selected_action = parsed.params.get("sa") if action is None else action
    try:
        if selected_action is not None and (
            not isinstance(selected_action, str)
            or not selected_action
            or len(selected_action.encode("utf-8")) > 256
        ):
            raise ValueError
        if bg is not None and (
            not isinstance(bg, str) or not bg or len(bg.encode("utf-8")) > 16384
        ):
            raise ValueError
        if user_agent is not None and (
            not isinstance(user_agent, str)
            or not user_agent.strip()
            or len(user_agent) > 1024
            or any(not 0x20 <= ord(character) <= 0x7E for character in user_agent)
        ):
            raise ValueError
    except (ValueError, UnicodeError):
        raise AnchorpassError("InvalidInput", "overrides") from None


class _TokenParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.token: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "input" and values.get("id") == "recaptcha-token":
            self.token = values.get("value")


def anchor_token(html: str) -> str:
    parser = _TokenParser()
    parser.feed(html)
    if not parser.token:
        raise AnchorpassError("ProtocolError", "anchor", "Missing anchor token")
    return parser.token


def response_token(text: str) -> str:
    try:
        payload = json.loads(text.removeprefix(")]}'").lstrip())
        if not isinstance(payload, list) or len(payload) < 2 or payload[0] != "rresp":
            raise ValueError
        token = payload[1]
        if not isinstance(token, str) or not token:
            raise ValueError
        return token
    except (ValueError, TypeError, RecursionError):
        raise AnchorpassError("ProtocolError", "reload", "Missing response token") from None
