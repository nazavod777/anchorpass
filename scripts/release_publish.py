#!/usr/bin/env python3
"""Publish an explicitly approved manifest, confirming exact public registry bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import release

REGISTRIES = {
    "python": "https://pypi.org/pypi/anchorpass/{version}/json",
    "npm": "https://registry.npmjs.org/anchorpass/{version}",
    "rust": "https://crates.io/api/v1/crates/anchorpass/{version}",
}
DOWNLOAD_HOSTS = {
    "python": "files.pythonhosted.org",
    "npm": "registry.npmjs.org",
    "rust": "static.crates.io",
}
MAX_ATTEMPTS = 6
REQUEST_TIMEOUT = 20


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def request(
    url: str, *, missing_ok: bool = False, limit: int = release.MAX_METADATA_BYTES
) -> bytes | None:
    request_value = urllib.request.Request(url, headers={"User-Agent": "anchorpass-release/1"})
    # Publication confirmation is independent of ambient proxy configuration.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request_value, timeout=REQUEST_TIMEOUT) as response:
            deadline = time.monotonic() + 60
            value = bytearray()
            while len(value) <= limit:
                block = response.read1(min(65536, limit + 1 - len(value)))
                if not block:
                    return bytes(value)
                value.extend(block)
                if time.monotonic() >= deadline:
                    raise release.ReleaseError(f"Registry response exceeded its deadline: {url}")
            raise release.ReleaseError(f"Registry response exceeds the limit: {url}")
    except urllib.error.HTTPError as error:
        if missing_ok and error.code == 404:
            return None
        raise release.ReleaseError(f"Registry returned HTTP {error.code}: {url}") from None
    except (urllib.error.URLError, TimeoutError) as error:
        raise release.ReleaseError(f"Registry check failed for {url}: {error}") from None


def target_artifacts(manifest: dict[str, Any], target: str) -> list[dict[str, Any]]:
    return [item for item in manifest["artifacts"] if item["path"].startswith(target + "/")]


def registry_files(target: str, version: str, metadata: dict[str, Any]) -> dict[str, str]:
    if target == "python":
        if metadata["info"]["version"] != version or metadata["info"]["name"] != "anchorpass":
            raise release.ReleaseError("PyPI returned a different package/version")
        return {item["filename"]: item["url"] for item in metadata["urls"]}
    if target == "npm":
        if metadata["version"] != version or metadata["name"] != "anchorpass":
            raise release.ReleaseError("npm returned a different package/version")
        return {f"anchorpass-{version}.tgz": metadata["dist"]["tarball"]}
    if metadata["version"]["num"] != version or metadata["version"]["crate"] != "anchorpass":
        raise release.ReleaseError("crates.io returned a different package/version")
    name = f"anchorpass-{version}.crate"
    return {name: f"https://static.crates.io/crates/anchorpass/{name}"}


def probe(manifest: dict[str, Any], target: str, present: set[str] | None = None) -> bool:
    """Return false only for confirmed absence; an existing conflict always fails."""
    raw = request(REGISTRIES[target].format(version=manifest["version"]), missing_ok=True)
    if raw is None:
        return False
    metadata = json.loads(raw)
    files = registry_files(target, manifest["version"], metadata)
    artifacts = target_artifacts(manifest, target)
    expected = {Path(item["path"]).name: item for item in artifacts}
    # PyPI may be partially published: the caller must not silently replace or skip it.
    if set(files) != set(expected):
        if target != "python" or not set(files) < set(expected):
            raise release.ReleaseError(f"Existing {target} version has a different artifact set")
    for filename, url in files.items():
        parsed = urllib.parse.urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != DOWNLOAD_HOSTS[target]
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            raise release.ReleaseError(f"Untrusted artifact download location for {target}")
        raw_artifact = request(url, limit=release.MAX_ARTIFACT_BYTES)
        if (
            raw_artifact is None
            or hashlib.sha256(raw_artifact).hexdigest() != expected[filename]["sha256"]
        ):
            raise release.ReleaseError(
                f"Existing {target} version contains different bytes: {filename}"
            )
        if present is not None:
            present.add(filename)
    return len(files) == len(expected)


def publish(
    output: Path,
    target: str,
    approved_digest: str,
    timeout: int,
    *,
    retry_unconfirmed: bool = False,
) -> str:
    with release.lock(output / ".lock"):
        manifest = release.verify(output)
        digest = release.sha256(output / "release.json")
        if approved_digest != digest:
            raise release.ReleaseError("Approval does not identify this exact release manifest")
        confirmed = [
            entry
            for entry in release.events(output)
            if entry.get("event") == "published" and entry.get("target") == target
        ]
        present: set[str] = set()
        matching = probe(manifest, target, present)
        if confirmed and not matching:
            raise release.ReleaseError(
                "Previously confirmed publication is now absent or partial; operator recovery required"
            )
        attempted = any(
            entry.get("event") == "publication-attempt" and entry.get("target") == target
            for entry in release.events(output)
        )
        if attempted and not matching and not retry_unconfirmed:
            raise release.ReleaseError(
                "Previous upload is unconfirmed; investigate registry state, then explicitly allow retry"
            )
        if not matching:
            artifacts = target_artifacts(manifest, target)
            paths = [
                str(output / item["path"])
                for item in artifacts
                if Path(item["path"]).name not in present
            ]
            if target == "python":
                command = [
                    sys.executable,
                    "-m",
                    "twine",
                    "upload",
                    "--non-interactive",
                    "--disable-progress-bar",
                    "--repository-url",
                    "https://upload.pypi.org/legacy/",
                    *paths,
                ]
            elif target == "npm":
                command = [
                    "npm",
                    "publish",
                    "--access",
                    "public",
                    "--ignore-scripts",
                    "--registry",
                    "https://registry.npmjs.org/",
                    *paths,
                ]
            else:
                # Cargo publishes a verified extracted crate, never the changing checkout.
                command = []
            release.append_event(
                output,
                {"event": "publication-attempt", "target": target, "manifest_sha256": digest},
            )
            log = output / f"publication-{target}.log"
            try:
                if target == "rust":
                    import tarfile
                    import tempfile

                    with tempfile.TemporaryDirectory(prefix="anchorpass-publish-") as name:
                        work = Path(name)
                        with tarfile.open(paths[0]) as archive:
                            archive.extractall(work, filter="data")
                        package = work / f"anchorpass-{manifest['version']}"
                        original_manifest = package / "Cargo.toml.orig"
                        if not original_manifest.is_file():
                            raise release.ReleaseError("Crate has no original packaging manifest")
                        (package / "Cargo.toml").write_bytes(original_manifest.read_bytes())
                        environment = dict(os.environ)
                        environment.setdefault("CARGO_BUILD_JOBS", "2")
                        environment["SOURCE_DATE_EPOCH"] = release.SOURCE_EPOCH
                        environment["CARGO_HOME"] = str(work / "cargo-home")
                        environment["CARGO_TARGET_DIR"] = str(package / "target")
                        release.run(
                            ["cargo", "package", "--locked", "--allow-dirty", "--no-verify"],
                            package,
                            log,
                            timeout,
                            environment,
                            capture_output=False,
                        )
                        repacked = (
                            package / f"target/package/anchorpass-{manifest['version']}.crate"
                        )
                        if release.sha256(repacked) != artifacts[0]["sha256"]:
                            raise release.ReleaseError(
                                "Cargo repack differs from the approved crate; publication stopped"
                            )
                        release.run(
                            [
                                "cargo",
                                "publish",
                                "--locked",
                                "--allow-dirty",
                                "--no-verify",
                                "--registry",
                                "crates-io",
                            ],
                            package,
                            log,
                            timeout,
                            environment,
                            capture_output=False,
                        )
                else:
                    release.run(
                        command, output, log, timeout, dict(os.environ), capture_output=False
                    )
            except release.ReleaseError:
                # A lost upload response is not evidence of failure or success.
                if not probe(manifest, target):
                    raise
            for attempt in range(MAX_ATTEMPTS):
                if probe(manifest, target):
                    matching = True
                    break
                if attempt + 1 < MAX_ATTEMPTS:
                    time.sleep(2)
            if not matching:
                raise release.ReleaseError(
                    f"{target} upload has no exact public confirmation; resume after inspection"
                )
        event = {
            "event": "published",
            "target": target,
            "manifest_sha256": digest,
            "registry": REGISTRIES[target].format(version=manifest["version"]),
            "artifacts": target_artifacts(manifest, target),
        }
        if confirmed and confirmed != [event]:
            raise release.ReleaseError("Publication journal identity mismatch")
        if not confirmed:
            release.append_event(output, event)
        return "confirmed"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--target", required=True, choices=("python", "npm", "rust"))
    parser.add_argument("--approved-manifest-sha256", required=True)
    parser.add_argument(
        "--execute", action="store_true", help="Explicitly authorize registry side effects"
    )
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument(
        "--retry-unconfirmed",
        action="store_true",
        help="Retry missing files only after operator investigation of a prior upload",
    )
    args = parser.parse_args()
    try:
        if not args.execute:
            raise release.ReleaseError("Publication requires explicit --execute approval")
        if not 1 <= args.timeout <= 3600:
            raise release.ReleaseError("Publication timeout must be between 1 and 3600 seconds")
        result = publish(
            args.output.resolve(),
            args.target,
            args.approved_manifest_sha256,
            args.timeout,
            retry_unconfirmed=args.retry_unconfirmed,
        )
        print(json.dumps({"target": args.target, "status": result}))
        return 0
    except (release.ReleaseError, OSError, ValueError, KeyError) as error:
        print(f"Publication failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
