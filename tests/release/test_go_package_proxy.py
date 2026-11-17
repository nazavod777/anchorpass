"""Offline dependencies must not replace or weaken the candidate Go archive."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import go_package_proxy  # noqa: E402
import release  # noqa: E402
import release_check  # noqa: E402

VERSION = "v0.1.0"
DEPENDENCY = "example.org/dependency"
DEPENDENCY_VERSION = "v1.0.0"
CONSUMER = f"""package consumer
import (
    "testing"
    anchorpass "{release.MODULE}"
)
func TestLocalCandidate(t *testing.T) {{
    if anchorpass.Value() != 42 {{ t.Fatal("candidate was substituted") }}
}}
"""


def module_archive(directory: Path, module: str, version: str, files: dict[str, str]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(directory / f"{version}.zip", "w") as archive:
        for name, contents in files.items():
            archive.writestr(f"{module}@{version}/{name}", contents)
    (directory / f"{version}.mod").write_text(files["go.mod"])
    (directory / f"{version}.info").write_text(
        json.dumps({"Version": version, "Time": "2026-01-01T00:00:00Z"})
    )
    (directory / "list").write_text(version + "\n")


@unittest.skipUnless(shutil.which("go"), "Go toolchain is required")
class GoPackageProxyTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="anchorpass-offline-go-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cache = self.root / "cached-modules"
        self.downloads = self.cache / "cache/download"
        self.dependency = self.downloads / DEPENDENCY / "@v"
        self.dependency_files = {
            "go.mod": f"module {DEPENDENCY}\n\ngo 1.23.0\n",
            "value.go": "package dependency\nconst Value = 42\n",
        }
        module_archive(self.dependency, DEPENDENCY, DEPENDENCY_VERSION, self.dependency_files)
        bootstrap_environment = {
            **os.environ,
            "GOENV": "off",
            "GOFLAGS": "",
            "GOWORK": "off",
            "GOTOOLCHAIN": "local",
            "GOPROXY": self.downloads.as_uri(),
            "GOSUMDB": "off",
            "GOPRIVATE": "",
            "GONOPROXY": "none",
            "GOMODCACHE": str(self.root / "checksum-cache"),
        }
        download = subprocess.run(
            ["go", "mod", "download", "-json", f"{DEPENDENCY}@{DEPENDENCY_VERSION}"],
            cwd=self.root,
            env=bootstrap_environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        checksums = json.loads(download.stdout)
        self.sums = (
            f"{DEPENDENCY} {DEPENDENCY_VERSION} {checksums['Sum']}\n"
            f"{DEPENDENCY} {DEPENDENCY_VERSION}/go.mod {checksums['GoModSum']}\n"
        )
        self.artifacts = self.root / "sealed"
        self.candidate_files = {
            "go.mod": (
                f"module {release.MODULE}\n\ngo 1.23.0\n\n"
                f"require {DEPENDENCY} {DEPENDENCY_VERSION}\n"
            ),
            "go.sum": self.sums,
            "value.go": (
                f'package anchorpass\nimport "{DEPENDENCY}"\n'
                "func Value() int { return dependency.Value }\n"
            ),
        }
        module_archive(self.artifacts / "go", release.MODULE, VERSION, self.candidate_files)
        module_archive(
            self.downloads / release.MODULE / "@v",
            release.MODULE,
            VERSION,
            {
                **self.candidate_files,
                "value.go": "package anchorpass\nfunc Value() int { return 0 }\n",
            },
        )
        self.environment = {
            **os.environ,
            "GOMODCACHE": str(self.cache),
            "GOFLAGS": "-modfile=/missing/override.mod",
            "GOPRIVATE": "*",
            "GONOPROXY": "*",
        }
        self.work = self.root / "consumer-work"
        self.work.mkdir()

    def run_sealed_go_check(self) -> None:
        with patch.object(release_check, "CONSUMER_TEST", CONSUMER):
            release_check.check_go(self.artifacts, self.work, "0.1.0", 30, self.environment)

    def test_sealed_candidate_and_cached_dependency_install_without_cache_mutation(self):
        before = {
            path: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.downloads.rglob("*")
            if path.is_file()
        }
        self.run_sealed_go_check()
        candidate = self.work / "proxy" / release.MODULE / "@v" / f"{VERSION}.zip"
        self.assertFalse(candidate.is_symlink())
        self.assertEqual(
            candidate.read_bytes(), (self.artifacts / "go" / candidate.name).read_bytes()
        )
        self.assertIn(self.sums, (self.work / "go-consumer/go.sum").read_text())
        self.assertEqual(
            before,
            {
                path: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.downloads.rglob("*")
                if path.is_file()
            },
        )

    def test_missing_candidate_never_falls_back_to_cached_candidate(self):
        consumer = self.work / "consumer"
        consumer.mkdir()
        environment = go_package_proxy.prepare_consumer(
            self.artifacts / "go",
            consumer,
            self.work / "proxy",
            self.work / "modcache",
            module=release.MODULE,
            version=VERSION,
            environment=self.environment,
        )
        (self.work / "proxy" / release.MODULE / "@v" / f"{VERSION}.zip").unlink()
        with self.assertRaises(release.ReleaseError):
            release.run(
                ["go", "mod", "download", f"{release.MODULE}@{VERSION}"],
                consumer,
                self.work / "missing-candidate.log",
                30,
                environment,
            )
        self.assertIn("no such file", (self.work / "missing-candidate.log").read_text())

    def test_packaged_checksum_rejects_modified_cached_dependency(self):
        module_archive(
            self.dependency,
            DEPENDENCY,
            DEPENDENCY_VERSION,
            {**self.dependency_files, "value.go": "package dependency\nconst Value = 0\n"},
        )
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(release.ReleaseError):
            self.run_sealed_go_check()
        self.assertIn("checksum mismatch", (self.work / "go-install.log").read_text())

    def test_missing_cached_dependency_explains_the_offline_prerequisite(self):
        (self.dependency / f"{DEPENDENCY_VERSION}.zip").unlink()
        error_output = io.StringIO()
        with contextlib.redirect_stderr(error_output), self.assertRaises(release.ReleaseError):
            self.run_sealed_go_check()
        self.assertIn("no such file", (self.work / "go-install.log").read_text())
        self.assertIn("make check", error_output.getvalue())


if __name__ == "__main__":
    unittest.main()
