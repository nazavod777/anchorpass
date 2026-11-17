"""Sign a protocol-data envelope using an operator-supplied Ed25519 PEM key."""

from __future__ import annotations

import argparse
import base64
import json
import time
from pathlib import Path

from anchorpass.bundle import verify_bundle
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", required=True, type=Path)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--fields", required=True, type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--expires-at", required=True, type=int, help="UTC Unix seconds")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    key = serialization.load_pem_private_key(args.key.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        parser.error("The signing key must be Ed25519")
    with args.fields.open("rb") as source:
        fields_raw = source.read(65537)
    if len(fields_raw) > 65536:
        parser.error("Fields file exceeds the input limit")
    payload = json.dumps(
        {
            "schema_version": 1,
            "protocol_family": "recaptcha-v3/1",
            "revision": args.revision,
            "issued_at": int(time.time()),
            "expires_at": args.expires_at,
            "fields": json.loads(fields_raw),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    message = b"anchorpass.bundle.v1\0" + args.key_id.encode("ascii") + b"\0" + payload
    envelope = (
        json.dumps(
            {
                "key_id": args.key_id,
                "payload": base64.b64encode(payload).decode(),
                "signature": base64.b64encode(key.sign(message)).decode(),
            },
            indent=2,
        ).encode()
        + b"\n"
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    verified = verify_bundle(envelope, {args.key_id: base64.b64encode(public).decode()})
    verified.require_valid()
    with args.output.open("xb") as output:
        output.write(envelope)
    print(
        json.dumps(
            {"output": str(args.output), "revision": verified.revision, "bytes": len(envelope)}
        )
    )


if __name__ == "__main__":
    main()
