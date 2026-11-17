"""A synthetic signer verifies channel staging without a production private key."""

from __future__ import annotations

import base64
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import release  # noqa: E402
import release_channel as channel  # noqa: E402


class ChannelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory(prefix="anchorpass-channel-test-")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.key = Ed25519PrivateKey.generate()
        self.keys = self.root / "keys.json"
        public = self.key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        self.keys.write_text(json.dumps({"release-test": base64.b64encode(public).decode()}))
        self.destination = self.root / "channel"

    def envelope(self, revision: str, lifetime: int = 600, bg: str = "test") -> Path:
        now = int(time.time())
        payload = json.dumps(
            {
                "schema_version": 1,
                "protocol_family": "recaptcha-v3/1",
                "revision": revision,
                "issued_at": now,
                "expires_at": now + lifetime,
                "fields": {
                    "reason": "q",
                    "hl": "en",
                    "size": "invisible",
                    "chr": "test",
                    "vh": "test",
                    "bg": bg,
                },
            },
            sort_keys=True,
        ).encode()
        raw = json.dumps(
            {
                "key_id": "release-test",
                "payload": base64.b64encode(payload).decode(),
                "signature": base64.b64encode(
                    self.key.sign(b"anchorpass.bundle.v1\0release-test\0" + payload)
                ).decode(),
            }
        ).encode()
        path = self.root / f"input-{revision}-{bg}.json"
        path.write_bytes(raw)
        return path

    def stage(self, path: Path) -> dict:
        return channel.stage(path, self.keys, self.destination, 60, 1200)

    def test_signed_revision_is_idempotent_and_new_revision_advances(self) -> None:
        first = self.envelope("1")
        self.stage(first)
        self.stage(first)
        self.assertEqual(len(release.events(self.destination)), 1)
        second = self.envelope("2")
        event = self.stage(second)
        self.assertEqual(event["revision"], "2")
        self.assertEqual((self.destination / "latest.json").read_bytes(), second.read_bytes())
        self.assertEqual((self.destination / "revisions/1.json").read_bytes(), first.read_bytes())

    def test_rollback_equivocation_and_excessive_lifetime_are_rejected(self) -> None:
        self.stage(self.envelope("2"))
        for path, reason in (
            (self.envelope("1"), "lower"),
            (self.envelope("2", bg="different"), "existing"),
            (self.envelope("3", lifetime=1800), "maximum lifetime"),
        ):
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(release.ReleaseError, reason):
                    self.stage(path)

    def test_development_trust_and_oversized_input_are_rejected(self) -> None:
        path = self.envelope("1")
        keys = json.loads(self.keys.read_bytes())
        keys["development-1"] = keys["release-test"]
        self.keys.write_text(json.dumps(keys))
        with self.assertRaisesRegex(release.ReleaseError, "production trust"):
            self.stage(path)
        path.write_bytes(b"x" * 65537)
        with self.assertRaisesRegex(release.ReleaseError, "bounded"):
            self.stage(path)

    def test_revision_directory_symlink_cannot_write_outside_channel(self) -> None:
        self.destination.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        (self.destination / "revisions").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(release.ReleaseError, "symlinks"):
            self.stage(self.envelope("1"))
        self.assertEqual(list(outside.iterdir()), [])

    def test_recovery_cannot_lower_a_retained_revision_ahead_of_latest(self) -> None:
        first = self.envelope("2")
        self.stage(first)
        retained = self.envelope("4")
        (self.destination / "revisions/4.json").write_bytes(retained.read_bytes())
        with self.assertRaisesRegex(release.ReleaseError, "retained"):
            self.stage(self.envelope("3"))
        self.assertEqual((self.destination / "latest.json").read_bytes(), first.read_bytes())
        self.assertFalse((self.destination / "revisions/3.json").exists())
        self.stage(retained)
        self.assertEqual((self.destination / "latest.json").read_bytes(), retained.read_bytes())

    def test_revision_enumeration_is_bounded_and_rejects_symlinks(self) -> None:
        self.stage(self.envelope("1"))
        with patch.object(channel, "MAX_REVISIONS", 0):
            with self.assertRaisesRegex(release.ReleaseError, "entry limit"):
                self.stage(self.envelope("2"))
        outside = self.envelope("3")
        (self.destination / "revisions/3.json").symlink_to(outside)
        with self.assertRaisesRegex(release.ReleaseError, "Unexpected"):
            self.stage(self.envelope("2"))


if __name__ == "__main__":
    unittest.main()
