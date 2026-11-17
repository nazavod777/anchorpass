#!/usr/bin/env python3
"""Stage an isolated, freshly signed test-data checkout for production renewal gates."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import release
from anchorpass.bundle import MAX_BUNDLE_BYTES, PREFIX, strict_json, verify_bundle
from anchorpass.errors import AnchorpassError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from renew_channel import LIFETIME_SECONDS, RENEWAL_WINDOW_SECONDS, SIGNING_KEY_ENV, read_fields
from renew_channel import read_keys as production_keys
from sync_package_data import DESTINATIONS

ROOT = Path(__file__).resolve().parents[1]
VALIDATION_KEY_ID = "channel-validation-1"
GIT_TIMEOUT_SECONDS = 60
DATA_DIRECTORIES = ("data/bundled", *DESTINATIONS)
DATA_PATHS = frozenset(
    f"{directory}/{name}" for directory in DATA_DIRECTORIES for name in ("bundle.json", "keys.json")
)


def git(root: Path, *arguments: str) -> bytes:
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.autocrlf=false", *arguments],
        cwd=root,
        capture_output=True,
        timeout=GIT_TIMEOUT_SECONDS,
        check=False,
    )
    if result.returncode:
        raise release.ReleaseError("Validation checkout Git operation failed")
    return result.stdout


def require_commit(root: Path, commit: str) -> None:
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit):
        raise release.ReleaseError("Supply the complete checked commit ID")
    if git(root, "rev-parse", "HEAD").decode().strip() != commit:
        raise release.ReleaseError("Validation source differs from the checked commit")


def package_data(root: Path) -> tuple[bytes, bytes]:
    values = []
    for name in ("bundle.json", "keys.json"):
        canonical = release.read_bounded(root / "data/bundled" / name, MAX_BUNDLE_BYTES)
        for directory in DATA_DIRECTORIES:
            path = root / directory / name
            for parent in path.relative_to(root).parents:
                release.safe_directory(root / parent)
            if release.read_bounded(path, MAX_BUNDLE_BYTES) != canonical:
                raise release.ReleaseError("Standalone package data differs from shared source")
        values.append(canonical)
    return values[0], values[1]


def verify_validation(destination: Path, commit: str) -> dict[str, object]:
    require_commit(destination, commit)
    changed = set(git(destination, "diff", "--name-only", "-z", "HEAD", "--").decode().split("\0"))
    changed.discard("")
    if changed != DATA_PATHS or git(
        destination, "ls-files", "--others", "--exclude-standard", "-z"
    ):
        raise release.ReleaseError("Validation changed files outside its isolated test data")
    raw, keys_raw = package_data(destination)
    keys = strict_json(keys_raw)
    if not isinstance(keys, dict) or set(keys) != {VALIDATION_KEY_ID}:
        raise release.ReleaseError("Validation must trust only its ephemeral test signer")
    bundle = verify_bundle(raw, keys)
    bundle.require_valid(RENEWAL_WINDOW_SECONDS)
    if bundle.revision != "1" or bundle.expires_at - bundle.issued_at != LIFETIME_SECONDS:
        raise release.ReleaseError("Validation requires revision one with a bounded fresh lifetime")
    if dict(bundle.fields) != read_fields(destination / "data/upstream-fields.json"):
        raise release.ReleaseError("Validation fields differ from approved upstream fields")
    return {
        "source_commit": commit,
        "fields_sha256": hashlib.sha256(release.canonical(dict(bundle.fields))).hexdigest(),
        "validation_revision": bundle.revision,
        "validation_expires_at": bundle.expires_at,
        "isolated_data_files": sorted(changed),
    }


def stage_validation(source: Path, destination: Path, commit: str) -> dict[str, object]:
    if SIGNING_KEY_ENV in os.environ:
        raise release.ReleaseError("Production signer must not be available during validation")
    source = source.resolve(strict=True)
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise release.ReleaseError("Validation destination must not exist")
    if source == destination or source in destination.resolve().parents:
        raise release.ReleaseError("Validation destination must be outside the source checkout")
    require_commit(source, commit)
    git(source, "diff", "--exit-code", "HEAD", "--")
    if git(source, "ls-files", "--others", "--exclude-standard", "-z"):
        raise release.ReleaseError("Validation source contains untracked files")
    git(
        source,
        "clone",
        "--local",
        "--no-hardlinks",
        "--no-checkout",
        "--",
        str(source),
        str(destination),
    )
    git(destination, "checkout", "--detach", commit)
    require_commit(destination, commit)
    git(destination, "diff", "--exit-code", "HEAD", "--")

    # Authenticate production data, even after expiry, before substituting test-only trust.
    original, _ = package_data(destination)
    verify_bundle(original, production_keys(destination / "data/bundled/keys.json"))
    fields = read_fields(destination / "data/upstream-fields.json")

    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    keys = release.canonical({VALIDATION_KEY_ID: base64.b64encode(public).decode("ascii")})
    now = int(time.time())
    payload = json.dumps(
        {
            "schema_version": 1,
            "protocol_family": "recaptcha-v3/1",
            "revision": "1",
            "issued_at": now,
            "expires_at": now + LIFETIME_SECONDS,
            "fields": fields,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    envelope = release.canonical(
        {
            "key_id": VALIDATION_KEY_ID,
            "payload": base64.b64encode(payload).decode("ascii"),
            "signature": base64.b64encode(
                key.sign(PREFIX + VALIDATION_KEY_ID.encode("ascii") + b"\0" + payload)
            ).decode("ascii"),
        }
    )
    # The private key stays in this process; only the disposable checkout receives public data.
    del key
    (destination / "data/bundled/bundle.json").write_bytes(envelope)
    (destination / "data/bundled/keys.json").write_bytes(keys)
    subprocess.run(
        [sys.executable, str(destination / "scripts/sync_package_data.py")],
        cwd=destination,
        timeout=GIT_TIMEOUT_SECONDS,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    return verify_validation(destination, commit)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("stage", "verify"))
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args(argv)
    try:
        if SIGNING_KEY_ENV in os.environ:
            raise release.ReleaseError("Production signer must not be available during validation")
        result = (
            stage_validation(args.source, args.destination, args.commit)
            if args.operation == "stage"
            else verify_validation(args.destination, args.commit)
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except release.ReleaseError as error:
        print(f"Channel validation failed: {error}", file=sys.stderr)
    except AnchorpassError as error:
        print(f"Channel validation failed: {error.code}", file=sys.stderr)
    except (OSError, ValueError, TypeError, RecursionError, subprocess.SubprocessError):
        print(
            "Channel validation failed; check the source commit and isolated checkout",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
