"""Regression checks for measurement integrity, without comparative timing."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import merge_matrix
import python_matrix
import report_matrix
import run_matrix


class MatrixIntegrityTests(unittest.TestCase):
    def test_partial_protocol_line_obeys_the_whole_response_deadline(self):
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys;sys.stdout.write('{');sys.stdout.flush();sys.stdin.read()",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            start_new_session=True,
        )
        guard = threading.Timer(2, run_matrix.terminate, args=(child,))
        guard.start()
        try:
            started = time.monotonic()
            with self.assertRaises(TimeoutError):
                run_matrix.read_line(child, timeout=0.2)
            self.assertLess(time.monotonic() - started, 1)
        finally:
            guard.cancel()
            run_matrix.terminate(child)
            guard.join()
            child.stdin.close()
            child.stdout.close()

    def test_protocol_reader_bounds_a_line_and_preserves_prefetched_messages(self):
        for output, limit in ((b'{"a":1}\n{"b":2}\n', 1024), (b"x" * 65, 64)):
            with self.subTest(limit=limit):
                child = subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        "import os,sys;os.write(1,bytes.fromhex(sys.argv[1]))",
                        output.hex(),
                    ],
                    stdout=subprocess.PIPE,
                    start_new_session=True,
                )
                try:
                    with patch.object(run_matrix, "MAX_PROTOCOL_LINE_BYTES", limit):
                        if limit == 64:
                            with self.assertRaises(ValueError):
                                run_matrix.read_line(child, timeout=2)
                        else:
                            self.assertEqual(run_matrix.read_line(child), {"a": 1})
                            self.assertEqual(run_matrix.read_line(child), {"b": 2})
                    self.assertEqual(child.wait(timeout=2), 0)
                finally:
                    run_matrix.terminate(child)
                    child.stdout.close()

    def setUp(self):
        self.profile = {
            "kind": "transport",
            "route": "direct",
            "connection_mode": "warm",
            "concurrency": 1,
            "warmup": 0,
            "requests": 1,
            "recovery_requests": 0,
            "expected_error": None,
            "expected_bytes": 1024,
            "timeout_ms": 2000,
            "max_response_bytes": 65536,
            "path": "/bytes?size=1024",
        }
        self.job = {"run_id": "test", "candidate": "test", "profile": self.profile}
        self.result = {
            "schema_version": 1,
            "run_id": "test",
            "candidate": "test",
            "status": "ok",
            "samples": [{"elapsed_ns": 100, "ok": True, "error": None, "bytes": 1024}],
            "recovery": [],
            "warmup_completed": 0,
        }
        self.metrics = {"requests": 1}

    def test_unknown_programming_errors_cannot_satisfy_transport_control(self):
        for error in (NameError("mistake"), ValueError("mistake"), RuntimeError("mistake")):
            with self.subTest(error=type(error)):
                self.assertEqual(python_matrix.classify(error), "unclassified")
                profile = {**self.profile, "expected_error": "transport"}
                observed = python_matrix.sample(profile, time.perf_counter_ns(), error=error)
                self.assertFalse(observed["ok"])
        self.assertEqual(python_matrix.classify(ConnectionResetError(104, "reset")), "transport")

    def test_result_schema_and_byte_counts_are_checked(self):
        self.assertEqual(run_matrix.validate(self.job, self.result, self.metrics), [])
        unknown = {**self.result, "status": "failed"}
        self.assertTrue(run_matrix.validate(self.job, unknown, self.metrics))
        wrong_size = copy.deepcopy(self.result)
        wrong_size["samples"][0]["bytes"] = 0
        self.assertTrue(run_matrix.validate(self.job, wrong_size, self.metrics))
        unsupported = {**self.result, "status": "unsupported", "reason": "feature", "samples": []}
        self.assertEqual(run_matrix.validate(self.job, unsupported, {}), [])
        unsupported["recovery"] = [{"ok": False}]
        self.assertTrue(run_matrix.validate(self.job, unsupported, {}))
        unsupported["recovery"] = []
        for key in (
            "credential_leaks",
            "blocked_external_targets",
            "protocol_errors",
            "shed_connections",
        ):
            self.assertTrue(run_matrix.validate(self.job, unsupported, {key: 1}))

    def test_honest_failed_observation_is_retained(self):
        result = copy.deepcopy(self.result)
        result["samples"][0].update(ok=False, error="unclassified")
        self.assertEqual(run_matrix.validate(self.job, result, self.metrics), [])

    def test_recovery_waits_for_every_measured_worker(self):
        primary_finished, recovery_started = [], []
        guard = threading.Lock()
        sequence = 0

        class FakeTransport:
            def __init__(self, job):
                pass

            def request(self, profile):
                nonlocal sequence
                if profile["path"] == "/reset":
                    with guard:
                        index = sequence
                        sequence += 1
                    time.sleep(0.03 if index == 0 else 0.001)
                    primary_finished.append(time.perf_counter_ns())
                    raise python_matrix.ObservedError("transport")
                recovery_started.append(time.perf_counter_ns())
                return 1024

            def close(self):
                pass

        profile = {
            **self.profile,
            "concurrency": 2,
            "requests": 3,
            "recovery_requests": 2,
            "path": "/reset",
            "expected_error": "transport",
        }
        with patch.object(python_matrix, "SyncTransport", FakeTransport):
            samples, recovery, _, _ = python_matrix.run_sync(
                {**self.job, "profile": profile}, "unused"
            )
        self.assertTrue(all(item["ok"] for item in samples + recovery))
        self.assertGreaterEqual(min(recovery_started), max(primary_finished))

    @unittest.skipUnless(Path("/proc/self").exists(), "Linux process accounting")
    def test_owned_process_group_is_cleaned_after_leader_exits(self):
        with tempfile.TemporaryDirectory(prefix="anchorpass-matrix-test-") as temporary:
            directory = Path(temporary)
            pid_path = directory / "child.pid"
            code = "import pathlib,subprocess,sys; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']); pathlib.Path(sys.argv[1]).write_text(str(p.pid))"
            result = run_matrix.monitored(
                [sys.executable, "-c", code, str(pid_path)], directory, "parent", timeout=5
            )
            self.assertEqual(result["exit_code"], 0)
            pid = int(pid_path.read_text())
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                try:
                    state = Path(f"/proc/{pid}/stat").read_text().split(")", 1)[1].split()[0]
                except FileNotFoundError:
                    break
                if state == "Z":
                    break
                time.sleep(0.01)
            else:
                os.kill(pid, 9)
                self.fail("owned child survived group cleanup")

    def test_manifest_budget_is_deterministic(self):
        manifest = json.loads((Path(__file__).with_name("profiles.json")).read_text())
        planned = list(
            run_matrix.jobs(manifest, run_matrix.RAW_CANDIDATES + run_matrix.API_CANDIDATES)
        )
        expected = len(run_matrix.RAW_CANDIDATES) * (
            len(manifest["capabilities"])
            + manifest["rounds"] * (len(manifest["performance"]) + len(manifest.get("flow", [])))
        ) + len(run_matrix.API_CANDIDATES) * manifest["rounds"] * len(manifest["api"])
        self.assertEqual(len(planned), expected)

    def test_empty_or_unknown_selection_fails_before_building(self):
        with tempfile.TemporaryDirectory(prefix="anchorpass-empty-plan-") as temporary:
            output = Path(temporary) / "run"
            for candidate, profile in (
                ("go-net-http", "direct-warm-c1,flow-direct-warm-c1"),
                ("go-api", "direct-warm-c1"),
            ):
                with (
                    self.subTest(candidate=candidate, profile=profile),
                    patch.object(
                        sys,
                        "argv",
                        [
                            "matrix",
                            "--output",
                            str(output),
                            "--candidates",
                            candidate,
                            "--profiles",
                            profile,
                        ],
                    ),
                    patch.object(run_matrix, "build_adapters") as build,
                    self.assertRaises(SystemExit) as raised,
                ):
                    run_matrix.main()
                self.assertEqual(raised.exception.code, 2)
                build.assert_not_called()
                self.assertFalse(output.exists())

    def test_flow_requires_every_step_and_exactly_three_requests(self):
        profile = {**self.profile, "kind": "flow", "expected_bytes": len(b"fixture-token")}
        job = {**self.job, "profile": profile}
        result = copy.deepcopy(self.result)
        result["samples"][0]["bytes"] = len(b"fixture-token")
        metrics = {
            "requests": 3,
            "path:/recaptcha/api.js": 1,
            "path:/recaptcha/api2/anchor": 1,
            "path:/recaptcha/api2/reload": 1,
        }
        self.assertEqual(run_matrix.validate(job, result, metrics), [])
        for key in metrics:
            with self.subTest(key=key):
                changed = {**metrics, key: 0}
                self.assertTrue(run_matrix.validate(job, result, changed))
        result["samples"][0]["bytes"] = 0
        self.assertTrue(run_matrix.validate(job, result, metrics))

    def test_report_retains_failed_rounds_and_all_failed_groups(self):
        for failed_rounds in ({1}, {0, 1, 2}):
            with self.subTest(failed_rounds=failed_rounds):
                rows = []
                for index in range(3):
                    failed = index in failed_rounds
                    result = {
                        **self.result,
                        "samples": [
                            {
                                "elapsed_ns": 2000000,
                                "ok": not failed,
                                "error": "transport" if failed else None,
                                "bytes": 0 if failed else 1024,
                            }
                        ],
                        "wall_ns": 3000000,
                        "cpu_ns": 1000000,
                    }
                    rows.append(
                        {
                            "job": {**self.job, "profile": {**self.profile, "name": "test"}},
                            "result": result,
                            "validation_errors": [],
                            "execution": {
                                "resources": {
                                    "rss_peak_bytes": 1024,
                                    "threads_peak": 1,
                                    "fds_peak": 1,
                                }
                            },
                            "fixture": {},
                        }
                    )
                raw = {
                    "smoke": False,
                    "jobs": rows,
                    "planned_jobs": 3,
                    "manifest": {"capabilities": []},
                    "environment": {},
                }
                summary = report_matrix.summarize(raw)
                group = summary["groups"][0]
                self.assertEqual(group["failed"], len(failed_rounds))
                self.assertEqual(group["successful_rounds"], 3 - len(failed_rounds))
                self.assertEqual(group["p50_ms"], None if len(failed_rounds) == 3 else 2)
                self.assertIn("test", report_matrix.markdown(raw, summary, Path("/tmp/raw.json")))
                self.assertIn("нет успехов", report_matrix.chart(summary))
                raw["manifest"]["capabilities"] = [{"name": "required-control"}]
                self.assertFalse(report_matrix.summarize(raw)["groups"][0]["controls_qualified"])
                control = copy.deepcopy(rows[0])
                control["job"]["profile"].update(name="required-control", expected_error="deadline")
                raw["jobs"].append(control)
                for verdict in (True, False):
                    control["result"]["samples"][0]["ok"] = verdict
                    self.assertEqual(
                        report_matrix.summarize(raw)["groups"][0]["controls_qualified"], verdict
                    )
                control["result"]["status"] = "unsupported"
                control["result"]["reason"] = "unsupported required control"
                self.assertFalse(report_matrix.summarize(raw)["groups"][0]["controls_qualified"])

    def test_report_requires_final_source_integrity_confirmation(self):
        with tempfile.TemporaryDirectory(prefix="anchorpass-report-integrity-") as temporary:
            directory = Path(temporary)
            source = directory / "raw.json"
            output = directory / "report"
            for flag in (None, False):
                raw = {"smoke": False}
                if flag is not None:
                    raw["sources_unchanged"] = flag
                source.write_text(json.dumps(raw))
                with (
                    self.subTest(flag=flag),
                    patch.object(sys, "argv", ["report", str(source), "--output", str(output)]),
                    self.assertRaisesRegex(SystemExit, "source integrity"),
                ):
                    report_matrix.main()
                self.assertFalse(output.exists())

    def test_selective_rerun_preserves_provenance_and_original_input(self):
        row = {
            "job": {**self.job, "round": 0, "profile": {**self.profile, "name": "test"}},
            "validation_errors": [],
            "result": self.result,
        }
        base = {
            "smoke": False,
            "sources_unchanged": True,
            "planned_jobs": 1,
            "jobs": [row],
            "manifest": {},
            "environment": {},
            "builds": [],
        }
        replacement = copy.deepcopy(base)
        replacement["jobs"][0]["result"]["marker"] = "new"
        result = merge_matrix.merge(
            base, replacement, base_path="base", replacement_path="rerun", reason="fixed"
        )
        self.assertEqual(result["jobs"][0]["result"]["marker"], "new")
        self.assertEqual(result["jobs"][0]["evidence_run"], "replacement")
        self.assertEqual(result["provenance"]["replaced_jobs"], [["test", "test", 0]])
        self.assertNotIn("marker", base["jobs"][0]["result"])
        for flag in (None, False):
            unconfirmed = copy.deepcopy(replacement)
            unconfirmed.pop("sources_unchanged")
            if flag is not None:
                unconfirmed["sources_unchanged"] = flag
            with self.assertRaisesRegex(ValueError, "source integrity"):
                merge_matrix.merge(
                    base, unconfirmed, base_path="base", replacement_path="rerun", reason="invalid"
                )
        replacement["jobs"][0]["job"]["profile"]["requests"] = 2
        with self.assertRaisesRegex(ValueError, "changes workload"):
            merge_matrix.merge(
                base, replacement, base_path="base", replacement_path="rerun", reason="invalid"
            )


if __name__ == "__main__":
    unittest.main()
