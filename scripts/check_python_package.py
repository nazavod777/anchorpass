"""Build and install Python distribution artifacts outside the checkout."""

from __future__ import annotations

import argparse
import contextlib
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], cwd: Path, timeout: float = 180) -> None:
    process = subprocess.Popen(command, cwd=cwd, start_new_session=True)
    try:
        code = process.wait(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        if isinstance(error, KeyboardInterrupt):
            raise
        raise SystemExit(f"Package check exceeded {timeout} seconds") from None
    if code:
        raise SystemExit(code)


def check(work: Path) -> None:
    artifacts = work / "artifacts"
    run([sys.executable, "-m", "build", "--outdir", str(artifacts), str(ROOT / "python")], work)
    run([sys.executable, "-m", "venv", str(work / "environment")], work)
    python = str(work / "environment/bin/python")
    (wheel,) = artifacts.glob("*.whl")
    (source,) = artifacts.glob("*.tar.gz")
    run([python, "-m", "pip", "install", "--disable-pip-version-check", str(wheel)], work)
    fixture = str(ROOT / "scripts/python_package_fixture.cjs")
    run(["node", fixture, python, str(work)], work, timeout=60)
    run(
        [
            python,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--force-reinstall",
            "--no-deps",
            "--no-cache-dir",
            str(source),
        ],
        work,
    )
    run(["node", fixture, python, str(work)], work, timeout=60)
    print(f"PASS: standalone wheel and sdist; work directory: {work}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, help="Keep artifacts in a new owned directory")
    args = parser.parse_args()
    if args.work_dir is None:
        with tempfile.TemporaryDirectory(prefix="anchorpass-python-package-") as name:
            check(Path(name))
    else:
        args.work_dir.mkdir(parents=True, exist_ok=False)
        check(args.work_dir.resolve())


if __name__ == "__main__":
    main()
