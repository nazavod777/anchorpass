#!/usr/bin/env python3
"""Build a local Go module archive and verify its installed public API offline."""

from __future__ import annotations

import contextlib
import datetime
import json
import os
import signal
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from go_package_proxy import CACHE_PREREQUISITE, prepare_consumer

MODULE = "github.com/nazavod777/anchorpass/go"
VERSION = "v0.1.1"
CONSUMER_TEST = (
    Path(__file__).resolve().parents[1] / "tests/integration/go_consumer/consumer_test.go"
).read_text()


def run(command: list[str], cwd: Path, env: dict[str, str]) -> None:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        output, _ = process.communicate(timeout=120)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        output, _ = process.communicate()
        print(output, end="")
        if isinstance(error, KeyboardInterrupt):
            raise
        raise SystemExit("Go package check timed out") from None
    print(output, end="")
    if process.returncode:
        raise SystemExit(process.returncode)


def main() -> None:
    source = Path(sys.argv[1]).resolve()
    if not (source / "go.mod").is_file():
        raise SystemExit("expected the Go module directory as the first argument")
    with tempfile.TemporaryDirectory(prefix="anchorpass-go-package-") as name:
        root = Path(name)
        artifacts = root / "artifacts"
        artifacts.mkdir()
        paths = []
        for path in source.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(source)
            if any(
                (source / parent / "go.mod").is_file()
                for parent in relative.parents
                if str(parent) != "."
            ):
                continue
            paths.append(path)
        with zipfile.ZipFile(artifacts / f"{VERSION}.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            for path in paths:
                archive.write(path, f"{MODULE}@{VERSION}/{path.relative_to(source)}")
        (artifacts / f"{VERSION}.mod").write_bytes((source / "go.mod").read_bytes())
        (artifacts / f"{VERSION}.info").write_text(
            json.dumps(
                {
                    "Version": VERSION,
                    "Time": datetime.datetime.now(datetime.UTC).isoformat(),
                }
            )
        )
        consumer = root / "consumer"
        consumer.mkdir()
        (consumer / "anchorpass_test.go").write_text(CONSUMER_TEST)
        env = prepare_consumer(
            artifacts,
            consumer,
            root / "proxy",
            root / "modcache",
            module=MODULE,
            version=VERSION,
        )
        try:
            run(["go", "mod", "tidy"], consumer, env)
        except SystemExit:
            print(CACHE_PREREQUISITE, file=sys.stderr)
            raise
        run(["go", "test", "-v", "./..."], consumer, env)
        print(f"Go archive package smoke passed; archive files={len(paths)}")


if __name__ == "__main__":
    main()
