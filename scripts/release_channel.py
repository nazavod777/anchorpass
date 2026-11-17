#!/usr/bin/env python3
"""Verify and stage an operator-signed data channel locally, without deploying it."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import release
from anchorpass.bundle import revision_key, verify_bundle
from anchorpass.errors import AnchorpassError

MAX_BUNDLE_BYTES = 65536
MAX_REVISIONS = 10000


def stage(
    envelope: Path,
    keys_path: Path,
    destination: Path,
    minimum_remaining: int,
    maximum_lifetime: int,
) -> dict[str, object]:
    raw = release.read_bounded(envelope, MAX_BUNDLE_BYTES)
    keys = release.read_json(keys_path)
    if not isinstance(keys, dict) or not keys or "development-1" in keys:
        raise release.ReleaseError(
            "Supply production trust keys without the discarded development key"
        )
    bundle = verify_bundle(raw, keys)
    bundle.require_valid(minimum_remaining)
    payload = json.loads(base64.b64decode(json.loads(raw)["payload"], validate=True))
    if bundle.expires_at - payload["issued_at"] > maximum_lifetime:
        raise release.ReleaseError("Signed bundle exceeds the approved maximum lifetime")
    if minimum_remaining <= 0 or maximum_lifetime < minimum_remaining:
        raise release.ReleaseError("Use positive approved lifetime/renewal limits")
    destination.mkdir(parents=True, exist_ok=True)
    release.safe_directory(destination)
    with release.lock(destination / ".channel.lock"):
        latest = destination / "latest.json"
        if latest.exists():
            current_raw = release.read_bounded(latest, MAX_BUNDLE_BYTES)
            current = verify_bundle(current_raw, keys)
            if revision_key(bundle.revision) < revision_key(current.revision):
                raise release.ReleaseError("Cannot lower the channel revision")
            if bundle.revision == current.revision and raw != current_raw:
                raise release.ReleaseError("Cannot change bytes of an existing channel revision")
        objects = destination / "revisions"
        release.safe_directory(objects, create=True)
        for count, path in enumerate(objects.iterdir(), start=1):
            if count > MAX_REVISIONS:
                raise release.ReleaseError("Channel revision directory exceeds the entry limit")
            if path.is_symlink() or not path.is_file() or path.suffix != ".json":
                raise release.ReleaseError("Unexpected immutable revision entry")
            if revision_key(bundle.revision) < revision_key(path.stem):
                raise release.ReleaseError("Cannot lower the retained channel revision")
        revision = objects / f"{bundle.revision}.json"
        release.write_once(revision, raw)
        event = {
            "event": "channel-staged",
            "family": payload["protocol_family"],
            "revision": bundle.revision,
            "key_id": json.loads(raw)["key_id"],
            "sha256": hashlib.sha256(raw).hexdigest(),
            "expires_at": bundle.expires_at,
        }
        if not latest.exists() or release.read_bounded(latest, MAX_BUNDLE_BYTES) != raw:
            with tempfile.NamedTemporaryFile(
                dir=destination, prefix=".latest-", delete=False
            ) as output:
                temporary = Path(output.name)
                try:
                    output.write(raw)
                    output.flush()
                    os.fsync(output.fileno())
                    output.close()
                    os.replace(temporary, latest)
                finally:
                    temporary.unlink(missing_ok=True)
        if event not in release.events(destination):
            release.append_event(destination, event)
        return {**event, "remaining_seconds": int(bundle.expires_at - time.time())}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--envelope", required=True, type=Path)
    parser.add_argument(
        "--keys", required=True, type=Path, help="Approved public keys, never private keys"
    )
    parser.add_argument(
        "--destination", required=True, type=Path, help="A separate directory per family"
    )
    parser.add_argument("--minimum-remaining-seconds", required=True, type=int)
    parser.add_argument("--maximum-lifetime-seconds", required=True, type=int)
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                stage(
                    args.envelope,
                    args.keys,
                    args.destination,
                    args.minimum_remaining_seconds,
                    args.maximum_lifetime_seconds,
                ),
                sort_keys=True,
            )
        )
        return 0
    except (release.ReleaseError, AnchorpassError, OSError, ValueError, KeyError) as error:
        print(f"Channel staging failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
