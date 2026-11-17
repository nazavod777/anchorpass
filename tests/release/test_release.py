"""Release invariants and registry failure paths, without external side effects."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import release  # noqa: E402
import release_check  # noqa: E402
import release_publish as publisher  # noqa: E402

VERSION = "0.1.0"


def fixture_source(root: Path) -> None:
    content = {
        "python/pyproject.toml": '[project]\nname="anchorpass"\nversion="0.1.0"\n',
        "rust/Cargo.toml": '[package]\nname="anchorpass"\nversion="0.1.0"\n',
        "js/package.json": json.dumps({"name": "anchorpass", "version": VERSION}),
        "js/package-lock.json": json.dumps(
            {"version": VERSION, "packages": {"": {"version": VERSION}}}
        ),
        "go/go.mod": f"module {release.MODULE}\n\ngo 1.23.0\n",
        "spec/contract.json": json.dumps({"version": VERSION}),
        "LICENSE": "MIT",
        "NOTICE": "Source",
    }
    for name, value in content.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)


def archive(path: Path, files: dict[str, bytes]) -> None:
    if path.suffix in {".whl", ".zip"}:
        with zipfile.ZipFile(path, "w") as output:
            for name, data in files.items():
                output.writestr(name, data)
    else:
        with tarfile.open(path, "w:gz") as output:
            for name, data in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                output.addfile(info, io.BytesIO(data))


def fake_build(root: Path, target: str, version: str, stage: Path, timeout: int) -> None:
    common = {
        "data/bundle.json": b"{}",
        "data/keys.json": b"{}",
        "LICENSE": b"MIT",
        "NOTICE": b"Source",
    }
    if target == "python":
        metadata = f"anchorpass-{version}.dist-info/"
        files = {
            f"anchorpass/{key}": value for key, value in common.items() if key.startswith("data/")
        }
        files.update(
            {
                "anchorpass/py.typed": b"",
                "anchorpass/__init__.py": b"",
                metadata + "METADATA": f"Version: {version}\n".encode(),
                metadata + "licenses/LICENSE": b"MIT",
                metadata + "licenses/NOTICE": b"Source",
            }
        )
        archive(stage / f"anchorpass-{version}-py3-none-any.whl", files)
        files = {
            f"anchorpass-{version}/src/anchorpass/{key}": value
            for key, value in common.items()
            if key.startswith("data/")
        }
        files.update(
            {
                f"anchorpass-{version}/{key}": value
                for key, value in common.items()
                if not key.startswith("data/")
            }
        )
        files[f"anchorpass-{version}/PKG-INFO"] = f"Version: {version}\n".encode()
        archive(stage / f"anchorpass-{version}.tar.gz", files)
    elif target == "npm":
        files = {f"package/{key}": value for key, value in common.items()}
        files.update(
            {
                f"package/dist/{name}": b""
                for name in ("worker.js", "index.js", "index.mjs", "index.d.ts")
            }
        )
        files["package/package.json"] = json.dumps({"version": version}).encode()
        archive(stage / f"anchorpass-{version}.tgz", files)
    elif target == "rust":
        files = {f"anchorpass-{version}/{key}": value for key, value in common.items()}
        files[f"anchorpass-{version}/Cargo.toml"] = f'[package]\nversion="{version}"\n'.encode()
        archive(stage / f"anchorpass-{version}.crate", files)
    else:
        prefix = f"{release.MODULE}@v{version}/"
        archive(stage / f"v{version}.zip", {prefix + key: value for key, value in common.items()})
        (stage / f"v{version}.mod").write_text(f"module {release.MODULE}\n")
        (stage / f"v{version}.info").write_text(json.dumps({"Version": f"v{version}"}))


class ReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory(prefix="anchorpass-release-test-")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name) / "source"
        fixture_source(self.root)
        self.output = Path(self.scratch.name) / "release"

    def prepare(self) -> dict:
        with patch.object(release, "build_target", side_effect=fake_build):
            return release.prepare(self.root, self.output, VERSION, 10)

    def test_version_mismatch_is_rejected_before_creating_output(self) -> None:
        (self.root / "js/package.json").write_text('{"name":"anchorpass","version":"0.2.0"}')
        with self.assertRaisesRegex(release.ReleaseError, "Version mismatch"):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_ready_artifacts_are_verified_and_reused_without_building(self) -> None:
        manifest = self.prepare()
        original = (self.output / "release.json").read_bytes()
        with patch.object(release, "build_target", side_effect=AssertionError("Must not rebuild")):
            self.assertEqual(manifest, release.prepare(self.root, self.output, VERSION, 10))
        self.assertEqual(original, (self.output / "release.json").read_bytes())
        self.assertEqual(len(manifest["artifacts"]), 7)

    def test_partial_failure_resumes_only_unprepared_targets(self) -> None:
        built = []

        def fail(root: Path, target: str, version: str, stage: Path, timeout: int) -> None:
            built.append(target)
            if target == "rust":
                raise release.ReleaseError("Controlled build failure")
            fake_build(root, target, version, stage, timeout)

        with patch.object(release, "build_target", side_effect=fail):
            with self.assertRaisesRegex(release.ReleaseError, "Controlled"):
                release.prepare(self.root, self.output, VERSION, 10)
        self.assertFalse((self.output / "release.json").exists())
        with patch.object(release, "build_target", side_effect=fake_build) as builder:
            release.prepare(self.root, self.output, VERSION, 10)
        self.assertEqual([call.args[1] for call in builder.call_args_list], ["rust", "go"])
        self.assertEqual(built, ["python", "npm", "rust"])

    def test_renamed_target_is_recovered_when_receipt_was_interrupted(self) -> None:
        manifest = self.prepare()
        entries = release.events(self.output)
        (self.output / "journal.jsonl").write_bytes(
            b"".join(
                json.dumps(event).encode() + b"\n"
                for event in entries
                if not (event.get("event") == "prepared" and event.get("target") == "npm")
            )
        )
        self.assertEqual(self.prepare(), manifest)

    def test_source_and_artifact_mutations_fail_without_overwrite(self) -> None:
        self.prepare()
        (self.root / "LICENSE").write_text("Changed")
        with self.assertRaisesRegex(release.ReleaseError, "identity changed"):
            self.prepare()
        path = self.output / "go/v0.1.0.mod"
        path.write_text(f"module {release.MODULE}\n// altered\n")
        with self.assertRaisesRegex(release.ReleaseError, "Journal"):
            release.verify(self.output)
        self.assertIn("altered", path.read_text())

    def test_local_delivery_is_resumable_and_conflicts_fail_closed(self) -> None:
        self.prepare()
        destination = Path(self.scratch.name) / "handoff"
        real_write = release.write_once

        def fail(path: Path, value: bytes) -> None:
            if path.name.endswith(".crate"):
                raise release.ReleaseError("Controlled delivery failure")
            real_write(path, value)

        with patch.object(release, "write_once", side_effect=fail):
            with self.assertRaisesRegex(release.ReleaseError, "Controlled"):
                release.stage_release(self.output, destination)
        self.assertEqual(
            [
                event["target"]
                for event in release.events(self.output)
                if event["event"] == "staged"
            ],
            ["python", "npm"],
        )
        release.stage_release(self.output, destination)
        release.stage_release(self.output, destination)
        self.assertEqual(
            len([event for event in release.events(self.output) if event["event"] == "staged"]), 4
        )
        (destination / "0.1.0/go/v0.1.0.mod").write_text("conflict")
        with self.assertRaisesRegex(release.ReleaseError, "overwrite different bytes"):
            release.stage_release(self.output, destination)

    def test_archive_missing_worker_and_traversal_are_rejected(self) -> None:
        self.prepare()
        path = self.output / "npm/anchorpass-0.1.0.tgz"
        files = release.archive_files(path)
        del files["package/dist/worker.js"]
        archive(path, files)
        with self.assertRaisesRegex(release.ReleaseError, "missing required"):
            release.inspect_target(path.parent, "npm", VERSION)
        archive(path, {"../outside": b"forbidden"})
        with self.assertRaisesRegex(release.ReleaseError, "Unsafe"):
            release.archive_files(path)

    def test_lock_contention_and_incomplete_journal_fail_closed(self) -> None:
        self.prepare()
        with release.lock(self.output / ".lock"):
            with self.assertRaisesRegex(release.ReleaseError, "locked"):
                self.prepare()
        with (self.output / "journal.jsonl").open("ab") as output:
            output.write(b'{"event":')
        with self.assertRaisesRegex(release.ReleaseError, "corrupt journal"):
            self.prepare()

    def test_remote_complete_version_is_confirmed_without_upload(self) -> None:
        self.prepare()
        digest = release.sha256(self.output / "release.json")
        with (
            patch.object(publisher, "probe", return_value=True),
            patch.object(release, "run") as upload,
        ):
            publisher.publish(self.output, "npm", digest, 10)
            publisher.publish(self.output, "npm", digest, 10)
        upload.assert_not_called()
        self.assertEqual(
            len([event for event in release.events(self.output) if event["event"] == "published"]),
            1,
        )

    def test_remote_mismatch_never_uploads_or_records_success(self) -> None:
        manifest = self.prepare()
        metadata = {
            "name": "anchorpass",
            "version": VERSION,
            "dist": {"tarball": "https://registry.npmjs.org/anchorpass/-/anchorpass-0.1.0.tgz"},
        }
        with patch.object(
            publisher, "request", side_effect=[json.dumps(metadata).encode(), b"wrong"]
        ):
            with self.assertRaisesRegex(release.ReleaseError, "different bytes"):
                publisher.probe(manifest, "npm")
        self.assertFalse(
            any(event["event"] == "published" for event in release.events(self.output))
        )

    def test_partial_pypi_compares_existing_and_uploads_only_missing(self) -> None:
        manifest = self.prepare()
        artifact = next(item for item in manifest["artifacts"] if item["path"].endswith(".whl"))
        name = Path(artifact["path"]).name
        metadata = {
            "info": {"name": "anchorpass", "version": VERSION},
            "urls": [{"filename": name, "url": f"https://files.pythonhosted.org/{name}"}],
        }
        present: set[str] = set()
        with patch.object(
            publisher,
            "request",
            side_effect=[
                json.dumps(metadata).encode(),
                (self.output / artifact["path"]).read_bytes(),
            ],
        ):
            self.assertFalse(publisher.probe(manifest, "python", present))
        self.assertEqual(present, {name})

        def preflight(manifest: dict, target: str, found: set[str] | None = None) -> bool:
            if found is not None:
                found.add(name)
                return False
            return True

        with (
            patch.object(publisher, "probe", side_effect=preflight),
            patch.object(release, "run") as upload,
        ):
            publisher.publish(
                self.output, "python", release.sha256(self.output / "release.json"), 10
            )
        command = upload.call_args.args[0]
        self.assertNotIn(str(self.output / artifact["path"]), command)
        self.assertTrue(any(item.endswith(".tar.gz") for item in command))

    def test_uncertain_upload_can_succeed_only_after_remote_confirmation(self) -> None:
        self.prepare()
        digest = release.sha256(self.output / "release.json")
        with (
            patch.object(publisher, "probe", side_effect=[False, True, True]),
            patch.object(release, "run", side_effect=release.ReleaseError("response lost")),
        ):
            self.assertEqual(publisher.publish(self.output, "npm", digest, 10), "confirmed")
        self.assertTrue(any(event["event"] == "published" for event in release.events(self.output)))

    def test_approval_must_match_exact_manifest(self) -> None:
        self.prepare()
        with patch.object(publisher, "probe") as probe:
            with self.assertRaisesRegex(release.ReleaseError, "Approval"):
                publisher.publish(self.output, "npm", hashlib.sha256(b"other").hexdigest(), 10)
        probe.assert_not_called()

    def test_manifest_version_cannot_escape_handoff(self) -> None:
        self.prepare()
        manifest = release.read_json(self.output / "release.json")
        manifest["version"] = ".."
        (self.output / "release.json").write_bytes(release.canonical(manifest))
        entries = release.events(self.output)
        entries[0]["version"] = ".."
        (self.output / "journal.jsonl").write_bytes(
            b"".join(json.dumps(event).encode() + b"\n" for event in entries)
        )
        with self.assertRaisesRegex(release.ReleaseError, "X.Y.Z"):
            release.verify(self.output)
        with self.assertRaisesRegex(release.ReleaseError, "X.Y.Z"):
            release.stage_release(self.output, Path(self.scratch.name) / "handoff")

    def test_parent_directory_symlinks_are_rejected(self) -> None:
        self.prepare()
        external = Path(self.scratch.name) / "external"
        (self.output / "python").rename(external)
        (self.output / "python").symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(release.ReleaseError, "symlinks"):
            release.verify(self.output)
        (self.output / "python").unlink()
        external.rename(self.output / "python")
        external.mkdir()
        destination = Path(self.scratch.name) / "handoff"
        destination.mkdir()
        (destination / VERSION).symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(release.ReleaseError, "symlinks"):
            release.stage_release(self.output, destination)
        self.assertFalse((external / "release.json").exists())

    def test_decompressed_limit_includes_pax_headers(self) -> None:
        path = Path(self.scratch.name) / "pax.tgz"
        with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT) as output:
            member = tarfile.TarInfo("package/ok")
            member.size = 1
            member.pax_headers = {"comment": "x" * 8192}
            output.addfile(member, io.BytesIO(b"x"))
        with patch.object(release, "MAX_ARCHIVE_BYTES", 4096):
            with self.assertRaisesRegex(release.ReleaseError, "decompressed-byte"):
                release.archive_files(path)

    def test_fifo_input_is_rejected_without_blocking(self) -> None:
        path = Path(self.scratch.name) / "input-fifo"
        os.mkfifo(path)
        with self.assertRaisesRegex(release.ReleaseError, "regular input"):
            release.read_bounded(path, 100)

    def test_unconfirmed_upload_needs_a_separate_retry_decision(self) -> None:
        self.prepare()
        digest = release.sha256(self.output / "release.json")
        release.append_event(
            self.output,
            {"event": "publication-attempt", "target": "npm", "manifest_sha256": digest},
        )
        with (
            patch.object(publisher, "probe", return_value=False),
            patch.object(release, "run") as upload,
        ):
            with self.assertRaisesRegex(release.ReleaseError, "Previous upload is unconfirmed"):
                publisher.publish(self.output, "npm", digest, 10)
        upload.assert_not_called()

    def test_publish_workflow_passes_retry_input_to_uploading_step(self) -> None:
        workflow = (
            Path(__file__).resolve().parents[2] / ".github/workflows/release.yml"
        ).read_text()
        uploading = workflow.split(
            "- name: Publish missing files and confirm exact registry bytes\n", 1
        )[1]
        uploading = uploading.split("- name: Retain receipts", 1)[0]
        environment, command = uploading.split("        run: |\n", 1)
        self.assertIn("RETRY_UNCONFIRMED: ${{ inputs.retry_unconfirmed }}", environment)
        self.assertIn("os.environ.get('RETRY_UNCONFIRMED')", command)
        self.assertIn("command.append('--retry-unconfirmed')", command)

    def test_forged_pax_size_cannot_allocate_its_declared_length(self) -> None:
        path = Path(self.scratch.name) / "forged-pax.tgz"
        header = tarfile.TarInfo("pax")
        header.type = tarfile.XHDTYPE
        header.size = 1073741824
        path.write_bytes(gzip.compress(header.tobuf() + b"\0" * 1024))

        class GuardedFile(io.BytesIO):
            def read(self, size: int = -1) -> bytes:
                if size > 4096:
                    raise AssertionError("Test prevented an unsafe large allocation")
                return super().read(size)

        with patch.object(release.tempfile, "TemporaryFile", return_value=GuardedFile()):
            with self.assertRaisesRegex(release.ReleaseError, "bounded content"):
                release.archive_files(path)

    def test_installed_consumer_directory_must_be_outside_checkout(self) -> None:
        with self.assertRaisesRegex(release.ReleaseError, "outside the source checkout"):
            release_check.check(self.output, release.ROOT / "artifacts/invalid-consumer", 10)


if __name__ == "__main__":
    unittest.main()
