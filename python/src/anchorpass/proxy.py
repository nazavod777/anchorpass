from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

from .errors import AnchorpassError


def normalize_proxy(value: str | None) -> str:
    if value is None:
        return ""
    try:
        if not isinstance(value, str) or not value or any(c.isspace() for c in value):
            raise ValueError
        if any(ord(c) < 32 for c in value) or re.search(r"%(?![a-fA-F0-9]{2})", value):
            raise ValueError
        value.encode("utf-8")
        p = urlsplit(value)
        if (
            p.scheme not in {"http", "https", "socks5", "socks5h"}
            or not p.hostname
            or p.port is None
            or p.port <= 0
            or p.path not in {"", "/"}
            or "?" in value
            or "#" in value
        ):
            raise ValueError
        for credential in (p.username, p.password):
            if credential is not None:
                decoded = unquote(credential, errors="strict")
                if not decoded.isascii() or any(ord(c) < 32 for c in decoded):
                    raise ValueError
    except (ValueError, UnicodeError):
        raise AnchorpassError("InvalidProxy", "configuration") from None
    return value
