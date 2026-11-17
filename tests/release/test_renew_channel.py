"""Production renewal policy exercised only with synthetic signing keys."""

from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from anchorpass.bundle import PREFIX, BundleStore, verify_bundle
from anchorpass.errors import AnchorpassError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import release  # noqa: E402
import renew_channel as renewal  # noqa: E402


def pem(key: Ed25519PrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")


class RenewalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory(prefix="anchorpass-channel-renew-test-")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.now = int(time.time())
        clock = patch("renew_channel.time.time", return_value=self.now)
        clock.start()
        self.addCleanup(clock.stop)
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop(renewal.SIGNING_KEY_ENV, None)
        self.key = Ed25519PrivateKey.generate()
        public = self.key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        self.keys = {renewal.KEY_ID: base64.b64encode(public).decode("ascii")}
        self.keys_path = self.root / "keys.json"
        self.keys_path.write_text(json.dumps(self.keys))
        self.fields = {
            "reason": "q",
            "hl": "en",
            "size": "invisible",
            "chr": "test",
            "vh": "test",
            "bg": "approved-test",
        }
        self.fields_path = self.root / "fields.json"
        self.fields_path.write_text(json.dumps(self.fields))
        self.destination = self.root / "channel"
        (self.destination / "revisions").mkdir(parents=True)
        self.original = self.seed("2", renewal.RENEWAL_WINDOW_SECONDS)

    def envelope(self, revision: str, remaining: int, **overrides: object) -> bytes:
        expires = self.now + remaining
        payload = json.dumps(
            {
                "schema_version": 1,
                "protocol_family": "recaptcha-v3/1",
                "revision": revision,
                "issued_at": expires - renewal.LIFETIME_SECONDS,
                "expires_at": expires,
                "fields": self.fields,
                **overrides,
            },
            sort_keys=True,
        ).encode()
        return release.canonical(
            {
                "key_id": renewal.KEY_ID,
                "payload": base64.b64encode(payload).decode(),
                "signature": base64.b64encode(
                    self.key.sign(PREFIX + renewal.KEY_ID.encode() + b"\0" + payload)
                ).decode(),
            }
        )

    def seed(self, revision: str, remaining: int, **overrides: object) -> bytes:
        raw = self.envelope(revision, remaining, **overrides)
        (self.destination / "latest.json").write_bytes(raw)
        (self.destination / "revisions" / f"{revision}.json").write_bytes(raw)
        return raw

    def renew(self) -> dict[str, object]:
        return renewal.renew_channel(self.destination, self.keys_path, self.fields_path)

    def tree(self) -> dict[str, bytes]:
        return {
            str(path.relative_to(self.destination)): path.read_bytes()
            for path in self.destination.rglob("*")
            if path.is_file()
        }

    def enable_signer(self) -> None:
        os.environ[renewal.SIGNING_KEY_ENV] = pem(self.key)

    def test_check_is_read_only_and_window_includes_exact_expiry_boundary(self) -> None:
        for remaining, due, expired in (
            (renewal.RENEWAL_WINDOW_SECONDS + 1, False, False),
            (renewal.RENEWAL_WINDOW_SECONDS, True, False),
            (0, True, True),
            (-1, True, True),
        ):
            with self.subTest(remaining=remaining):
                self.seed("2", remaining)
                before = self.tree()
                result = renewal.inspect_channel(self.destination, self.keys_path)
                self.assertEqual(result["needs_renewal"], due)
                self.assertEqual(result["expired"], expired)
                self.assertEqual(result["remaining_seconds"], remaining)
                self.assertEqual(self.tree(), before)
                self.assertNotIn(renewal.SIGNING_KEY_ENV, os.environ)

    def test_early_renewal_does_not_require_a_signer_or_create_an_object(self) -> None:
        self.seed("2", renewal.RENEWAL_WINDOW_SECONDS + 1)
        before = self.tree()
        with patch.object(renewal, "load_signer") as signer:
            result = self.renew()
        signer.assert_not_called()
        self.assertFalse(result["renewed"])
        self.assertEqual(self.tree(), before)

    def test_renewal_increments_revision_preserves_history_and_signs_approved_fields(self) -> None:
        self.enable_signer()
        self.fields["bg"] = "new-approved-test"
        self.fields_path.write_text(json.dumps(self.fields))
        result = self.renew()
        raw = (self.destination / "latest.json").read_bytes()
        candidate = verify_bundle(raw, self.keys)
        candidate.require_valid(renewal.RENEWAL_WINDOW_SECONDS)
        self.assertEqual(candidate.revision, "3")
        self.assertEqual(candidate.issued_at, self.now)
        self.assertEqual(candidate.expires_at, self.now + renewal.LIFETIME_SECONDS)
        self.assertEqual(dict(candidate.fields), self.fields)
        self.assertEqual((self.destination / "revisions/2.json").read_bytes(), self.original)
        self.assertEqual((self.destination / "revisions/3.json").read_bytes(), raw)
        self.assertTrue(result["renewed"])
        self.assertEqual(result["previous_revision"], "2")
        self.assertNotIn(renewal.SIGNING_KEY_ENV, os.environ)
        self.assertNotIn(b"PRIVATE KEY", b"".join(self.tree().values()))

    def test_immediate_retry_is_idempotent_without_a_signer(self) -> None:
        self.enable_signer()
        self.renew()
        before = self.tree()
        result = self.renew()
        self.assertFalse(result["renewed"])
        self.assertEqual(result["revision"], "3")
        self.assertEqual(self.tree(), before)

    def test_decimal_revision_crosses_a_digit_boundary(self) -> None:
        self.seed("9", renewal.RENEWAL_WINDOW_SECONDS)
        self.enable_signer()
        self.assertEqual(self.renew()["revision"], "10")
        self.assertEqual(
            renewal.inspect_channel(self.destination, self.keys_path)["revision"], "10"
        )

    def test_history_limit_is_enforced_before_adding_a_new_object(self) -> None:
        before = self.tree()
        with patch.object(renewal, "MAX_REVISIONS", 1):
            self.assertTrue(
                renewal.inspect_channel(self.destination, self.keys_path)["needs_renewal"]
            )
            with self.assertRaisesRegex(release.ReleaseError, "entry limit"):
                self.renew()
        self.assertEqual(self.tree(), before)

    def test_expired_previous_envelope_can_be_renewed_without_bypassing_client_expiry(self) -> None:
        expired = self.seed("2", -1)
        store = BundleStore(keys=self.keys)
        with self.assertRaises(AnchorpassError) as error:
            store.accept(expired)
        self.assertEqual(error.exception.code, "ConfigExpired")
        self.enable_signer()
        self.renew()
        self.assertEqual(
            store.accept((self.destination / "latest.json").read_bytes()).revision, "3"
        )
        with self.assertRaises(AnchorpassError) as error:
            store.accept(expired)
        self.assertEqual(error.exception.code, "ConfigRollback")
        with self.assertRaises(AnchorpassError) as error:
            verify_bundle(expired, self.keys).require_valid()
        self.assertEqual(error.exception.code, "ConfigExpired")

    def test_invalid_previous_signature_is_rejected_even_if_expired(self) -> None:
        raw = json.loads(self.seed("2", -1))
        raw["signature"] = base64.b64encode(b"x" * 64).decode()
        invalid = release.canonical(raw)
        (self.destination / "latest.json").write_bytes(invalid)
        (self.destination / "revisions/2.json").write_bytes(invalid)
        before = self.tree()
        with patch.object(renewal, "load_signer") as signer:
            with self.assertRaises(AnchorpassError):
                self.renew()
        signer.assert_not_called()
        self.assertEqual(self.tree(), before)

    def test_rollback_pointer_and_conflicting_immutable_bytes_are_rejected(self) -> None:
        (self.destination / "revisions/3.json").write_bytes(self.envelope("3", -1))
        before = self.tree()
        for operation in (
            self.renew,
            lambda: renewal.inspect_channel(self.destination, self.keys_path),
        ):
            with self.assertRaisesRegex(release.ReleaseError, "roll back"):
                operation()
            self.assertEqual(self.tree(), before)
        (self.destination / "latest.json").write_bytes(
            self.envelope("2", renewal.RENEWAL_WINDOW_SECONDS - 1)
        )
        with self.assertRaisesRegex(release.ReleaseError, "immutable"):
            self.renew()

    def test_nonmatching_signer_cannot_create_any_signed_object(self) -> None:
        os.environ[renewal.SIGNING_KEY_ENV] = pem(Ed25519PrivateKey.generate())
        before = self.tree()
        with self.assertRaisesRegex(release.ReleaseError, "does not match"):
            self.renew()
        self.assertEqual(self.tree(), before)

    def test_malformed_or_missing_signer_fails_without_disclosing_environment(self) -> None:
        for secret in ("", "private-marker-not-a-pem"):
            with self.subTest(secret=bool(secret)):
                os.environ[renewal.SIGNING_KEY_ENV] = secret
                before = self.tree()
                output, errors = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                    code = renewal.main(
                        [
                            "renew",
                            "--destination",
                            str(self.destination),
                            "--keys",
                            str(self.keys_path),
                            "--fields",
                            str(self.fields_path),
                        ]
                    )
                self.assertEqual(code, 1)
                self.assertEqual(output.getvalue(), "")
                self.assertIn("Channel operation failed", errors.getvalue())
                self.assertNotIn("private-marker", errors.getvalue())
                self.assertEqual(self.tree(), before)

    def test_unapproved_development_trust_is_rejected(self) -> None:
        self.keys["development-1"] = self.keys[renewal.KEY_ID]
        self.keys_path.write_text(json.dumps(self.keys))
        with self.assertRaisesRegex(release.ReleaseError, "production public keys"):
            self.renew()

    def test_non_ed25519_signer_is_rejected(self) -> None:
        key = ec.generate_private_key(ec.SECP256R1())
        os.environ[renewal.SIGNING_KEY_ENV] = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode("ascii")
        before = self.tree()
        with self.assertRaisesRegex(release.ReleaseError, "must be Ed25519"):
            self.renew()
        self.assertEqual(self.tree(), before)

    def test_invalid_fields_and_duplicate_input_keys_are_rejected_before_signing(self) -> None:
        for raw in (b'{"bg":"x","bg":"y"}', b'{"unknown":"field"}', b"x" * 65537):
            with self.subTest(raw=raw[:40]):
                self.fields_path.write_bytes(raw)
                before = self.tree()
                with patch.object(renewal, "load_signer") as signer:
                    with self.assertRaises((release.ReleaseError, ValueError)):
                        self.renew()
                signer.assert_not_called()
                self.assertEqual(self.tree(), before)

    def test_excessive_prior_lifetime_and_revision_overflow_fail_before_signing(self) -> None:
        self.seed("2", renewal.RENEWAL_WINDOW_SECONDS, issued_at=1)
        with self.assertRaisesRegex(release.ReleaseError, "maximum lifetime"):
            self.renew()
        self.seed("9" * 20, renewal.RENEWAL_WINDOW_SECONDS)
        before = self.tree()
        with patch.object(renewal, "load_signer") as signer:
            with self.assertRaises(AnchorpassError):
                self.renew()
        signer.assert_not_called()
        self.assertEqual(self.tree(), before)

    def test_existing_lock_refuses_concurrent_renewal(self) -> None:
        lock = self.destination / ".channel.lock"
        lock.write_text("owned-test-lock\n")
        before = self.tree()
        with self.assertRaisesRegex(release.ReleaseError, "locked"):
            self.renew()
        self.assertEqual(self.tree(), before)

    def test_symlinked_history_cannot_write_outside_channel(self) -> None:
        outside = self.root / "outside"
        (self.destination / "revisions").rename(outside)
        (self.destination / "revisions").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(release.ReleaseError, "symlinks"):
            self.renew()
        self.assertEqual((outside / "2.json").read_bytes(), self.original)
        self.assertEqual({path.name for path in outside.iterdir()}, {"2.json"})

    def test_partial_local_write_retains_both_objects_and_requires_explicit_recovery(self) -> None:
        self.enable_signer()
        with patch.object(
            renewal, "replace_latest", side_effect=OSError("synthetic write failure")
        ):
            with self.assertRaises(OSError):
                self.renew()
        self.assertEqual((self.destination / "latest.json").read_bytes(), self.original)
        self.assertEqual((self.destination / "revisions/2.json").read_bytes(), self.original)
        verify_bundle(
            (self.destination / "revisions/3.json").read_bytes(), self.keys
        ).require_valid()
        before = self.tree()
        with self.assertRaisesRegex(release.ReleaseError, "roll back"):
            self.renew()
        self.assertEqual(self.tree(), before)

    def test_cli_check_without_a_secret_and_renew_with_secret_only_in_environment(self) -> None:
        command = [
            sys.executable,
            str(ROOT / "scripts/renew_channel.py"),
            "check",
            "--destination",
            str(self.destination),
            "--keys",
            str(self.keys_path),
            "--fields",
            str(self.fields_path),
        ]
        inspected = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
        self.assertEqual(inspected.returncode, 0, inspected.stderr)
        self.assertTrue(json.loads(inspected.stdout)["needs_renewal"])
        environment = {**os.environ, renewal.SIGNING_KEY_ENV: pem(self.key)}
        command[2] = "renew"
        renewed = subprocess.run(
            command, env=environment, capture_output=True, text=True, timeout=15, check=False
        )
        self.assertEqual(renewed.returncode, 0, renewed.stderr)
        self.assertTrue(json.loads(renewed.stdout)["renewed"])
        self.assertNotIn("PRIVATE KEY", renewed.stdout + renewed.stderr)


if __name__ == "__main__":
    unittest.main()
