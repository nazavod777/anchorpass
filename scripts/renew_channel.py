#!/usr/bin/env python3
"""Inspect or locally renew the approved production channel; never publish it."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path

import release
from anchorpass.bundle import (
    FIELD_NAMES,
    MAX_BUNDLE_BYTES,
    PREFIX,
    Bundle,
    revision_key,
    strict_json,
    verify_bundle,
)
from anchorpass.errors import AnchorpassError
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

ROOT = Path(__file__).resolve().parents[1]
KEY_ID = "production-1"
SIGNING_KEY_ENV = "ANCHORPASS_SIGNING_KEY_PEM"
LIFETIME_SECONDS = int(timedelta(days=30).total_seconds())
RENEWAL_WINDOW_SECONDS = int(timedelta(days=7).total_seconds())
MAX_SIGNER_BYTES = 4096
MAX_REVISIONS = 10000


def read_keys(path: Path) -> dict[str, str]:
    keys = strict_json(release.read_bounded(path, MAX_BUNDLE_BYTES))
    if (
        not isinstance(keys, dict)
        or KEY_ID not in keys
        or "development-1" in keys
        or any(not isinstance(value, str) for value in keys.values())
    ):
        raise release.ReleaseError("Supply approved production public keys")
    Ed25519PublicKey.from_public_bytes(base64.b64decode(keys[KEY_ID], validate=True))
    return keys


def read_fields(path: Path) -> dict[str, str]:
    fields = strict_json(release.read_bounded(path, MAX_BUNDLE_BYTES))
    if (
        not isinstance(fields, dict)
        or set(fields) != FIELD_NAMES
        or any(
            not isinstance(value, str) or not value or len(value.encode()) > 16384
            for value in fields.values()
        )
    ):
        raise release.ReleaseError("Invalid approved protocol fields")
    return fields


def read_current(
    destination: Path, keys: dict[str, str], *, reserve_revision: bool = False
) -> Bundle:
    release.safe_directory(destination)
    objects = destination / "revisions"
    release.safe_directory(objects)
    raw = release.read_bounded(destination / "latest.json", MAX_BUNDLE_BYTES)
    # Authentication is required even after expiry; client expiry policy is unchanged.
    current = verify_bundle(raw, keys)
    if current.expires_at - current.issued_at > LIFETIME_SECONDS:
        raise release.ReleaseError("Current channel exceeds the approved maximum lifetime")
    if release.read_bounded(objects / f"{current.revision}.json", MAX_BUNDLE_BYTES) != raw:
        raise release.ReleaseError("Latest bytes differ from their immutable revision")
    for count, path in enumerate(objects.iterdir(), start=1):
        if count > MAX_REVISIONS or (reserve_revision and count == MAX_REVISIONS):
            raise release.ReleaseError("Channel revision directory exceeds the entry limit")
        if path.is_symlink() or not path.is_file() or path.suffix != ".json":
            raise release.ReleaseError("Unexpected immutable revision entry")
        if revision_key(path.stem) > revision_key(current.revision):
            raise release.ReleaseError("Latest would roll back an existing channel revision")
    return current


def status(current: Bundle, now: int) -> dict[str, object]:
    remaining = current.expires_at - now
    return {
        "revision": current.revision,
        "expires_at": current.expires_at,
        "remaining_seconds": remaining,
        "expired": remaining <= 0,
        "needs_renewal": remaining <= RENEWAL_WINDOW_SECONDS,
        "renewed": False,
        "lifetime_seconds": LIFETIME_SECONDS,
        "renewal_window_seconds": RENEWAL_WINDOW_SECONDS,
    }


def load_signer(keys: dict[str, str]) -> Ed25519PrivateKey:
    # Never persist the PEM, include it in an exception, or pass it to a child process.
    pem = os.environ.pop(SIGNING_KEY_ENV, "")
    try:
        encoded = pem.encode("ascii")
        if not encoded or len(encoded) > MAX_SIGNER_BYTES:
            raise ValueError
        key = serialization.load_pem_private_key(encoded, password=None)
    except (ValueError, TypeError, UnicodeError, UnsupportedAlgorithm):
        raise release.ReleaseError("A valid Ed25519 signing secret is required") from None
    if not isinstance(key, Ed25519PrivateKey):
        raise release.ReleaseError("The signing secret must be Ed25519")
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    if public != base64.b64decode(keys[KEY_ID], validate=True):
        raise release.ReleaseError("Signing secret does not match the approved public key")
    return key


def replace_latest(destination: Path, raw: bytes) -> None:
    with tempfile.NamedTemporaryFile(dir=destination, prefix=".latest-", delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
            output.close()
            os.replace(temporary, destination / "latest.json")
        finally:
            temporary.unlink(missing_ok=True)


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def inspect_channel(destination: Path, keys_path: Path) -> dict[str, object]:
    return status(read_current(destination, read_keys(keys_path)), int(time.time()))


def renew_channel(destination: Path, keys_path: Path, fields_path: Path) -> dict[str, object]:
    release.safe_directory(destination)
    with release.lock(destination / ".channel.lock"):
        keys = read_keys(keys_path)
        current = read_current(destination, keys, reserve_revision=True)
        now = int(time.time())
        result = status(current, now)
        if not result["needs_renewal"]:
            return result
        revision = str(int(current.revision) + 1)
        revision_key(revision)
        payload = json.dumps(
            {
                "schema_version": 1,
                "protocol_family": "recaptcha-v3/1",
                "revision": revision,
                "issued_at": now,
                "expires_at": now + LIFETIME_SECONDS,
                "fields": read_fields(fields_path),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        signer = load_signer(keys)
        raw = release.canonical(
            {
                "key_id": KEY_ID,
                "payload": base64.b64encode(payload).decode("ascii"),
                "signature": base64.b64encode(
                    signer.sign(PREFIX + KEY_ID.encode("ascii") + b"\0" + payload)
                ).decode("ascii"),
            }
        )
        candidate = verify_bundle(raw, keys)
        candidate.require_valid(RENEWAL_WINDOW_SECONDS)
        release.write_once(destination / "revisions" / f"{revision}.json", raw)
        sync_directory(destination / "revisions")
        replace_latest(destination, raw)
        sync_directory(destination)
        return {**status(candidate, now), "renewed": True, "previous_revision": current.revision}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("check", "renew"))
    parser.add_argument("--keys", type=Path, default=ROOT / "data/bundled/keys.json")
    parser.add_argument("--fields", type=Path, default=ROOT / "data/upstream-fields.json")
    parser.add_argument("--destination", type=Path, default=ROOT / "channels/recaptcha-v3-1")
    args = parser.parse_args(argv)
    try:
        result = (
            inspect_channel(args.destination, args.keys)
            if args.operation == "check"
            else renew_channel(args.destination, args.keys, args.fields)
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except release.ReleaseError as error:
        print(f"Channel operation failed: {error}", file=sys.stderr)
        return 1
    except AnchorpassError as error:
        print(f"Channel operation failed: {error.code}", file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError, RecursionError, UnsupportedAlgorithm):
        # Do not render exception text from key loading, JSON, or the environment.
        print(
            "Channel operation failed; check approved inputs, signer and revision history",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
