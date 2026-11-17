"""Run isolated local matrix jobs sequentially, retaining raw evidence and statuses."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import platform
import resource
import selectors
import signal
import subprocess
import sys
import threading
import time
import weakref
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_CANDIDATES = [
    "python-requests",
    "python-httpx-sync",
    "python-curl-sync",
    "python-aiohttp",
    "python-httpx-async",
    "python-curl-async",
    "python-urllib3",
    "python-pycurl",
]
NODE_CANDIDATES = [
    "node-fetch",
    "node-undici",
    "node-axios",
    "node-https",
    "node-axios-http",
    "node-got",
]
GO_CANDIDATES = ["go-net-http", "go-resty", "go-fasthttp"]
RUST_CANDIDATES = ["rust-reqwest-async", "rust-reqwest-blocking", "rust-isahc", "rust-ureq"]
API_CANDIDATES = [
    "python-api-sync",
    "python-api-async",
    "python-api-aiohttp",
    "node-api-async",
    "node-api-sync",
    "go-api",
    "rust-api-async",
    "rust-api-blocking",
]
RAW_CANDIDATES = PYTHON_CANDIDATES + NODE_CANDIDATES + GO_CANDIDATES + RUST_CANDIDATES
SAMPLE_INTERVAL_SECONDS = 0.01
JOB_TIMEOUT_SECONDS = 60
MAX_PROTOCOL_LINE_BYTES = 1048576
_PROTOCOL_BUFFERS = weakref.WeakKeyDictionary()


def terminate(process):
    # The dedicated group may still contain a child after its leader exits.
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    if process.poll() is None:
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)


def read_line(process, timeout=10):
    deadline = time.monotonic() + timeout
    buffer = _PROTOCOL_BUFFERS.setdefault(process, bytearray())
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while True:
            end = buffer.find(b"\n")
            if end >= 0:
                if end > MAX_PROTOCOL_LINE_BYTES:
                    raise ValueError("fixture response line exceeds its byte limit")
                line = bytes(buffer[:end])
                del buffer[: end + 1]
                return json.loads(line.decode("utf-8"))
            if len(buffer) > MAX_PROTOCOL_LINE_BYTES:
                raise ValueError("fixture response line exceeds its byte limit")
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise TimeoutError("fixture response deadline exceeded")
            chunk = os.read(
                process.stdout.fileno(), min(65536, MAX_PROTOCOL_LINE_BYTES + 1 - len(buffer))
            )
            if not chunk:
                raise RuntimeError(
                    f"fixture exited before a complete response (exit={process.poll()})"
                )
            buffer.extend(chunk)


class LocalFixture:
    def __init__(self, directory):
        directory.mkdir()
        self.log = (directory / "stderr.log").open("w")
        self.process = subprocess.Popen(
            [sys.executable, str(ROOT / "benchmarks/fixture.py"), "--directory", str(directory)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.log,
            text=True,
            start_new_session=True,
        )
        try:
            ready = read_line(self.process)
            if ready.get("ready") is not True:
                raise RuntimeError("invalid fixture readiness")
            self.metadata = ready["fixture"]
        except BaseException:
            terminate(self.process)
            self.log.close()
            raise

    def command(self, command):
        self.process.stdin.write(json.dumps({"command": command}) + "\n")
        self.process.stdin.flush()
        return read_line(self.process)

    def close(self):
        try:
            if self.process.poll() is None:
                self.process.stdin.write('{"command":"stop"}\n')
                self.process.stdin.flush()
            code = self.process.wait(timeout=5)
            if code != 0:
                raise RuntimeError(f"fixture exited unsuccessfully: {code}")
        finally:
            terminate(self.process)
            self.process.stdin.close()
            self.process.stdout.close()
            self.log.close()


def read_process(pid):
    try:
        status = {}
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            name, _, value = line.partition(":")
            if name in {"VmRSS", "Threads"}:
                status[name] = int(value.split()[0])
        return {
            "rss_bytes": status.get("VmRSS", 0) * 1024,
            "threads": status.get("Threads", 0),
            "fds": len(list(Path(f"/proc/{pid}/fd").iterdir())),
        }
    except (OSError, ValueError):
        return None


def monitored(command, directory, prefix, timeout=JOB_TIMEOUT_SECONDS, cwd=ROOT):
    started = time.time_ns()
    with (
        (directory / f"{prefix}.stdout.log").open("w") as stdout,
        (directory / f"{prefix}.stderr.log").open("w") as stderr,
    ):
        process = subprocess.Popen(
            command, cwd=cwd, stdout=stdout, stderr=stderr, start_new_session=True
        )
        stopped = threading.Event()
        observations = []

        def monitor():
            while not stopped.is_set():
                snapshot = read_process(process.pid)
                if snapshot:
                    observations.append(snapshot)
                stopped.wait(SAMPLE_INTERVAL_SECONDS)

        monitor_thread = threading.Thread(target=monitor, daemon=True)
        monitor_thread.start()
        timed_out = False
        try:
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                terminate(process)
                code = process.returncode
        finally:
            terminate(process)
            stopped.set()
            monitor_thread.join(timeout=2)
        ended = time.time_ns()
    return {
        "command": command,
        "exit_code": code,
        "timed_out": timed_out,
        "started_at_ns": started,
        "ended_at_ns": ended,
        "resources": {
            "sampling_interval_ns": int(SAMPLE_INTERVAL_SECONDS * 1000000000),
            "scope": "process lifetime, including startup/warmup/recovery/cleanup; sampled peaks may miss brief spikes",
            "observations": len(observations),
            "rss_peak_bytes": max((row["rss_bytes"] for row in observations), default=None),
            "threads_peak": max((row["threads"] for row in observations), default=None),
            "fds_peak": max((row["fds"] for row in observations), default=None),
        },
    }


def environment():
    def read(path):
        try:
            return Path(path).read_text().strip()
        except OSError:
            return None

    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "affinity_cpus": sorted(os.sched_getaffinity(0)),
        "cpu_model": next(
            (
                line.split(":", 1)[1].strip()
                for line in (read("/proc/cpuinfo") or "").splitlines()
                if line.startswith("model name")
            ),
            None,
        ),
        "cpu_max": read("/sys/fs/cgroup/cpu.max"),
        "memory_max": read("/sys/fs/cgroup/memory.max"),
        "nofile": list(resource.getrlimit(resource.RLIMIT_NOFILE)),
        "fixture_connections_limit": 512,
        "fixture_io_timeout_seconds": 5,
        "dependency_hashes": {
            relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            for relative in [
                "requirements-dev.lock",
                "python/pyproject.toml",
                "js/package.json",
                "js/package-lock.json",
                "go/go.mod",
                "go/go.sum",
                "go/benchmarks/go.mod",
                "go/benchmarks/go.sum",
                "rust/Cargo.toml",
                "rust/Cargo.lock",
                "rust/benchmarks/Cargo.toml",
                "rust/benchmarks/Cargo.lock",
                "rust/benchmarks/api/Cargo.toml",
                "rust/benchmarks/api/Cargo.lock",
            ]
        },
        "source_hashes": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for folder in [
                ROOT / "benchmarks",
                ROOT / "js/src",
                ROOT / "js/benchmarks",
                ROOT / "go",
                ROOT / "rust/src",
                ROOT / "rust/benchmarks",
                ROOT / "python/src",
            ]
            for path in sorted(folder.rglob("*"))
            if path.is_file()
            and path.suffix in {".py", ".cjs", ".ts", ".go", ".rs", ".toml", ".json"}
            and not any(
                part in {"target", "node_modules", "__pycache__", "reports"} for part in path.parts
            )
        },
    }


def adapter(candidate, directory):
    if candidate.startswith("python-"):
        return [sys.executable, str(ROOT / "benchmarks/python_matrix.py")]
    if candidate.startswith("node-"):
        return ["node", str(ROOT / "js/benchmarks/matrix.cjs")]
    if candidate.startswith("go-"):
        return [str(directory / "go-matrix")]
    api = "api" in candidate
    return [
        str(
            ROOT
            / "rust/benchmarks/target/release"
            / ("transport-matrix-api" if api else "transport-matrix")
        )
    ]


def build_adapters(candidates, directory):
    commands = []
    if any(name.startswith("node-") for name in candidates):
        commands.append((ROOT, ["npm", "--prefix", "js", "run", "build"]))
    if any(name.startswith("go-") for name in candidates):
        commands.append(
            (
                ROOT / "go/benchmarks",
                ["go", "build", "-o", str(directory / "go-matrix"), "./matrix"],
            )
        )
    if any(name.startswith("rust-") and "api" not in name for name in candidates):
        commands.append(
            (
                ROOT,
                [
                    "cargo",
                    "build",
                    "--locked",
                    "--release",
                    "--manifest-path",
                    "rust/benchmarks/Cargo.toml",
                    "--bin",
                    "transport-matrix",
                ],
            )
        )
    if any(name.startswith("rust-api-") for name in candidates):
        commands.append(
            (
                ROOT,
                [
                    "cargo",
                    "build",
                    "--locked",
                    "--release",
                    "--manifest-path",
                    "rust/benchmarks/api/Cargo.toml",
                    "--target-dir",
                    "rust/benchmarks/target",
                ],
            )
        )
    results = []
    for index, (cwd, command) in enumerate(commands):
        result = monitored(command, directory, f"build-{index}", timeout=600, cwd=cwd)
        results.append(result)
        if result["exit_code"] != 0:
            raise RuntimeError(f"adapter build failed: {command}; logs in {directory}")
    return results


def jobs(manifest, candidates, smoke=False, selected_profiles=None):
    defaults = manifest["defaults"]
    # Qualify all candidates before performance; rotate order across rounds.
    for index, patch in enumerate(manifest["capabilities"]):
        profile = defaults | manifest["capability_defaults"] | patch
        if selected_profiles and profile["name"] not in selected_profiles:
            continue
        for candidate in RAW_CANDIDATES[index:] + RAW_CANDIDATES[:index]:
            if candidate in candidates:
                yield (
                    candidate,
                    0,
                    {
                        **profile,
                        **(
                            {"requests": 2, "warmup": 0, "recovery_requests": 2, "concurrency": 1}
                            if smoke
                            else {}
                        ),
                    },
                )
    for group, names in [
        ("performance", RAW_CANDIDATES),
        ("flow", RAW_CANDIDATES),
        ("api", API_CANDIDATES),
    ]:
        for round_index in range(1 if smoke else manifest["rounds"]):
            for index, patch in enumerate(manifest.get(group, [])):
                profile = defaults | patch
                if selected_profiles and profile["name"] not in selected_profiles:
                    continue
                offset = (round_index + index) % len(names)
                for candidate in names[offset:] + names[:offset]:
                    if candidate in candidates:
                        yield (
                            candidate,
                            round_index,
                            {
                                **profile,
                                **({"requests": 2, "warmup": 1, "concurrency": 1} if smoke else {}),
                            },
                        )


def validate(job, data, metrics):
    problems = []
    p = job["profile"]
    if data.get("run_id") != job["run_id"] or data.get("candidate") != job["candidate"]:
        return ["result identity mismatch"]
    if data.get("schema_version") != 1 or data.get("status") not in {"ok", "unsupported"}:
        return ["invalid result schema/status"]
    if any(
        metrics.get(key, 0)
        for key in (
            "credential_leaks",
            "blocked_external_targets",
            "protocol_errors",
            "shed_connections",
        )
    ):
        problems.append("fixture recorded a routing, protocol, credential or capacity violation")
    if data.get("status") == "unsupported":
        if not data.get("reason") or data.get("samples") or data.get("recovery"):
            problems.append("invalid unsupported result")
        return problems
    for key, count in [("samples", p["requests"]), ("recovery", p["recovery_requests"])]:
        values = data.get(key) or []
        if len(values) != count:
            problems.append(f"{key} count mismatch: {len(values)} != {count}")
        for item in values:
            expected = None if key == "recovery" else p["expected_error"]
            observed = item.get("error")
            should_pass = observed == expected
            if expected == "deadline" and item["elapsed_ns"] > (p["timeout_ms"] + 100) * 1000000:
                should_pass = False
            if item.get("ok") != should_pass:
                problems.append("adapter verdict contradicts observed error/deadline")
                break
            if should_pass and expected is None and p["kind"] in {"transport", "flow"}:
                if item.get("bytes") != (1024 if key == "recovery" else p["expected_bytes"]):
                    problems.append("successful transport byte count mismatch")
    if data.get("warmup_completed") != p["warmup"]:
        problems.append("warmup count mismatch")
    route = p["route"]
    if route == "socks5" and (
        metrics.get("socks_atyp:3", 0)
        or not (metrics.get("socks_atyp:1", 0) + metrics.get("socks_atyp:4", 0))
    ):
        problems.append("SOCKS local DNS address type mismatch")
    if route == "socks5h" and (
        not metrics.get("socks_atyp:3", 0)
        or metrics.get("socks_atyp:1", 0) + metrics.get("socks_atyp:4", 0)
    ):
        problems.append("SOCKS remote DNS address type mismatch")
    if route == "bad_auth" and metrics.get("requests", 0) != p["recovery_requests"]:
        problems.append("invalid proxy reached target, or recovery did not reach it")
    if p["kind"] == "transport" and route not in {"bad_auth", "bad_tls", "bad_target"}:
        expected_requests = p["warmup"] + p["requests"] + p["recovery_requests"]
        if metrics.get("requests", 0) != expected_requests:
            problems.append(
                f"unexpected target request count: {metrics.get('requests', 0)} != {expected_requests}"
            )
    if p["kind"] in {"api", "flow"}:
        expected = p["warmup"] + p["requests"]
        for suffix in ("api.js", "api2/anchor", "api2/reload"):
            if metrics.get(f"path:/recaptcha/{suffix}", 0) != expected:
                problems.append(f"unexpected API step count: {suffix}")
        if p["kind"] == "flow" and metrics.get("requests", 0) != expected * 3:
            problems.append("unexpected flow request count")
    return sorted(set(problems))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--candidates", nargs="+", choices=RAW_CANDIDATES + API_CANDIDATES)
    parser.add_argument("--profiles", nargs="+")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    directory = args.output.resolve()
    candidates = args.candidates or RAW_CANDIDATES + API_CANDIDATES
    manifest = json.loads((ROOT / "benchmarks/profiles.json").read_text())
    known_profiles = {
        profile["name"]
        for group in ("capabilities", "performance", "flow", "api")
        for profile in manifest.get(group, [])
    }
    unknown_profiles = set(args.profiles or []) - known_profiles
    if unknown_profiles:
        parser.error(f"unknown profiles: {', '.join(sorted(unknown_profiles))}")
    planned = list(jobs(manifest, candidates, args.smoke, args.profiles))
    if not planned:
        parser.error("candidate/profile selection produces no jobs")
    directory.mkdir(parents=True, exist_ok=False)
    record = {
        "schema_version": 1,
        "smoke": args.smoke,
        "manifest": manifest,
        "environment": environment(),
        "builds": build_adapters(candidates, directory),
        "planned_jobs": len(planned),
        "jobs": [],
    }
    (directory / "plan.json").write_text(
        json.dumps({k: v for k, v in record.items() if k != "jobs"}, indent=2) + "\n"
    )
    fixture = LocalFixture(directory / "fixture")
    try:
        for index, (candidate, round_index, profile) in enumerate(planned):
            name = f"{index:04d}-{candidate}-{profile['name']}-r{round_index}"
            job_directory = directory / name
            job_directory.mkdir()
            job = {
                "schema_version": 1,
                "run_id": name,
                "candidate": candidate,
                "round": round_index,
                "fixture": fixture.metadata,
                "profile": profile,
            }
            path = job_directory / "job.json"
            path.write_text(json.dumps(job, indent=2) + "\n")
            fixture.command("reset")
            execution = monitored(
                adapter(candidate, directory)
                + ["--job", str(path), "--output", str(job_directory / "result.json")],
                job_directory,
                "adapter",
            )
            metrics = fixture.command("state")
            data = (
                json.loads((job_directory / "result.json").read_text())
                if execution["exit_code"] == 0
                else None
            )
            problems = validate(job, data, metrics) if data else ["adapter process failed"]
            row = {
                "job": job,
                "execution": execution,
                "fixture": metrics,
                "result": data,
                "validation_errors": problems,
            }
            (job_directory / "record.json").write_text(json.dumps(row, indent=2) + "\n")
            record["jobs"].append(row)
            failed = sum(
                not item["ok"]
                for item in ((data or {}).get("samples") or [])
                + ((data or {}).get("recovery") or [])
            )
            print(
                json.dumps(
                    {
                        "completed": index + 1,
                        "total": len(planned),
                        "candidate": candidate,
                        "profile": profile["name"],
                        "status": (data or {}).get("status", "harness_error"),
                        "failed_observations": failed,
                        "validation_errors": problems,
                    }
                ),
                flush=True,
            )
    finally:
        fixture.close()
        (directory / "raw.json").write_text(json.dumps(record, indent=2) + "\n")
    final_environment = environment()
    record["sources_unchanged"] = (
        final_environment["source_hashes"] == record["environment"]["source_hashes"]
        and final_environment["dependency_hashes"] == record["environment"]["dependency_hashes"]
    )
    (directory / "raw.json").write_text(json.dumps(record, indent=2) + "\n")
    exit_code = int(
        not record["sources_unchanged"]
        or len(record["jobs"]) != record["planned_jobs"]
        or any(row["validation_errors"] for row in record["jobs"])
    )
    (directory / "exit-record.json").write_text(
        json.dumps(
            {
                "exit_code": exit_code,
                "completed_jobs": len(record["jobs"]),
                "planned_jobs": record["planned_jobs"],
                "sources_unchanged": record["sources_unchanged"],
            },
            indent=2,
        )
        + "\n"
    )
    if exit_code:
        raise SystemExit(1)


if __name__ == "__main__":
    with contextlib.suppress(BrokenPipeError):
        main()
