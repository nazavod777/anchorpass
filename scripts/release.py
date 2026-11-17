#!/usr/bin/env python3
"""Prepare, verify and stage immutable local release artifacts; never publish remotely."""

from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ("python", "npm", "rust", "go")
MODULE = "github.com/nazavod777/anchorpass/go"
EXCLUDED = {
    ".git",
    ".venv",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "__pycache__",
    "node_modules",
    "target",
    "build",
    "dist",
    "benchmarks",
}
MAX_METADATA_BYTES = 1048576
MAX_ARTIFACT_BYTES = 67108864
MAX_FILES = 10000
MAX_ARCHIVE_BYTES = 134217728
SOURCE_EPOCH = "315532800"


class ReleaseError(Exception):
    """A release invariant was not satisfied."""


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def validate_version(version: Any) -> None:
    if not isinstance(version, str) or not re.fullmatch(
        r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", version
    ):
        raise ReleaseError("Use an exact stable X.Y.Z release version")


def validate_identity(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {
        "format",
        "version",
        "module",
        "source_sha256",
        "tags",
    }:
        raise ReleaseError("Invalid release identity schema")
    validate_version(value["version"])
    version = value["version"]
    if (
        type(value["format"]) is not int
        or value["format"] != 1
        or value["module"] != MODULE
        or not isinstance(value["source_sha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", value["source_sha256"])
        or value["tags"]
        != {
            "python": f"py-v{version}",
            "npm": f"npm-v{version}",
            "rust": f"rust-v{version}",
            "go": f"go/v{version}",
        }
    ):
        raise ReleaseError("Noncanonical release identity")


def safe_directory(path: Path, *, create: bool = False) -> None:
    if path.is_symlink():
        raise ReleaseError(f"Directory symlinks are not allowed: {path}")
    if create:
        path.mkdir(exist_ok=True)
    if not path.is_dir():
        raise ReleaseError(f"Expected an owned directory: {path}")


def sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ARTIFACT_BYTES:
        raise ReleaseError(f"Not a bounded regular artifact: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1048576), b""):
            digest.update(block)
    return digest.hexdigest()


def read_bounded(path: Path, limit: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ReleaseError(f"Not a bounded regular input: {path}")
        raw = source.read(limit + 1)
        if len(raw) > limit:
            raise ReleaseError(f"Input exceeded the byte limit: {path}")
        return raw


def read_json(path: Path) -> Any:

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ReleaseError(f"Duplicate metadata key: {key}")
            result[key] = value
        return result

    return json.loads(read_bounded(path, MAX_METADATA_BYTES), object_pairs_hook=unique)


def write_once(path: Path, data: bytes) -> None:
    """Install complete bytes atomically, without replacing an existing file."""
    if path.exists():
        if path.is_symlink() or read_bounded(path, len(data)) != data:
            raise ReleaseError(f"Refusing to overwrite different bytes: {path}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".write-", delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
            output.close()
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.is_symlink() or read_bounded(path, len(data)) != data:
                    raise ReleaseError(f"Concurrent conflicting bytes: {path}") from None
        finally:
            temporary.unlink(missing_ok=True)


@contextlib.contextmanager
def lock(path: Path) -> Iterator[None]:
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ReleaseError(
            f"Release is locked: {path}; inspect the owning process before recovery"
        ) from None
    try:
        with os.fdopen(descriptor, "w") as output:
            output.write(f"{os.getpid()}\n")
        yield
    finally:
        path.unlink()


def source_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for directory in ("python", "js", "go", "rust", "data/bundled", "spec"):
        for path in sorted((root / directory).rglob("*")):
            relative = path.relative_to(root / directory)
            if any(part in EXCLUDED or part.endswith(".egg-info") for part in relative.parts):
                continue
            if path.suffix in {".pyc", ".tgz", ".log", ".crate", ".whl"}:
                continue
            if path.is_symlink():
                raise ReleaseError(f"Release source must not contain symlinks: {path}")
            if path.is_file():
                files.append(path)
    files.extend(root / name for name in ("LICENSE", "NOTICE"))
    if len(files) > MAX_FILES:
        raise ReleaseError("Release source exceeds the file-count limit")
    return sorted(files)


def identity(root: Path, version: str) -> dict[str, Any]:
    validate_version(version)
    with (root / "python/pyproject.toml").open("rb") as source:
        python = tomllib.load(source)["project"]
    with (root / "rust/Cargo.toml").open("rb") as source:
        rust = tomllib.load(source)["package"]
    npm = read_json(root / "js/package.json")
    npm_lock = read_json(root / "js/package-lock.json")
    versions = {
        "python": python["version"],
        "npm": npm["version"],
        "rust": rust["version"],
        "npm-lock": npm_lock["version"],
        "npm-lock-root": npm_lock["packages"][""]["version"],
        "contract": read_json(root / "spec/contract.json")["version"],
    }
    if any(value != version for value in versions.values()):
        raise ReleaseError(f"Version mismatch for {version}: {versions}")
    if any(value != "anchorpass" for value in (python["name"], npm["name"], rust["name"])):
        raise ReleaseError("Unexpected package name; review the release contract")
    if not re.search(rf"^module {re.escape(MODULE)}$", (root / "go/go.mod").read_text(), re.M):
        raise ReleaseError("Unexpected Go module path; review the release contract")
    records = [
        {"path": str(path.relative_to(root)), "sha256": sha256(path)} for path in source_files(root)
    ]
    return {
        "format": 1,
        "version": version,
        "module": MODULE,
        "source_sha256": hashlib.sha256(canonical(records)).hexdigest(),
        "tags": {
            "python": f"py-v{version}",
            "npm": f"npm-v{version}",
            "rust": f"rust-v{version}",
            "go": f"go/v{version}",
        },
    }


def run(
    command: list[str],
    cwd: Path,
    log: Path,
    timeout: int,
    env: dict[str, str],
    *,
    capture_output: bool = True,
) -> None:
    """Retain bounded-by-disk command output and reap the whole owned process group."""
    with log.open("ab") as output:
        output.write((json.dumps(command) + "\n").encode())
        output.flush()
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=output if capture_output else subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            code = process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            output.write(f"exit={process.returncode}; interrupted or timed out\n".encode())
            raise ReleaseError(f"Command interrupted or exceeded {timeout}s; see {log}") from None
        output.write(f"exit={code}\n".encode())
    if code:
        raise ReleaseError(f"Command exited {code}; see {log}")


def build_target(root: Path, target: str, version: str, stage: Path, timeout: int) -> None:
    with tempfile.TemporaryDirectory(prefix="anchorpass-release-build-") as name:
        work = Path(name)
        source_name = "js" if target == "npm" else target
        source = work / source_name
        source.mkdir()
        for path in source_files(root):
            relative = path.relative_to(root)
            if relative.parts[0] == source_name:
                destination = work / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, destination)
        environment = dict(os.environ, SOURCE_DATE_EPOCH=SOURCE_EPOCH)
        environment.pop("MAKEFLAGS", None)
        environment.pop("MFLAGS", None)
        environment.setdefault("CARGO_BUILD_JOBS", "2")
        log = stage / "build.log"
        if target == "python":
            run(
                [
                    sys.executable,
                    "-m",
                    "build",
                    "--no-isolation",
                    "--outdir",
                    str(stage),
                    str(source),
                ],
                work,
                log,
                timeout,
                environment,
            )
        elif target == "npm":
            dependencies = root / "js/node_modules"
            if not dependencies.is_dir():
                raise ReleaseError(
                    "Install the locked Node build dependencies with npm --prefix js ci"
                )
            (source / "node_modules").symlink_to(dependencies, target_is_directory=True)
            run(
                ["npm", "pack", "--json", "--pack-destination", str(stage)],
                source,
                log,
                timeout,
                environment,
            )
        elif target == "rust":
            run(
                [
                    "cargo",
                    "package",
                    "--allow-dirty",
                    "--locked",
                    "--no-verify",
                    "--target-dir",
                    str(work / "target"),
                ],
                source,
                log,
                timeout,
                environment,
            )
            shutil.copyfile(
                work / f"target/package/anchorpass-{version}.crate",
                stage / f"anchorpass-{version}.crate",
            )
        else:
            prefix = f"{MODULE}@v{version}/"
            with zipfile.ZipFile(stage / f"v{version}.zip", "w", zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(source.rglob("*")):
                    if path.is_file():
                        item = zipfile.ZipInfo(
                            prefix + str(path.relative_to(source)), (1980, 1, 1, 0, 0, 0)
                        )
                        item.compress_type = zipfile.ZIP_DEFLATED
                        item.external_attr = 0o100644 << 16
                        archive.writestr(item, path.read_bytes())
            (stage / f"v{version}.mod").write_bytes((source / "go.mod").read_bytes())
            (stage / f"v{version}.info").write_bytes(
                canonical(
                    {
                        "Version": f"v{version}",
                        "Time": "1980-01-01T00:00:00Z",
                    }
                )
            )


def archive_files(path: Path) -> dict[str, bytes]:
    """Read bounded regular archive entries, rejecting traversal and link members."""
    result: dict[str, bytes] = {}
    size = 0
    if path.suffix in {".whl", ".zip"}:
        with zipfile.ZipFile(path) as archive:
            if len(archive.infolist()) > MAX_FILES:
                raise ReleaseError("Archive exceeds the file-count limit")
            entries = [
                (
                    item.filename,
                    item.file_size,
                    item.is_dir(),
                    (item.external_attr >> 16) & 0o170000 == 0o120000,
                    item,
                )
                for item in archive.infolist()
            ]
            for name, length, directory, link, item in entries:
                size += length
                check_member(name, len(entries), size, link)
                if not directory:
                    if name in result:
                        raise ReleaseError(f"Duplicate archive member: {name}")
                    result[name] = archive.read(item)
    else:
        # Bound the entire decompressed tar, including hidden PAX/longname headers,
        # before tarfile can allocate based on those headers.
        with tempfile.TemporaryFile() as expanded, gzip.open(path, "rb") as compressed:
            expanded_size = 0
            for block in iter(lambda: compressed.read(65536), b""):
                expanded_size += len(block)
                if expanded_size > MAX_ARCHIVE_BYTES:
                    raise ReleaseError("Archive exceeds the decompressed-byte limit")
                expanded.write(block)
            expanded.seek(0)
            with tarfile.open(
                fileobj=BoundedArchiveReader(expanded, expanded_size), mode="r:"
            ) as archive:
                for count, member in enumerate(archive, 1):
                    size += member.size
                    check_member(member.name, count, size, not (member.isfile() or member.isdir()))
                    if member.isfile():
                        if member.name in result:
                            raise ReleaseError(f"Duplicate archive member: {member.name}")
                        source = archive.extractfile(member)
                        if source is None:
                            raise ReleaseError(f"Unreadable archive member: {member.name}")
                        with source:
                            result[member.name] = source.read()
    return result


class BoundedArchiveReader:
    """Reject forged tar metadata lengths before tarfile allocates their read buffer."""

    def __init__(self, source: BinaryIO, length: int) -> None:
        self.source = source
        self.length = length

    def read(self, size: int = -1) -> bytes:
        remaining = self.length - self.source.tell()
        if size < 0:
            size = remaining
        if size > remaining or size > MAX_ARCHIVE_BYTES:
            raise ReleaseError("Archive declares a read outside its bounded content")
        return self.source.read(size)

    def tell(self) -> int:
        return self.source.tell()

    def seek(self, offset: int, whence: int = 0) -> int:
        position = self.source.seek(offset, whence)
        if not 0 <= position <= self.length:
            raise ReleaseError("Archive declares a seek outside its bounded content")
        return position


def check_member(name: str, count: int, size: int, link: bool) -> None:
    if (
        PurePosixPath(name).is_absolute()
        or ".." in PurePosixPath(name).parts
        or "\\" in name
        or link
        or count > MAX_FILES
        or size > MAX_ARCHIVE_BYTES
    ):
        raise ReleaseError(f"Unsafe or oversized archive member: {name}")


def require_content(files: dict[str, bytes], paths: tuple[str, ...], label: str) -> None:
    if any(path not in files for path in paths):
        raise ReleaseError(f"{label} is missing required package content: {paths}")


def inspect_target(directory: Path, target: str, version: str) -> list[dict[str, Any]]:
    validate_version(version)
    safe_directory(directory)
    expected = {
        "python": [f"anchorpass-{version}-py3-none-any.whl", f"anchorpass-{version}.tar.gz"],
        "npm": [f"anchorpass-{version}.tgz"],
        "rust": [f"anchorpass-{version}.crate"],
        "go": [f"v{version}.zip", f"v{version}.mod", f"v{version}.info"],
    }[target]
    actual = {path.name for path in directory.iterdir() if path.name != "build.log"}
    if set(expected) != actual:
        raise ReleaseError(f"Unexpected {target} artifact set: {sorted(actual)}")
    records: list[dict[str, Any]] = []
    for filename in expected:
        path = directory / filename
        digest = sha256(path)
        if path.suffix not in {".mod", ".info"}:
            files = archive_files(path)
            if target == "python" and path.suffix == ".whl":
                prefix = "anchorpass/"
                require_content(
                    files,
                    (
                        prefix + "data/bundle.json",
                        prefix + "data/keys.json",
                        prefix + "py.typed",
                        prefix + "__init__.py",
                    ),
                    filename,
                )
                metadata = f"anchorpass-{version}.dist-info/"
                require_content(
                    files,
                    (
                        metadata + "METADATA",
                        metadata + "licenses/LICENSE",
                        metadata + "licenses/NOTICE",
                    ),
                    filename,
                )
                if f"Version: {version}\n".encode() not in files[metadata + "METADATA"]:
                    raise ReleaseError("Wheel metadata version mismatch")
            else:
                prefix = {
                    "python": f"anchorpass-{version}/",
                    "npm": "package/",
                    "rust": f"anchorpass-{version}/",
                    "go": f"{MODULE}@v{version}/",
                }[target]
                data = "src/anchorpass/data/" if target == "python" else "data/"
                require_content(
                    files,
                    tuple(
                        prefix + item
                        for item in (data + "bundle.json", data + "keys.json", "LICENSE", "NOTICE")
                    ),
                    filename,
                )
                if target == "npm":
                    require_content(
                        files,
                        tuple(
                            prefix + item
                            for item in (
                                "dist/worker.js",
                                "dist/index.js",
                                "dist/index.mjs",
                                "dist/index.d.ts",
                            )
                        ),
                        filename,
                    )
                    if json.loads(files[prefix + "package.json"])["version"] != version:
                        raise ReleaseError("npm artifact version mismatch")
                if (
                    target == "rust"
                    and tomllib.loads(files[prefix + "Cargo.toml"].decode())["package"]["version"]
                    != version
                ):
                    raise ReleaseError("Crate version mismatch")
                if (
                    target == "python"
                    and f"Version: {version}\n".encode() not in files[prefix + "PKG-INFO"]
                ):
                    raise ReleaseError("sdist metadata version mismatch")
        elif path.suffix == ".info" and read_json(path)["Version"] != f"v{version}":
            raise ReleaseError("Go info version mismatch")
        elif path.suffix == ".mod" and f"module {MODULE}\n".encode() not in path.read_bytes():
            raise ReleaseError("Go mod path mismatch")
        records.append(
            {"path": f"{target}/{filename}", "sha256": digest, "bytes": path.stat().st_size}
        )
    return records


def events(output: Path) -> list[dict[str, Any]]:
    path = output / "journal.jsonl"
    if not path.exists():
        return []
    if path.is_symlink() or path.stat().st_size > MAX_METADATA_BYTES:
        raise ReleaseError("Release journal is not bounded metadata")
    try:
        return [json.loads(line) for line in read_bounded(path, MAX_METADATA_BYTES).splitlines()]
    except (ValueError, TypeError):
        raise ReleaseError(
            "Incomplete or corrupt journal; restore the recorded journal before resuming"
        ) from None


def append_event(output: Path, event: dict[str, Any]) -> None:
    data = json.dumps(event, sort_keys=True).encode() + b"\n"
    path = output / "journal.jsonl"
    if path.is_symlink() or (
        path.exists() and path.stat().st_size + len(data) > MAX_METADATA_BYTES
    ):
        raise ReleaseError("Release journal exceeds its limit")
    descriptor = os.open(
        path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600
    )
    with os.fdopen(descriptor, "ab") as journal:
        if not stat.S_ISREG(os.fstat(journal.fileno()).st_mode):
            raise ReleaseError("Journal must be a regular file")
        journal.write(data)
        journal.flush()
        os.fsync(journal.fileno())


def checksums(records: list[dict[str, Any]]) -> bytes:
    return "".join(f"{item['sha256']}  {item['path']}\n" for item in records).encode()


def prepare(root: Path, output: Path, version: str, timeout: int) -> dict[str, Any]:
    candidate = identity(root, version)
    output.mkdir(parents=True, exist_ok=True)
    with lock(output / ".lock"):
        journal = events(output)
        if journal:
            if journal[0] != {"event": "release", **candidate}:
                raise ReleaseError(
                    "Release identity changed; select a new version/output, never overwrite"
                )
        else:
            if set(output.iterdir()) - {output / ".lock"}:
                raise ReleaseError("Unjournaled files exist in release output")
            append_event(output, {"event": "release", **candidate})
        records = []
        for target in TARGETS:
            destination = output / target
            if not destination.exists():
                with tempfile.TemporaryDirectory(prefix=f".{target}-", dir=output) as name:
                    stage = Path(name) / target
                    stage.mkdir()
                    try:
                        build_target(root, target, version, stage, timeout)
                    except BaseException:
                        if (stage / "build.log").is_file():
                            failure_log = output / f"{target}-failed.log"
                            shutil.copyfile(stage / "build.log", failure_log)
                            print(f"Build failure log: {failure_log}", file=sys.stderr)
                        raise
                    inspect_target(stage, target, version)
                    stage.rename(destination)
            current = inspect_target(destination, target, version)
            previous = [
                entry
                for entry in journal
                if entry.get("event") == "prepared" and entry.get("target") == target
            ]
            event = {"event": "prepared", "target": target, "artifacts": current}
            if previous and previous != [event]:
                raise ReleaseError(f"Prepared {target} artifact identity changed")
            if not previous:
                append_event(output, event)
            records.extend(current)
        if identity(root, version) != candidate:
            raise ReleaseError(
                "Source changed while building; keep this output for inspection and use a new output"
            )
        manifest = {**candidate, "artifacts": records}
        write_once(output / "SHA256SUMS", checksums(records))
        write_once(output / "release.json", canonical(manifest))
        verify(output)
        return manifest


def verify(output: Path) -> dict[str, Any]:
    safe_directory(output)
    manifest = read_json(output / "release.json")
    if not isinstance(manifest, dict):
        raise ReleaseError("Manifest must be an object")
    journal = events(output)
    identity_fields = {key: value for key, value in manifest.items() if key != "artifacts"}
    validate_identity(identity_fields)
    if not journal or journal[0] != {"event": "release", **identity_fields}:
        raise ReleaseError("Manifest and journal identities differ")
    records = []
    for target in TARGETS:
        current = inspect_target(output / target, target, manifest["version"])
        matching = [
            event
            for event in journal
            if event.get("event") == "prepared" and event.get("target") == target
        ]
        if matching != [{"event": "prepared", "target": target, "artifacts": current}]:
            raise ReleaseError(f"Journal does not confirm the exact {target} artifacts")
        records.extend(current)
    if records != manifest["artifacts"] or read_bounded(
        output / "SHA256SUMS", MAX_METADATA_BYTES
    ) != checksums(records):
        raise ReleaseError("Manifest/checksum mismatch")
    return manifest


def stage_release(output: Path, destination: Path) -> list[str]:
    """Copy a release into local handoff storage, recording only verified deliveries."""
    destination = destination.resolve()
    if destination == output.resolve() or output.resolve() in destination.parents:
        raise ReleaseError("Use a separate staging destination outside release output")
    destination.mkdir(parents=True, exist_ok=True)
    with lock(output / ".lock"), lock(destination / ".release.lock"):
        manifest = verify(output)
        release = destination / manifest["version"]
        safe_directory(release, create=True)
        write_once(release / "release.json", canonical(manifest))
        write_once(release / "SHA256SUMS", checksums(manifest["artifacts"]))
        completed = []
        for target in TARGETS:
            artifacts = [
                item for item in manifest["artifacts"] if item["path"].startswith(target + "/")
            ]
            safe_directory(release / target, create=True)
            for item in artifacts:
                source, delivered = output / item["path"], release / item["path"]
                write_once(delivered, source.read_bytes())
                if sha256(delivered) != item["sha256"]:
                    raise ReleaseError(f"Staged checksum differs: {delivered}")
            event = {
                "event": "staged",
                "target": target,
                "destination": str(release),
                "manifest_sha256": sha256(output / "release.json"),
                "artifacts": artifacts,
            }
            if event not in events(output):
                append_event(output, event)
            completed.append(target)
        return completed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("prepare", help="Build/resume one immutable local version")
    build.add_argument("--source", type=Path, default=ROOT)
    build.add_argument("--version", required=True)
    build.add_argument("--output", required=True, type=Path)
    build.add_argument("--timeout", type=int, default=600, help="Per build command, seconds")
    check = subparsers.add_parser(
        "verify", help="Verify exact archive content, manifest and journal"
    )
    check.add_argument("--output", required=True, type=Path)
    stage = subparsers.add_parser(
        "stage", help="Resume immutable delivery to a local handoff directory"
    )
    stage.add_argument("--output", required=True, type=Path)
    stage.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            if not 1 <= args.timeout <= 3600:
                raise ReleaseError("Build timeout must be between 1 and 3600 seconds")
            result = prepare(
                args.source.resolve(), args.output.resolve(), args.version, args.timeout
            )
        elif args.command == "verify":
            result = verify(args.output.resolve())
        else:
            result = {"staged": stage_release(args.output.resolve(), args.destination)}
        print(json.dumps(result, sort_keys=True))
        return 0
    except (
        ReleaseError,
        OSError,
        ValueError,
        KeyError,
        tarfile.TarError,
        zipfile.BadZipFile,
    ) as error:
        print(f"Release failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
