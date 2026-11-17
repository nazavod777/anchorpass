#!/usr/bin/env python3
"""Install and exercise the exact sealed artifacts outside the source checkout."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tarfile
import tempfile
from pathlib import Path

import release
from check_go_package import CONSUMER_TEST
from go_package_proxy import CACHE_PREREQUISITE, prepare_consumer


def check_go(output: Path, work: Path, version: str, timeout: int, environment: dict[str, str]):
    consumer = work / "go-consumer"
    consumer.mkdir()
    (consumer / "consumer_test.go").write_text(CONSUMER_TEST)
    go_environment = prepare_consumer(
        output / "go",
        consumer,
        work / "proxy",
        work / "go-modcache",
        module=release.MODULE,
        version=f"v{version}",
        environment=environment,
    )
    try:
        release.run(
            ["go", "mod", "tidy"], consumer, work / "go-install.log", timeout, go_environment
        )
    except release.ReleaseError:
        print(CACHE_PREREQUISITE, file=sys.stderr)
        raise
    release.run(
        ["go", "test", "-v", "./..."], consumer, work / "go-api.log", timeout, go_environment
    )


def check(output: Path, work: Path, timeout: int) -> None:
    if work.resolve().is_relative_to(release.ROOT.resolve()):
        raise release.ReleaseError("Installed consumers must be outside the source checkout")
    manifest = release.verify(output)
    version = manifest["version"]
    environment = dict(os.environ, PYTHONNOUSERSITE="1")
    for variable in ("PYTHONPATH", "PYTHONHOME", "MAKEFLAGS", "MFLAGS"):
        environment.pop(variable, None)
    environment.setdefault("CARGO_BUILD_JOBS", "2")

    def run(command: list[str], cwd: Path, name: str, env: dict[str, str] | None = None) -> None:
        release.run(command, cwd, work / f"{name}.log", timeout, env or environment)

    python_work = work / "python"
    python_work.mkdir()
    run(
        [sys.executable, "-m", "venv", str(python_work / "environment")], python_work, "python-venv"
    )
    python = str(python_work / "environment/bin/python")
    for name, artifact in (
        ("wheel", f"anchorpass-{version}-py3-none-any.whl"),
        ("sdist", f"anchorpass-{version}.tar.gz"),
    ):
        command = [python, "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir"]
        if name == "sdist":
            command.extend(["--force-reinstall", "--no-deps"])
        run([*command, str(output / "python" / artifact)], python_work, f"python-install-{name}")
        run(
            [
                "node",
                str(release.ROOT / "scripts/python_package_fixture.cjs"),
                python,
                str(python_work),
            ],
            python_work,
            f"python-api-{name}",
        )

    run(
        [
            "node",
            str(release.ROOT / "js/scripts/package-smoke.cjs"),
            str(output / f"npm/anchorpass-{version}.tgz"),
        ],
        work,
        "npm-api",
    )

    check_go(output, work, version, timeout, environment)

    rust_work = work / "rust"
    rust_work.mkdir()
    with tarfile.open(output / f"rust/anchorpass-{version}.crate") as archive:
        archive.extractall(rust_work, filter="data")
    package = rust_work / f"anchorpass-{version}"
    run(
        [
            "cargo",
            "test",
            "--locked",
            "--all-features",
            "--target-dir",
            str(release.ROOT / "rust/target"),
        ],
        package,
        "rust-api",
    )
    (package / "Cargo.toml").write_bytes((package / "Cargo.toml.orig").read_bytes())
    repack_environment = dict(
        environment,
        SOURCE_DATE_EPOCH=release.SOURCE_EPOCH,
        CARGO_HOME=str(work / "cargo-home"),
        CARGO_TARGET_DIR=str(package / "target"),
    )
    run(
        ["cargo", "package", "--locked", "--allow-dirty", "--no-verify"],
        package,
        "rust-repack",
        repack_environment,
    )
    repacked = package / f"target/package/anchorpass-{version}.crate"
    original = output / f"rust/anchorpass-{version}.crate"
    if release.sha256(repacked) != release.sha256(original):
        raise release.ReleaseError("Cargo repack differs from sealed artifact")
    run(
        [
            "cargo",
            "publish",
            "--dry-run",
            "--locked",
            "--allow-dirty",
            "--no-verify",
            "--registry",
            "crates-io",
        ],
        package,
        "rust-publication-dry-run",
        repack_environment,
    )
    release.verify(output)
    (work / "result.json").write_bytes(
        release.canonical(
            {
                "status": "passed",
                "manifest_sha256": release.sha256(output / "release.json"),
                "targets": list(release.TARGETS),
                "rust_repack_sha256": release.sha256(repacked),
            }
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--work-dir", type=Path, help="New owned directory for installed consumers and logs"
    )
    parser.add_argument("--timeout", type=int, default=600, help="Per command, seconds")
    args = parser.parse_args()
    try:
        if not 1 <= args.timeout <= 3600:
            raise release.ReleaseError("Check timeout must be between 1 and 3600 seconds")
        if args.work_dir is None:
            work = Path(tempfile.mkdtemp(prefix="anchorpass-sealed-check-"))
        else:
            work = args.work_dir.resolve()
            work.mkdir(parents=True, exist_ok=False)
        check(args.output.resolve(), work, args.timeout)
        print(json.dumps({"status": "passed", "work_dir": str(work)}))
        return 0
    except (release.ReleaseError, OSError, ValueError, KeyError, tarfile.TarError) as error:
        print(f"Sealed artifact check failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
