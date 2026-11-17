"""The legacy single-demo CLI must not turn diagnostics into a clean pass."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import check_demo


class SingleDemoTests(unittest.TestCase):
    def check(self, **changes):
        response = {
            "success": True,
            "score": 0.9,
            "action": check_demo.ACTION,
            "hostname": "recaptcha-demo.appspot.com",
        }
        response.update(changes)
        with (
            patch.object(check_demo, "Client") as client,
            patch.object(check_demo, "Session"),
            patch.object(
                check_demo,
                "fetch",
                side_effect=[
                    '<script src="https://www.google.com/recaptcha/api.js?render=test"></script>',
                    json.dumps(response),
                ],
            ),
        ):
            client.return_value.__enter__.return_value.solve.return_value = SimpleNamespace(
                token="synthetic", bundle_revision="1", protocol_version="fixture"
            )
            return check_demo.check(None, 1)

    def test_invalid_score_never_passes(self):
        for score in [None, "0.9", True, -1, 2, 10**400, float("inf"), float("nan")]:
            with self.subTest(score=score):
                result = self.check(score=score)
                self.assertFalse(result["server_accepted"])
                self.assertEqual(result["status"], "rejected")

    def test_quota_and_clean_acceptance_have_different_exit_status(self):
        for errors, expected_status, expected_exit in [
            ([], "accepted", 0),
            (["quota exceeded"], "degraded", 1),
        ]:
            result = self.check(**{"error-codes": errors})
            self.assertEqual(result["status"], expected_status)
            with (
                patch.object(sys, "argv", ["check_demo.py"]),
                patch.object(check_demo, "check", return_value=result),
                patch("builtins.print"),
            ):
                self.assertEqual(check_demo.main(), expected_exit)

    def test_malformed_diagnostics_are_rejected(self):
        for errors in [False, 0, {}, "", [None], ["x"] * 33]:
            with self.subTest(errors=errors):
                with self.assertRaisesRegex(check_demo.DemoError, "VerificationInvalidShape"):
                    self.check(**{"error-codes": errors})


if __name__ == "__main__":
    unittest.main()
