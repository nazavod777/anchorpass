"""Offline regression protection for live-result classification and subprocess limits."""

from __future__ import annotations

import json
import os
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import check_demo_matrix as matrix
import live_sites
from check_demo import DemoError


class ClassificationTests(unittest.TestCase):
    def standard(self, **changes):
        value = {"success": True, "hostname": "2captcha.com", "action": "demo_action", "score": 0.9}
        value.update(changes)
        return live_sites.assess(live_sites.SITES["two-captcha-v3"], value)

    def test_acceptance_requires_exact_boolean_hostname_action_and_score(self):
        self.assertEqual(self.standard()["status"], "accepted")
        for changes in [
            {"success": 1},
            {"success": False},
            {"hostname": "other.invalid"},
            {"action": "wrong"},
            {"action": None},
            {"score": True},
            {"score": float("nan")},
            {"score": 2},
            {"score": 10**400},
            {"score": -(10**400)},
            {"score": float("inf")},
            {"score": -float("inf")},
            {"score": "0.9"},
        ]:
            with self.subTest(changes=changes):
                self.assertFalse(self.standard(**changes)["server_accepted"])

    def test_quota_warning_is_not_clean_acceptance(self):
        result = self.standard(**{"error-codes": ["Over Enterprise free quota"]})
        self.assertTrue(result["server_accepted"])
        self.assertEqual(result["status"], "degraded")

    def test_enterprise_missing_action_is_explicitly_limited(self):
        site = live_sites.SITES["two-captcha-enterprise"]
        value = {
            "success": True,
            "tokenProperties": {"valid": True, "hostname": "2captcha.com"},
            "riskAnalysis": {"score": 0.3},
        }
        result = live_sites.assess(site, value)
        self.assertEqual(result["status"], "accepted_action_unreported")
        self.assertFalse(result["action_confirmed"])
        value["tokenProperties"]["action"] = "wrong"
        self.assertFalse(live_sites.assess(site, value)["server_accepted"])
        value["tokenProperties"].update(action="demo_action", valid=False)
        self.assertFalse(live_sites.assess(site, value)["server_accepted"])

    def test_malformed_response_fields_are_bounded(self):
        for errors in ["error", [None], ["x" * 1025], ["x"] * 33]:
            with self.assertRaisesRegex(DemoError, "VerificationInvalidShape"):
                self.standard(**{"error-codes": errors})
        for text in ["[]", '{"success":true,"success":false}', '{"score":NaN}', "{"]:
            with self.assertRaisesRegex(DemoError, "VerificationInvalidJson"):
                live_sites.object_json(text)

    def test_invalid_control_blocks_every_solve(self):
        with (
            patch.object(matrix, "Session") as session,
            patch.object(
                matrix, "discover", return_value={"site_key": "test", "source_sha256": {}}
            ),
            patch.object(matrix, "verify", return_value={"success": True}),
            patch.object(matrix, "obtain") as obtain,
        ):
            session.return_value.__enter__.return_value = object()
            control, rows = matrix.check_site("google", ["python-sync", "node-sync"], None, 1, 1)
        obtain.assert_not_called()
        self.assertFalse(control["invalid_token_rejected"])
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["status"] == "unavailable" for row in rows))

    def test_negative_control_transport_error_is_not_rejection(self):
        with (
            patch.object(matrix, "Session"),
            patch.object(
                matrix, "discover", return_value={"site_key": "test", "source_sha256": {}}
            ),
            patch.object(matrix, "verify", side_effect=DemoError("DemoTransportError")),
        ):
            control, rows = matrix.check_site("google", ["go"], None, 1, 1)
        self.assertFalse(control["invalid_token_rejected"])
        self.assertEqual(rows[0]["status"], "unavailable")


