"""Regression protection for workload bounds and strict result validation."""

import asyncio
import base64
import copy
import gzip
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import Mock, patch

from pressure.fixture import PressureFixture
from pressure.report import evidence_digest, report
from pressure.run import main as run_main
from pressure.run import quantile, run_candidate, summarize, validate_budget, validate_samples
from pressure.verify_expiry import operation as expiry_operation

SAFETY_COUNTERS = (
    "credential_leaks",
    "blocked_external_targets",
    "protocol_errors",
    "shed_connections",
)


class PressureHarnessTests(unittest.TestCase):
    def test_budget_rejects_unbounded_or_too_short_work(self):
        for args in (
            (0, 4, 8, 10),
            (6, 4, 8, 10),
            (3, 1, 8, 10),
            (3, 4, 129, 10),
            (3, 4, 8, 1),
            (3, 4, 8, 121),
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                validate_budget(*args)

    def test_budget_is_deterministic_before_results(self):
        self.assertEqual(validate_budget(3, 4, 8, 10), validate_budget(3, 4, 8, 10))

    def test_negative_control_rejects_success_in_failure_profile(self):
        with self.assertRaises(AssertionError):
            validate_samples([{"elapsed_ns": 1, "error": None}], ("DeadlineExceeded",))

    def test_success_requires_correct_public_value(self):
        with self.assertRaises(AssertionError):
            validate_samples(
                [{"elapsed_ns": 1, "error": None, "value": "wrong"}], (None,), "matrix-bg"
            )

    def test_malformed_time_samples_are_rejected(self):
        for elapsed in (0, -1, True, "1", None):
            with self.subTest(elapsed=elapsed), self.assertRaises(AssertionError):
                validate_samples([{"elapsed_ns": elapsed, "error": None}], (None,))

    def test_reports_reject_partial_and_duplicate_evidence(self):
        plan = {"candidates": ["python"], "budget": {"rounds": 1}}
        for results in ([], [{"candidate": "python", "round": 0}] * 2):
            with self.subTest(results=results), self.assertRaises(ValueError):
                raw = {"plan": plan, "results": results}
                report(raw, {"exit_code": 0, "canonical_raw_sha256": evidence_digest(raw)})

    def test_report_requires_original_success_exit_status(self):
        for status in ({}, {"exit_code": 1}, {"exit_code": None}):
            with self.subTest(status=status), self.assertRaises(ValueError):
                report({}, status)

    def test_startup_failure_records_original_nonzero_status(self):
        with tempfile.TemporaryDirectory(prefix="anchorpass-pressure-status-") as directory:
            output = Path(directory) / "new"
            with (
                patch("sys.argv", ["run", "--output", str(output)]),
                patch("pressure.run.environment", side_effect=RuntimeError("controlled failure")),
                self.assertRaisesRegex(RuntimeError, "controlled failure"),
            ):
                run_main()
            record = json.loads((output / "exit-record.json").read_text())
            self.assertEqual(record["exit_code"], 1)
            self.assertEqual(record["error_type"], "RuntimeError")

    def test_runner_rejects_unsafe_fixture_before_and_after_operation(self):
        for counter in SAFETY_COUNTERS:
            for boundary in ("before", "after"):
                for value in (1, -1, True, False, 0.0, "0", None):
                    with (
                        self.subTest(counter=counter, boundary=boundary, value=value),
                        tempfile.TemporaryDirectory(prefix="anchorpass-pressure-safety-") as path,
                    ):
                        fixture = Mock()
                        states = {"before": {}, "after": {}}
                        states[boundary][counter] = value
                        fixture.call.side_effect = [{}, states["before"], states["after"]]
                        peer = Mock()
                        peer.call.side_effect = [
                            {"ok": True},
                            {"ok": True, "samples": [{"elapsed_ns": 1, "error": None}]},
                        ]
                        with (
                            patch("pressure.run.Peer", return_value=peer),
                            patch("pressure.run.terminate") as terminate_peer,
                            self.assertRaisesRegex(AssertionError, counter),
                        ):
                            run_candidate(
                                "python",
                                0,
                                Path(path),
                                fixture,
                                {},
                                validate_budget(1, 2, 8, 5),
                                {"python": ["controlled-adapter"]},
                            )
                        terminate_peer.assert_called_once_with(peer.process)
                        peer.close.assert_called_once()

    def test_final_fixture_failure_records_nonzero_exit_without_completion_digest(self):
        for counter in SAFETY_COUNTERS:
            with (
                self.subTest(counter=counter),
                tempfile.TemporaryDirectory(prefix="anchorpass-pressure-final-state-") as path,
            ):
                output = Path(path) / "new"
                fixture = Mock()
                fixture.call.return_value = {counter: 1}
                with (
                    patch("sys.argv", ["run", "--output", str(output), "--candidates", "python"]),
                    patch("pressure.run.environment", return_value={}),
                    patch("pressure.run.Peer", return_value=fixture),
                    patch("pressure.run.read_line", return_value={"ready": True, "fixture": {}}),
                    patch("pressure.run.run_candidate", return_value={"exit_code": 0}),
                    patch("pressure.run.terminate") as terminate_fixture,
                    self.assertRaisesRegex(AssertionError, counter),
                ):
                    run_main()
                record = json.loads((output / "exit-record.json").read_text())
                self.assertEqual(record["exit_code"], 1)
                self.assertEqual(record["error_type"], "AssertionError")
                self.assertNotIn("canonical_raw_sha256", record)
                self.assertFalse((output / "complete.json").exists())
                terminate_fixture.assert_called_once_with(fixture.process)
                fixture.close.assert_called_once()

    def test_empty_evidence_is_not_success(self):
        with self.assertRaises(AssertionError):
            validate_samples([], (None,))

    def test_focused_expiry_requires_exact_sample_count_and_adapter_success(self):
        for op, count, error, value in (
            ("refresh", 1, None, None),
            ("refresh", 8, "ConfigExpired", None),
            ("load", 8, "ConfigExpired", None),
            ("load", 4, None, "matrix-bg"),
        ):
            sample = {"elapsed_ns": 1, "error": error, "value": value}
            peer = Mock()
            peer.call.return_value = {"ok": True, "samples": [sample] * count}
            with self.subTest(op=op, count=count):
                self.assertEqual(
                    expiry_operation(peer, op, count, (error,), value), [sample] * count
                )
            for returned in {0, count - 1, count + 1}:
                peer.call.return_value = {"ok": True, "samples": [sample] * returned}
                with (
                    self.subTest(op=op, count=count, returned=returned),
                    self.assertRaisesRegex(AssertionError, "requested operations"),
                ):
                    expiry_operation(peer, op, count, (error,), value)
            for status in ({}, {"ok": False}, {"ok": None}, {"ok": 1}, {"ok": "true"}):
                peer.call.return_value = {**status, "samples": [sample] * count}
                with (
                    self.subTest(op=op, count=count, status=status),
                    self.assertRaisesRegex(AssertionError, "requested operations"),
                ):
                    expiry_operation(peer, op, count, (error,), value)

    def test_tail_quantiles_and_error_totals_use_raw_samples(self):
        samples = [{"elapsed_ns": n, "error": None} for n in range(1, 101)]
        samples[-1]["error"] = "DeadlineExceeded"
        result = summarize(samples)
        self.assertEqual((result["p50_ns"], result["p95_ns"], result["p99_ns"]), (50, 95, 99))
        self.assertEqual(result["errors"], {"success": 99, "DeadlineExceeded": 1})
        self.assertEqual(quantile([8], 0.99), 8)

    def test_fixture_blocks_external_route_and_counts_negative_control(self):
        fixture = object.__new__(PressureFixture)
        fixture.origin_port = 12345
        fixture.allowed_ports = {12345}
        fixture.metrics = Counter()
        self.assertEqual(fixture.target("pressure.invalid", 12345), ("127.0.0.1", 12345))
        with self.assertRaises(ValueError):
            fixture.target("example.com", 443)
        self.assertEqual(fixture.metrics["blocked_external_targets"], 1)
        self.assertEqual(fixture.metrics["proxy_only_name_resolutions"], 1)

    def test_fixture_recovery_advances_beyond_each_expired_floor(self):
        with tempfile.TemporaryDirectory(prefix="anchorpass-pressure-revisions-") as path:
            fixture = PressureFixture(Path(path))
            previous_floor = 0
            for _ in range(3):
                payloads = {
                    name: json.loads(base64.b64decode(json.loads(fixture.bundles[name])["payload"]))
                    for name in ("normal", "expired")
                }
                seed, expired = payloads["normal"], payloads["expired"]
                self.assertGreater(int(seed["revision"]), previous_floor)
                self.assertGreater(int(expired["revision"]), int(seed["revision"]))
                self.assertLess(expired["expires_at"], seed["issued_at"])
                previous_floor = int(expired["revision"])
                asyncio.run(fixture.command("mode:recover"))
                self.assertEqual(fixture.mode, "normal")


class PressureReportMutationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / "reports/pressure/raw.json.gz"
        raw = json.loads(gzip.decompress(path.read_bytes()))
        cls.evidence = {"plan": copy.deepcopy(raw["plan"]), "results": [raw["results"][0]]}
        cls.evidence["plan"]["candidates"] = ["python"]
        cls.evidence["plan"]["budget"]["rounds"] = 1
        cls.current_evidence = copy.deepcopy(cls.evidence)
        cls.current_evidence["plan"]["expiry_semantics"] = "monotonic-floor-v1"
        budget = cls.current_evidence["plan"]["budget"]
        cls.current_evidence["plan"]["budget"] = validate_budget(
            1,
            budget["equal_error_batches"],
            budget["error_batch_size"],
            budget["minimum_soak_seconds"],
        )
        stages = cls.current_evidence["results"][0]["stages"]
        for index, stage in enumerate(stages):
            if stage["name"] == "expired-automatic-fallback":
                recovery = copy.deepcopy(stage)
                recovery["name"] = "expired-floor-recovery"
                recovery["samples"] = recovery["samples"][:4]
                recovery["summary"] = summarize(recovery["samples"])
                recovery["fixture_delta"] = {"requests": 4, "path:/bundle": 4}
                stage["name"] = "expired-automatic-floor"
                for sample in stage["samples"]:
                    sample.update(error="ConfigExpired", value=None)
                stage["summary"] = summarize(stage["samples"])
                stages.insert(index + 1, recovery)
                break

    def assert_rejected(self, mutate, evidence=None, record_fields=None):
        raw = copy.deepcopy(self.evidence if evidence is None else evidence)
        mutate(raw)
        record = {"exit_code": 0, "canonical_raw_sha256": evidence_digest(raw)} | (
            record_fields or {}
        )
        with self.assertRaises((ValueError, AssertionError)):
            report(raw, record)

    def test_real_complete_fixture_is_accepted(self):
        record = {"exit_code": 0, "canonical_raw_sha256": evidence_digest(self.evidence)}
        result = report(self.evidence, record)
        self.assertEqual(result["runs"], 1)
        self.assertEqual(result["coverage"]["expiry_semantics"], "historical-fallback")
        self.assertFalse(result["coverage"]["current_expiry_floor_qualified"])

    def test_current_floor_and_recovery_are_required_by_recorded_plan(self):
        record = {"exit_code": 0, "canonical_raw_sha256": evidence_digest(self.current_evidence)}
        result = report(self.current_evidence, record)
        self.assertEqual(result["coverage"]["expiry_semantics"], "monotonic-floor-v1")
        self.assertTrue(result["coverage"]["current_expiry_floor_qualified"])
        self.assert_rejected(lambda raw: raw["plan"].update(expiry_semantics="monotonic-floor-v1"))
        self.assert_rejected(lambda raw: raw["plan"].pop("expiry_semantics"), self.current_evidence)

    def test_current_floor_cannot_accept_historical_fallback_success(self):
        self.assert_rejected(
            lambda raw: next(
                stage
                for stage in raw["results"][0]["stages"]
                if stage["name"] == "expired-automatic-floor"
            )["samples"][0].update(error=None, value="matrix-bg"),
            self.current_evidence,
        )

    def test_current_floor_requires_successful_newer_revision_recovery(self):
        self.assert_rejected(
            lambda raw: next(
                stage
                for stage in raw["results"][0]["stages"]
                if stage["name"] == "expired-floor-recovery"
            )["samples"][0].update(error="ConfigExpired", value=None),
            self.current_evidence,
        )

    def test_unknown_expiry_semantics_are_rejected(self):
        self.assert_rejected(lambda raw: raw["plan"].update(expiry_semantics="unknown"))

    def test_historical_exit_marker_cannot_relax_current_expiry_errors(self):
        def mutate(raw):
            raw["plan"]["candidates"] = ["go"]
            run = raw["results"][0]
            run["candidate"] = "go"
            for stage in run["stages"]:
                if stage["name"] == "expired-explicit":
                    for sample in stage["samples"]:
                        sample["error"] = "ConfigInvalid"
                elif stage["name"].startswith("proxy-error-"):
                    for sample in stage["samples"]:
                        sample["error"] = "ProxyAuthError"

        self.assert_rejected(mutate, self.current_evidence, {"historical_expiry_revision": "2"})

    def test_explicit_zero_safety_counters_are_accepted(self):
        raw = copy.deepcopy(self.evidence)
        raw["results"][0]["stages"][0]["fixture_delta"].update(dict.fromkeys(SAFETY_COUNTERS, 0))
        record = {"exit_code": 0, "canonical_raw_sha256": evidence_digest(raw)}
        self.assertEqual(report(raw, record)["runs"], 1)

    def test_unsafe_fixture_counters_are_rejected_even_with_matching_digest(self):
        for counter in SAFETY_COUNTERS:
            for value in (1, -1, True, False, 0.0, "0", None, [], {}):
                with self.subTest(counter=counter, value=value):
                    self.assert_rejected(
                        lambda raw, counter=counter, value=value: raw["results"][0]["stages"][0][
                            "fixture_delta"
                        ].update({counter: value})
                    )

    def test_malformed_fixture_metrics_are_rejected_even_with_matching_digest(self):
        for value in (None, [], "", 0):
            with self.subTest(value=value):
                self.assert_rejected(
                    lambda raw, value=value: raw["results"][0]["stages"][0].update(
                        fixture_delta=value
                    )
                )

    def test_mixed_socks_dns_policy_is_rejected_even_with_matching_digest(self):
        for name, counter in (
            ("dns-policy-socks5", "socks_atyp:3"),
            ("dns-policy-socks5h", "socks_atyp:1"),
            ("dns-policy-socks5h", "socks_atyp:4"),
        ):
            with self.subTest(name=name, counter=counter):
                self.assert_rejected(
                    lambda raw, name=name, counter=counter: next(
                        s for s in raw["results"][0]["stages"] if s["name"] == name
                    )["fixture_delta"].update({counter: 1})
                )

    def test_malformed_socks_counts_are_rejected_even_with_matching_digest(self):
        for name in ("dns-policy-socks5", "dns-policy-socks5h"):
            for counter in ("socks_atyp:1", "socks_atyp:3", "socks_atyp:4"):
                for value in (-1, True, False, 0.0, "0", None):
                    with self.subTest(name=name, counter=counter, value=value):
                        self.assert_rejected(
                            lambda raw, name=name, counter=counter, value=value: next(
                                s for s in raw["results"][0]["stages"] if s["name"] == name
                            )["fixture_delta"].update({counter: value})
                        )

    def test_wrong_warm_bg_is_rejected_even_with_matching_digest(self):
        self.assert_rejected(
            lambda raw: next(
                s for s in raw["results"][0]["stages"] if s["name"] == "warm-verified-bg"
            )["samples"][0].update(value="wrong")
        )

    def test_successful_failed_proxy_is_rejected_even_with_matching_digest(self):
        self.assert_rejected(
            lambda raw: next(
                s for s in raw["results"][0]["stages"] if s["name"] == "proxy-error-0"
            )["samples"][0].update(error=None)
        )

    def test_zero_soak_duration_is_rejected_even_with_matching_digest(self):
        self.assert_rejected(lambda raw: raw["results"][0].update(soak_wall_seconds=0))

    def test_zero_soak_operations_is_rejected_even_with_matching_digest(self):
        self.assert_rejected(lambda raw: raw["results"][0].update(soak_operations=0))

    def test_network_bypass_is_rejected_even_with_matching_digest(self):
        self.assert_rejected(
            lambda raw: next(
                s for s in raw["results"][0]["stages"] if s["name"] == "proxy-error-0"
            )["fixture_delta"].update(requests=1)
        )

    def test_mutating_raw_after_completion_breaks_binding(self):
        raw = copy.deepcopy(self.evidence)
        record = {"exit_code": 0, "canonical_raw_sha256": evidence_digest(raw)}
        raw["results"][0]["stages"][0]["samples"][0]["elapsed_ns"] += 1
        with self.assertRaisesRegex(ValueError, "does not bind"):
            report(raw, record)


if __name__ == "__main__":
    unittest.main()
