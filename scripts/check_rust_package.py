"""Verify the packaged Rust crate outside the repository."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import tarfile
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], cwd: Path) -> None:
    environment = {
        key: value for key, value in os.environ.items() if key not in {"MAKEFLAGS", "MFLAGS"}
    }
    environment.setdefault("CARGO_BUILD_JOBS", "2")
    process = subprocess.Popen(command, cwd=cwd, env=environment, start_new_session=True)
    try:
        code = process.wait(timeout=600)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        if isinstance(error, KeyboardInterrupt):
            raise
        raise SystemExit("Rust package check timed out") from None
    if code:
        raise SystemExit(code)


def main() -> None:
    source = ROOT / "rust"
    for features in ("async", "blocking"):
        run(
            [
                "cargo",
                "package",
                "--allow-dirty",
                "--locked",
                "--no-default-features",
                "--features",
                features,
            ],
            source,
        )
    with (source / "Cargo.toml").open("rb") as manifest_file:
        manifest = tomllib.load(manifest_file)["package"]
    artifact = source / "target/package" / f"{manifest['name']}-{manifest['version']}.crate"
    with tempfile.TemporaryDirectory(prefix="anchorpass-rust-package-") as name:
        destination = Path(name)
        with tarfile.open(artifact) as archive:
            archive.extractall(destination, filter="data")
        (package,) = destination.iterdir()
        for path in ("data/bundle.json", "data/keys.json", "LICENSE", "NOTICE"):
            assert (package / path).is_file(), path
        run(
            [
                "cargo",
                "test",
                "--locked",
                "--all-features",
                "--target-dir",
                str(source / "target"),
            ],
            package,
        )
    print("PASS: standalone Rust crate, async/blocking packaging and extracted tests")


if __name__ == "__main__":
    main()
