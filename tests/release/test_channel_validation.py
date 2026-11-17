"""Renewal gates use disposable signed data without changing production trust or expiry."""

from __future__ import annotations

import base64
import contextlib
import hashlib
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
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import channel_validation as validation  # noqa: E402
import release  # noqa: E402
import renew_channel as renewal  # noqa: E402


class ValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory(prefix="anchorpass-channel-validation-test-")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.destination = self.root / "validation"
        self.fields = {
            "reason": "q",
            "hl": "en",
            "size": "invisible",
            "chr": "synthetic",
            "vh": "synthetic",
            "bg": "approved-synthetic",
        }
        self.production_key = Ed25519PrivateKey.generate()
        public = self.production_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        self.production_keys = {renewal.KEY_ID: base64.b64encode(public).decode("ascii")}
        self.production_raw = self.envelope()
        for directory in validation.DATA_DIRECTORIES:
            (self.source / directory).mkdir(parents=True)
            (self.source / directory / "bundle.json").write_bytes(self.production_raw)
            (self.source / directory / "keys.json").write_bytes(
                release.canonical(self.production_keys)
            )
        (self.source / "data/upstream-fields.json").write_bytes(release.canonical(self.fields))
        (self.source / "scripts").mkdir()
        (self.source / "scripts/sync_package_data.py").write_bytes(
            (ROOT / "scripts/sync_package_data.py").read_bytes()
        )
        (self.source / "application.py").write_text("VALUE = 'exact checked source'\n")
        (self.source / ".gitignore").write_text(".venv/\nartifacts/\n")
        self.run_git("init", "--quiet")
        self.commit = self.commit_fixture()
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop(renewal.SIGNING_KEY_ENV, None)

    def run_git(self, *arguments: str) -> str:
        return (
            subprocess.check_output(["git", "-C", str(self.source), *arguments], timeout=15)
            .decode()
            .strip()
        )

    def commit_fixture(self) -> str:
        self.run_git("add", ".")
        self.run_git(
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "--quiet",
            "-m",
            "Synthetic validation fixture",
        )
        return self.run_git("rev-parse", "HEAD")

    def envelope(self) -> bytes:
        # Expired production data is authenticated history, never valid client input.
        payload = release.canonical(
            {
                "schema_version": 1,
                "protocol_family": "recaptcha-v3/1",
                "revision": "1",
                "issued_at": 1,
                "expires_at": 2,
                "fields": self.fields,
            }
        )
        return release.canonical(
            {
                "key_id": renewal.KEY_ID,
                "payload": base64.b64encode(payload).decode("ascii"),
                "signature": base64.b64encode(
                    self.production_key.sign(PREFIX + renewal.KEY_ID.encode() + b"\0" + payload)
                ).decode("ascii"),
            }
        )

    def source_files(self) -> dict[str, bytes]:
        return {
            name: (self.source / name).read_bytes()
            for name in self.run_git("ls-files").splitlines()
        }

    def stage(self) -> dict[str, object]:
        return validation.stage_validation(self.source, self.destination, self.commit)

    def test_expired_source_can_stage_fresh_validation_without_changing_production(self) -> None:
        before = self.source_files()
        with self.assertRaises(AnchorpassError) as expired:
            verify_bundle(self.production_raw, self.production_keys).require_valid()
        self.assertEqual(expired.exception.code, "ConfigExpired")
        started = int(time.time())
        result = self.stage()
        self.assertEqual(result["source_commit"], self.commit)
        self.assertEqual(result["isolated_data_files"], sorted(validation.DATA_PATHS))
        self.assertEqual(self.source_files(), before)
        self.assertEqual(self.run_git("status", "--porcelain"), "")
        self.assertEqual(
            (self.destination / "application.py").read_bytes(), before["application.py"]
        )
        raw, keys_raw = validation.package_data(self.destination)
        keys = json.loads(keys_raw)
        bundle = BundleStore(keys=keys).accept(raw)
        self.assertEqual(bundle.revision, "1")
        self.assertGreaterEqual(bundle.issued_at, started)
        self.assertEqual(bundle.expires_at - bundle.issued_at, renewal.LIFETIME_SECONDS)
        self.assertEqual(dict(bundle.fields), self.fields)
        self.assertEqual(
            result["fields_sha256"], hashlib.sha256(release.canonical(self.fields)).hexdigest()
        )
        for envelope, trust in ((self.production_raw, keys), (raw, self.production_keys)):
            with self.assertRaises(AnchorpassError) as invalid:
                verify_bundle(envelope, trust)
            self.assertEqual(invalid.exception.code, "ConfigInvalid")
        for path in self.destination.rglob("*"):
            if path.is_file() and ".git" not in path.relative_to(self.destination).parts:
                self.assertNotIn(b"PRIVATE KEY", path.read_bytes(), str(path))
        # Staging does not make the original expired production envelope valid.
        with self.assertRaises(AnchorpassError) as expired:
            verify_bundle(self.production_raw, self.production_keys).require_valid()
        self.assertEqual(expired.exception.code, "ConfigExpired")

    def test_untracked_or_modified_source_and_wrong_commit_are_rejected(self) -> None:
        (self.source / "application.py").write_text("VALUE = 'modified'\n")
        with self.assertRaises(release.ReleaseError):
            self.stage()
        self.run_git("restore", "application.py")
        (self.source / "untracked.py").write_text("VALUE = 'untracked'\n")
        with self.assertRaisesRegex(release.ReleaseError, "untracked"):
            self.stage()
        (self.source / "untracked.py").unlink()
        with self.assertRaisesRegex(release.ReleaseError, "checked commit"):
            validation.stage_validation(self.source, self.destination, "0" * 40)
        self.assertFalse(self.destination.exists())

    def test_existing_or_nested_destination_is_rejected_without_source_changes(self) -> None:
        self.destination.mkdir()
        before = self.source_files()
        with self.assertRaisesRegex(release.ReleaseError, "must not exist"):
            self.stage()
        with self.assertRaisesRegex(release.ReleaseError, "outside the source"):
            validation.stage_validation(self.source, self.source / "nested", self.commit)
        self.assertEqual(self.source_files(), before)

    def test_production_secret_is_rejected_before_any_validation_process(self) -> None:
        os.environ[renewal.SIGNING_KEY_ENV] = "synthetic-secret-marker"
        with patch.object(validation, "git") as git:
            with self.assertRaisesRegex(release.ReleaseError, "must not be available"):
                self.stage()
        git.assert_not_called()
        self.assertFalse(self.destination.exists())

    def test_reviewed_upstream_fields_are_validated_even_when_embedded_fields_are_older(
        self,
    ) -> None:
        fields = {**self.fields, "bg": "reviewed-next-protocol-value"}
        (self.source / "data/upstream-fields.json").write_bytes(release.canonical(fields))
        self.commit = self.commit_fixture()
        before = self.source_files()
        result = self.stage()
        raw, keys_raw = validation.package_data(self.destination)
        self.assertEqual(dict(verify_bundle(raw, json.loads(keys_raw)).fields), fields)
        self.assertEqual(
            result["fields_sha256"], hashlib.sha256(release.canonical(fields)).hexdigest()
        )
        self.assertEqual(self.source_files(), before)

    def test_invalid_production_signature_is_not_hidden_by_synthetic_data(self) -> None:
        invalid = json.loads(self.production_raw)
        invalid["signature"] = base64.b64encode(b"x" * 64).decode("ascii")
        for directory in validation.DATA_DIRECTORIES:
            (self.source / directory / "bundle.json").write_bytes(release.canonical(invalid))
        self.commit = self.commit_fixture()
        with self.assertRaises(AnchorpassError):
            self.stage()

    def test_source_package_data_mismatch_is_not_hidden_by_staging(self) -> None:
        (self.source / "go/data/bundle.json").write_bytes(b"{}")
        self.commit = self.commit_fixture()
        with self.assertRaisesRegex(release.ReleaseError, "package data differs"):
            self.stage()

    def test_post_gate_verification_rejects_code_changes_and_untracked_modules(self) -> None:
        self.stage()
        for path, contents in (("application.py", "VALUE = 'changed'\n"), ("new.py", "x = 1\n")):
            with self.subTest(path=path):
                target = self.destination / path
                original = target.read_bytes() if target.exists() else None
                target.write_text(contents)
                with self.assertRaisesRegex(release.ReleaseError, "outside its isolated test data"):
                    validation.verify_validation(self.destination, self.commit)
                if original is None:
                    target.unlink()
                else:
                    target.write_bytes(original)
        validation.verify_validation(self.destination, self.commit)

    def test_cli_stage_and_verify_emit_only_public_metadata(self) -> None:
        for operation in ("stage", "verify"):
            output, errors = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                code = validation.main(
                    [
                        operation,
                        "--source",
                        str(self.source),
                        "--destination",
                        str(self.destination),
                        "--commit",
                        self.commit,
                    ]
                )
            self.assertEqual(code, 0, errors.getvalue())
            self.assertEqual(json.loads(output.getvalue())["source_commit"], self.commit)
            self.assertNotIn("PRIVATE KEY", output.getvalue() + errors.getvalue())