class DiscoveryTests(unittest.TestCase):
    def test_active_two_captcha_widget_checked_instead_of_tutorial_key(self):
        responses = [
            '<script src="/dist/web/assets/index-entry.js"></script>old tutorial key',
            'url:"/api/v1/captcha-demo/", import("./index-widget.js")',
            'key="6Lcyqq8oAAAAAJE7eVJ3aZp_hnJcI6LgGdYD8lge"; action="demo_action"',
        ]
        with patch.object(live_sites, "request", side_effect=responses) as request:
            result = live_sites.discover(
                object(), live_sites.SITES["two-captcha-v3"], time.monotonic() + 1
            )
        self.assertIn("api2/anchor?", result["anchor"])
        self.assertEqual(len(result["source_sha256"]), 3)
        self.assertEqual(
            request.call_args_list[-1].args[1],
            "https://2captcha.com/dist/web/assets/index-widget.js",
        )

    def test_changed_widget_key_fails_closed(self):
        responses = [
            '<script src="/dist/web/assets/index-entry.js"></script>',
            'url:"/api/v1/captcha-demo/", import("./index-widget.js")',
            "new-key demo_action",
        ]
        with (
            patch.object(live_sites, "request", side_effect=responses),
            self.assertRaisesRegex(DemoError, "DemoConfigurationChanged"),
        ):
            live_sites.discover(object(), live_sites.SITES["two-captcha-v3"], time.monotonic() + 1)

    def test_fetched_script_cannot_select_an_external_origin(self):
        with (
            patch.object(
                live_sites,
                "request",
                return_value='<script src="https://elsewhere.invalid/index-a.js"></script>',
            ) as request,
            self.assertRaisesRegex(DemoError, "DemoEntryScriptNotFound"),
        ):
            live_sites.discover(object(), live_sites.SITES["two-captcha-v3"], time.monotonic() + 1)
        self.assertEqual(request.call_count, 1)

    def test_zennolab_extracts_json_not_success_banner(self):
        body = '<h1>Success</h1><pre>{"success": false, "error-codes": ["invalid-input-response"]}</pre>'
        with patch.object(live_sites, "request", return_value=body):
            result = live_sites.verify(
                object(), live_sites.SITES["zennolab"], "key", "synthetic", time.monotonic() + 1
            )
        self.assertIs(result["success"], False)

    def test_http_body_limit_and_expired_deadline(self):
        class FakeSession:
            def request(self, method, url, **options):
                callback = options["content_callback"]
                callback(b"a" * 64)
                if callback(b"b") != 1:
                    raise live_sites.RequestException("synthetic body limit")
                raise AssertionError("limit must stop the request")

        with self.assertRaisesRegex(DemoError, "ResponseLimit"):
            live_sites.request(
                FakeSession(), "https://example.invalid", time.monotonic() + 1, limit=64
            )
        with self.assertRaisesRegex(DemoError, "DeadlineExceeded"):
            live_sites.request(FakeSession(), "https://example.invalid", time.monotonic() - 1)


class HelperTests(unittest.TestCase):
    def run_child(self, code, timeout=2):
        return matrix.run_helper([sys.executable, "-c", code], {"synthetic": True}, timeout)

    def test_valid_result(self):
        value = self.run_child('print(\'{"token":"synthetic-token","bundle_revision":"1"}\')')
        self.assertEqual(value["bundle_revision"], "1")

    def test_partial_output_does_not_defeat_deadline(self):
        with self.assertRaisesRegex(DemoError, "HelperDeadlineExceeded"):
            self.run_child(
                'import sys,time;sys.stdout.write("{");sys.stdout.flush();time.sleep(10)', 0.2
            )

    @unittest.skipUnless(sys.platform == "linux", "uses procfs to distinguish stopped descendants")
    def test_exited_leader_does_not_leave_running_descendants(self):
        with tempfile.TemporaryDirectory(prefix="anchorpass-live-child-") as directory:
            pidfile = Path(directory) / "pids.json"
            program = f"""import json, os, time
from pathlib import Path
child = os.fork()
if child == 0:
    time.sleep(15)
    os._exit(0)
Path({str(pidfile)!r}).write_text(json.dumps({{"group": os.getpid(), "child": child}}))
os._exit(0)
"""
            try:
                with self.assertRaisesRegex(DemoError, "HelperDeadlineExceeded"):
                    self.run_child(program, 0.5)
                pids = json.loads(pidfile.read_text())
                deadline = time.monotonic() + 1
                while True:
                    try:
                        state = (
                            Path(f"/proc/{pids['child']}/stat")
                            .read_text()
                            .split(") ")[1]
                            .split()[0]
                        )
                    except FileNotFoundError:
                        state = None
                    if state in (None, "Z") or time.monotonic() >= deadline:
                        break
                    time.sleep(0.01)
                self.assertIn(state, (None, "Z"))
            finally:
                if pidfile.exists():
                    pids = json.loads(pidfile.read_text())
                    try:
                        os.killpg(pids["group"], signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_oversized_stdout_or_stderr_is_rejected(self):
        for stream in ["stdout", "stderr"]:
            with (
                self.subTest(stream=stream),
                self.assertRaisesRegex(DemoError, "HelperResponseLimit"),
            ):
                self.run_child(f'import sys;sys.{stream}.write("x"*65537)')

    def test_child_failure_never_echoes_token_or_proxy(self):
        raw_error = "http://user:password@proxy.invalid synthetic-token"
        program = "import sys;print(" + repr(json.dumps({"error": raw_error})) + ");sys.exit(1)"
        with self.assertRaisesRegex(DemoError, "^HelperFailed$"):
            self.run_child(program)

    def test_bad_helper_payload_is_not_a_token(self):
        for value in [
            {"token": "", "bundle_revision": "1"},
            {"token": "x", "bundle_revision": 1},
            {"token": "x", "bundle_revision": "0"},
        ]:
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(DemoError, "HelperInvalidResponse"),
            ):
                self.run_child("print(" + repr(json.dumps(value)) + ")")


if __name__ == "__main__":
    unittest.main()
