"""Package checks must stop and reap their owned commands when interrupted."""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
CHECKERS = tuple(
    importlib.import_module(name)
    for name in ("check_python_package", "check_go_package", "check_rust_package")
)

WRAPPER = """
import importlib, os, sys
from pathlib import Path
checker = importlib.import_module(sys.argv[1])
arguments = [[sys.executable, '-c', sys.argv[3], sys.argv[4]], Path(sys.argv[2])]
if checker.__name__ == 'check_go_package':
    arguments.append(dict(os.environ))
checker.run(*arguments)
"""

CHILD = """
import json, os, subprocess, sys, time
from pathlib import Path
descendant = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
Path(sys.argv[1]).write_text(json.dumps({'pid': os.getpid(), 'descendant': descendant.pid}))
time.sleep(30)
"""


def invoke(checker, command: list[str], cwd: Path) -> None:
    if checker.__name__ == "check_go_package":
        checker.run(command, cwd, dict(os.environ))
    else:
        checker.run(command, cwd)


def running(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().split(")", 1)[1].split()[0]
    except FileNotFoundError:
        return False
    return state != "Z"


class PackageCheckProcessTests(unittest.TestCase):
    def test_child_exit_status_is_preserved(self) -> None:
        for checker in CHECKERS:
            with self.subTest(checker=checker.__name__):
                invoke(checker, [sys.executable, "-c", "pass"], SCRIPTS)
                with self.assertRaises(SystemExit) as error:
                    invoke(checker, [sys.executable, "-c", "raise SystemExit(7)"], SCRIPTS)
                self.assertEqual(error.exception.code, 7)

    def test_cleanup_race_preserves_interrupt_and_timeout(self) -> None:
        for checker in CHECKERS:
            for interrupted in (True, False):
                with self.subTest(checker=checker.__name__, interrupted=interrupted):
                    error = (
                        KeyboardInterrupt()
                        if interrupted
                        else subprocess.TimeoutExpired(["controlled-command"], 1)
                    )
                    process = Mock(pid=12345)
                    if checker.__name__ == "check_go_package":
                        process.communicate.side_effect = [error, ("partial output\n", None)]
                    else:
                        process.wait.side_effect = [error, -signal.SIGKILL]
                    output = io.StringIO()
                    with (
                        patch.object(checker.subprocess, "Popen", return_value=process),
                        patch.object(
                            checker.os, "killpg", side_effect=ProcessLookupError
                        ) as kill_group,
                        contextlib.redirect_stdout(output),
                        self.assertRaises(KeyboardInterrupt if interrupted else SystemExit),
                    ):
                        invoke(checker, ["controlled-command"], SCRIPTS)
                    kill_group.assert_called_once_with(process.pid, signal.SIGKILL)
                    if checker.__name__ == "check_go_package":
                        self.assertEqual(process.communicate.call_count, 2)
                        self.assertEqual(output.getvalue(), "partial output\n")
                    else:
                        self.assertEqual(process.wait.call_count, 2)

    @unittest.skipUnless(Path("/proc/self").exists(), "Linux process accounting")
    def test_sigint_stops_owned_child_and_descendant(self) -> None:
        for checker in CHECKERS:
            with (
                self.subTest(checker=checker.__name__),
                tempfile.TemporaryDirectory(prefix="anchorpass-package-interrupt-") as name,
            ):
                work = Path(name)
                marker = work / "ready.json"
                command = [
                    sys.executable,
                    "-c",
                    WRAPPER,
                    checker.__name__,
                    str(work),
                    CHILD,
                    str(marker),
                ]
                wrapper = subprocess.Popen(
                    command,
                    env={**os.environ, "PYTHONPATH": str(SCRIPTS)},
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
                owned = None
                try:
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        if marker.exists():
                            try:
                                owned = json.loads(marker.read_text())
                            except json.JSONDecodeError:
                                pass
                            else:
                                break
                        if wrapper.poll() is not None:
                            self.fail(f"wrapper exited before readiness: {wrapper.communicate()}")
                        time.sleep(0.01)
                    else:
                        self.fail("owned command did not report readiness before deadline")
                    self.assertTrue(running(owned["pid"]))
                    self.assertTrue(running(owned["descendant"]))
                    wrapper.send_signal(signal.SIGINT)
                    output, _ = wrapper.communicate(timeout=5)
                    self.assertEqual(wrapper.returncode, -signal.SIGINT, output)
                    self.assertFalse(Path(f"/proc/{owned['pid']}").exists(), output)
                    self.assertFalse(running(owned["descendant"]), output)
                finally:
                    if owned is not None:
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(owned["pid"], signal.SIGKILL)
                    if wrapper.poll() is None:
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(wrapper.pid, signal.SIGKILL)
                    wrapper.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