class WorkflowTests(unittest.TestCase):
    def test_validation_and_signing_have_separate_runners_and_permissions(self) -> None:
        workflow = (ROOT / ".github/workflows/channel.yml").read_text()
        validation_job = workflow.split("  validation:\n", 1)[1].split("  signing:\n", 1)[0]
        signing_job = workflow.split("  signing:\n", 1)[1].split("  notification:\n", 1)[0]
        self.assertNotIn("secrets.", validation_job)
        self.assertNotIn("contents: write", validation_job)
        self.assertIn("run: make check", validation_job)
        self.assertIn("make test-live", validation_job)
        self.assertIn("working-directory: ${{ runner.temp }}/channel-validation", validation_job)
        self.assertIn("inputs.validate_now == true", validation_job)
        self.assertIn("needs: validation", signing_job)
        self.assertIn("if: needs.validation.outputs.due == 'true'", signing_job)
        self.assertNotIn("validate_now", signing_job)
        self.assertIn("ref: ${{ needs.validation.outputs.checked_sha }}", signing_job)
        self.assertNotIn("make check", signing_job)
        self.assertNotIn("make test-live", signing_job)
        self.assertNotIn("download-artifact", signing_job)
        self.assertIn("secrets.ANCHORPASS_SIGNING_KEY_PEM", signing_job)
        self.assertIn("item.user?.login === 'github-actions[bot]'", workflow)


if __name__ == "__main__":
    unittest.main()
