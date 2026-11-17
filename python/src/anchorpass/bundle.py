from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import stat
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .errors import AnchorpassError

MAX_BUNDLE_BYTES = 65536
FIELD_NAMES = {"reason", "hl", "size", "chr", "vh", "bg"}
PREFIX = b"anchorpass.bundle.v1\0"


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate key")
        result[key] = value
    return result


def strict_json(raw: bytes) -> Any:
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)


def revision_key(revision: str) -> tuple[int, str]:
    if not isinstance(revision, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", revision):
        raise AnchorpassError("ConfigInvalid", "bundle", "Invalid revision")
    return len(revision), revision


@dataclass(frozen=True)
class Bundle:
    revision: str
    issued_at: int
    expires_at: int
    fields: Mapping[str, str]
    digest: str
    envelope: bytes

    def require_valid(self, duration: float = 0) -> None:
        if self.expires_at <= time.time() + duration:
            raise AnchorpassError("ConfigExpired", "bundle")


def verify_bundle(raw: bytes, keys: Mapping[str, str]) -> Bundle:
    try:
        if len(raw) > MAX_BUNDLE_BYTES:
            raise ValueError
        envelope = strict_json(raw)
        if not isinstance(envelope, dict) or set(envelope) != {"key_id", "payload", "signature"}:
            raise ValueError
        key_id = envelope["key_id"]
        if not isinstance(key_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key_id):
            raise ValueError
        payload = base64.b64decode(envelope["payload"], validate=True)
        public_key = base64.b64decode(keys[key_id], validate=True)
        signature = base64.b64decode(envelope["signature"], validate=True)
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature, PREFIX + key_id.encode("ascii") + b"\0" + payload
        )
        value = strict_json(payload)
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "protocol_family",
            "revision",
            "issued_at",
            "expires_at",
            "fields",
        }:
            raise ValueError
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise ValueError
        if value["protocol_family"] != "recaptcha-v3/1":
            raise ValueError
        revision_key(value["revision"])
        issued, expires = value["issued_at"], value["expires_at"]
        if (
            type(issued) is not int
            or type(expires) is not int
            or not 0 <= issued < expires <= 9007199254740991
            or issued > time.time() + 300
        ):
            raise ValueError
        fields = value["fields"]
        if (
            not isinstance(fields, dict)
            or set(fields) != FIELD_NAMES
            or any(
                not isinstance(v, str) or not v or len(v.encode()) > 16384 for v in fields.values()
            )
        ):
            raise ValueError
        return Bundle(
            value["revision"],
            issued,
            expires,
            MappingProxyType(fields),
            hashlib.sha256(payload).hexdigest(),
            raw,
        )
    except (
        KeyError,
        TypeError,
        ValueError,
        InvalidSignature,
        binascii.Error,
        UnicodeError,
        RecursionError,
    ):
        raise AnchorpassError("ConfigInvalid", "bundle") from None


def default_keys() -> dict[str, str]:
    result: dict[str, str] = json.loads(files("anchorpass.data").joinpath("keys.json").read_bytes())
    return result


def _read_cache(path: Path) -> bytes | None:
    try:
        fd = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise AnchorpassError("ConfigInvalid", "cache", "Cache is not a regular file")
        raw = handle.read(MAX_BUNDLE_BYTES + 1)
    if len(raw) > MAX_BUNDLE_BYTES:
        raise AnchorpassError("ConfigInvalid", "cache")
    return raw


@contextmanager
def _cache_lock(path: Path) -> Iterator[None]:
    # POSIX advisory locks are released by the kernel if the process crashes.
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = path.with_name(path.name + ".lock")
    fd = os.open(
        lock_path,
        os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        0o600,
    )
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise AnchorpassError("ConfigInvalid", "cache_lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AnchorpassError("ResourceLimit", "cache_lock") from None
        yield
    finally:
        os.close(fd)


class BundleStore:
    def __init__(
        self,
        *,
        keys: Mapping[str, str] | None = None,
        cache_path: str | Path | None = None,
        pin: str | None = None,
    ) -> None:
        self.keys = dict(default_keys() if keys is None else keys)
        self.cache_path = Path(cache_path) if cache_path is not None else None
        self.pin = pin
        if pin is not None:
            revision_key(pin)
        self.current: Bundle | None = None
        self._cache_error: AnchorpassError | None = None
        if keys is None:
            self.current = verify_bundle(
                files("anchorpass.data").joinpath("bundle.json").read_bytes(), self.keys
            )

    def _compare(self, candidate: Bundle, current: Bundle | None) -> None:
        if current is not None:
            if revision_key(candidate.revision) < revision_key(current.revision):
                raise AnchorpassError("ConfigRollback", "bundle")
            if candidate.revision == current.revision and candidate.digest != current.digest:
                raise AnchorpassError("ConfigInvalid", "bundle", "Revision has different content")

    def load_cache(self) -> None:
        if self.cache_path is None:
            return
        try:
            raw = _read_cache(self.cache_path)
        except OSError:
            raise AnchorpassError("ConfigInvalid", "cache") from None
        if raw is not None:
            candidate = verify_bundle(raw, self.keys)
            if self.current is not None and revision_key(candidate.revision) < revision_key(
                self.current.revision
            ):
                return
            self._compare(candidate, self.current)
            self.current = candidate

    def accept(self, raw: bytes) -> Bundle:
        candidate = verify_bundle(raw, self.keys)
        self._compare(candidate, self.current)
        # Authentication establishes the floor even if persistence fails.
        self.current = candidate
        if self.cache_path is not None:
            try:
                with _cache_lock(self.cache_path):
                    self.load_cache()
                    self._compare(candidate, self.current)
                    fd, name = tempfile.mkstemp(prefix=".anchorpass-", dir=self.cache_path.parent)
                    try:
                        with os.fdopen(fd, "wb") as handle:
                            handle.write(raw)
                            handle.flush()
                            os.fsync(handle.fileno())
                        os.replace(name, self.cache_path)
                        directory_fd = os.open(self.cache_path.parent, os.O_RDONLY | os.O_DIRECTORY)
                        try:
                            os.fsync(directory_fd)
                        finally:
                            os.close(directory_fd)
                    finally:
                        if os.path.exists(name):
                            os.unlink(name)
            except (OSError, AnchorpassError) as exc:
                self._cache_error = (
                    exc
                    if isinstance(exc, AnchorpassError)
                    else AnchorpassError("ConfigUnavailable", "cache")
                )
                raise self._cache_error from None
        self._cache_error = None
        candidate.require_valid()
        if self.pin is not None and candidate.revision != self.pin:
            raise AnchorpassError("ConfigUnavailable", "bundle", "Pinned revision unavailable")
        return candidate

    def snapshot(self, duration: float = 0) -> Bundle:
        if self._cache_error is not None:
            raise self._cache_error
        if self.current is None:
            raise AnchorpassError("ConfigUnavailable", "bundle")
        if self.pin is not None and self.current.revision != self.pin:
            raise AnchorpassError("ConfigUnavailable", "bundle", "Pinned revision unavailable")
        self.current.require_valid(duration)
        return self.current
