"""Explicit, sequential live checks against the public demos in tests/live/sites.json."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import selectors
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from anchorpass import AnchorpassError, Client
from check_demo import DemoError
from curl_cffi import CurlOpt
from curl_cffi.requests import Session
from live_sites import INVALID_TOKEN, ROOT, SITES, assess, discover, object_json, verify

MODES = [
    "python-sync",
    "python-async",
    "node-async",
    "node-sync",
    "go",
    "rust-async",
    "rust-blocking",
]
MAX_HELPER_BYTES = 65536


def helper_command(mode: str) -> list[str]:
    if mode.startswith("python-"):
        return [sys.executable, str(ROOT / "tests/live/python.py")]
    if mode.startswith("node-"):
        return ["node", str(ROOT / "tests/live/node.cjs")]
    if mode == "go":
        return [str(ROOT / "artifacts/live/go")]
    return [str(ROOT / "tests/live/rust/target/debug/anchorpass-live-check")]


def run_helper(command: list[str], payload: dict, timeout: float) -> dict:
    encoded = json.dumps(payload).encode()
    if len(encoded) > 8192 or timeout <= 0:
        raise DemoError("HelperInvalidInput")
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    deadline = time.monotonic() + timeout
    output = bytearray()
    total = 0
    try:
        assert (
            process.stdin is not None and process.stdout is not None and process.stderr is not None
        )
        process.stdin.write(encoded)
        process.stdin.close()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, True)
            selector.register(process.stderr, selectors.EVENT_READ, False)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DemoError("HelperDeadlineExceeded")
                ready = selector.select(remaining)
                if not ready:
                    raise DemoError("HelperDeadlineExceeded")
                for key, _ in ready:
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(chunk)
                    if total > MAX_HELPER_BYTES:
                        raise DemoError("HelperResponseLimit")
                    if key.data:
                        output.extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DemoError("HelperDeadlineExceeded")
        code = process.wait(timeout=remaining)
        try:
            result = object_json(output.decode("utf-8"))
        except (UnicodeError, DemoError):
            raise DemoError("HelperInvalidResponse") from None
        if code != 0:
            error = result.get("error")
            raise DemoError(
                error
                if isinstance(error, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", error)
                else "HelperFailed"
            )
        token = result.get("token")
        if not isinstance(token, str) or not token or len(token.encode()) > 32768:
            raise DemoError("HelperInvalidResponse")
        revision = result.get("bundle_revision")
        if not isinstance(revision, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", revision):
            raise DemoError("HelperInvalidResponse")
        return {"token": token, "bundle_revision": revision}
    except subprocess.TimeoutExpired:
        raise DemoError("HelperDeadlineExceeded") from None
    finally:
        # An exited leader can leave descendants holding its output pipes open.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def obtain(mode: str, anchor: str, proxy: str | None, timeout: float, action: str) -> dict:
    return run_helper(
        helper_command(mode),
        {
            "mode": mode,
            "anchor": anchor,
            "action": action,
            "proxy": proxy,
            "timeout_ms": max(1, int(timeout * 1000)),
        },
        timeout,
    )


def safe_error(error: Exception) -> str:
    if isinstance(error, AnchorpassError):
        return error.code
    if isinstance(error, DemoError) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", str(error)):
        return str(error)
    return type(error).__name__


def source_hashes() -> dict[str, str]:
    paths = (
        subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=ROOT,
            timeout=5,
        )
        .decode()
        .split("\0")
    )
    prefixes = ("python/", "js/", "go/", "rust/", "scripts/", "tests/live/")
    return {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in sorted(set(paths))
        if name and name.startswith(prefixes)
    }


def helper_hashes(modes: list[str]) -> dict[str, str]:
    paths = {Path(helper_command(mode)[-1]) for mode in modes if not mode.startswith("python-")}
    if any(mode.startswith("node-") for mode in modes):
        paths.update((ROOT / "js/dist").glob("*.js"))
        paths.update((ROOT / "js/dist").glob("*.mjs"))
    return {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(paths)
        if path.is_file()
    }


def check_site(
    name: str, modes: list[str], proxy: str | None, timeout: float, interval: float
) -> tuple[dict, list[dict]]:
    site = SITES[name]
    control = {"site": name, "invalid_token_rejected": False}
    results = []
    with Session(
        trust_env=False, curl_options={CurlOpt.PROXY: proxy or "", CurlOpt.NOPROXY: ""}
    ) as session:
        try:
            discovery = discover(session, site, time.monotonic() + timeout)
            control["source_sha256"] = discovery["source_sha256"]
            invalid = verify(
                session, site, discovery["site_key"], INVALID_TOKEN, time.monotonic() + timeout
            )
            control["invalid_token_rejected"] = invalid.get("success") is False
            if not control["invalid_token_rejected"]:
                raise DemoError("InvalidTokenControlAccepted")
        except (DemoError, ValueError, OSError) as error:
            control["error"] = safe_error(error)
            return control, [
                {
                    "site": name,
                    "mode": mode,
                    "status": "unavailable",
                    "server_accepted": False,
                    "error": safe_error(error),
                }
                for mode in modes
            ]
        for mode in modes:
            # Pacing is intentional; this is a sparse acceptance suite, not a load test.
            time.sleep(interval)
            started = time.monotonic()
            deadline = started + timeout
            result = {
                "site": name,
                "mode": mode,
                "proxy_configured": proxy is not None,
                "protocol_variant": site["variant"],
            }
            try:
                token = obtain(
                    mode, discovery["anchor"], proxy, deadline - time.monotonic(), site["action"]
                )
                value = verify(session, site, discovery["site_key"], token["token"], deadline)
                result.update(assess(site, value))
                result["bundle_revision"] = token["bundle_revision"]
            except (DemoError, AnchorpassError, ValueError, OSError) as error:
                result.update(status="error", server_accepted=False, error=safe_error(error))
            result["elapsed_seconds"] = time.monotonic() - started
            results.append(result)
            print(json.dumps(result), flush=True)
    return control, results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sites", nargs="+", choices=list(SITES), default=list(SITES))
    parser.add_argument("--modes", nargs="+", choices=MODES, default=MODES)
    parser.add_argument("--proxy")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--interval", type=float, default=1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for label, value, minimum, maximum in [
        ("timeout", args.timeout, 1, 60),
        ("interval", args.interval, 1, 60),
    ]:
        if not math.isfinite(value) or not minimum <= value <= maximum:
            parser.error(f"{label} must be between {minimum} and {maximum} seconds")
    if len(set(args.modes)) != len(args.modes) or len(set(args.sites)) != len(args.sites):
        parser.error("duplicate sites or modes are not allowed")
    try:
        with Client(proxy=args.proxy, timeout=args.timeout):
            pass  # Validate routing before discovery or any child process.
    except AnchorpassError as error:
        print(json.dumps({"error": error.code}))
        return 1
    hashes = source_hashes()
    executables = helper_hashes(args.modes)
    report = {
        "schema_version": 2,
        "recorded_at": datetime.now(UTC).isoformat(),
        "source_sha256": hashes,
        "helper_sha256": executables,
        "controls": [],
        "results": [],
        "sites": {name: SITES[name] for name in args.sites},
    }
    try:
        for name in args.sites:
            control, results = check_site(name, args.modes, args.proxy, args.timeout, args.interval)
            report["controls"].append(control)
            report["results"].extend(results)
    except KeyboardInterrupt:
        report["interrupted"] = True
    report["source_unchanged"] = hashes == source_hashes()
    report["helpers_unchanged"] = executables == helper_hashes(args.modes)
    report["summary"] = {
        status: sum(row["status"] == status for row in report["results"])
        for status in sorted({row["status"] for row in report["results"]})
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    # A hidden Enterprise action is a limited observation, not a full conformance pass.
    passed = (
        report["source_unchanged"]
        and report["helpers_unchanged"]
        and not report.get("interrupted")
        and len(report["results"]) == len(args.sites) * len(args.modes)
        and all(row["status"] == "accepted" for row in report["results"])
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
