"""Sandbox isolation canaries A-F for AutoPiton.

Every test runs through the real bwrap sandbox; there is no mocked path.
Run from the PITON repository root:

    python3 -m unittest autopiton.tests.test_sandbox -v
"""

import json
import os
import subprocess
import time
import unittest

from autopiton.cpython_wrapper import RESULT_FILENAME
from autopiton.sandbox import Sandbox, SandboxConfig, sandbox_available


def _report(sandbox):
    return json.loads((sandbox.workdir / RESULT_FILENAME).read_text(encoding="utf-8"))


class SandboxCanaries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not sandbox_available():
            raise unittest.SkipTest("bubblewrap is not available")

    def test_a_normal_run(self):
        with Sandbox(SandboxConfig(timeout_seconds=5)) as sandbox:
            result = sandbox.run_cpython("print('hello')\n")
            report = _report(sandbox)
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(result.succeeded)
        self.assertEqual(result.stdout, "hello\n")
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.fs_created, [])
        self.assertEqual(report["status"], "ok")

    def test_a2_runtime_exception_is_structured(self):
        with Sandbox(SandboxConfig(timeout_seconds=5)) as sandbox:
            result = sandbox.run_cpython("print('before')\n1/0\n")
            report = _report(sandbox)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("ZeroDivisionError", result.stderr)
        self.assertEqual(report["status"], "exception")
        self.assertEqual(report["type"], "ZeroDivisionError")
        self.assertIn('File "program.py"', report["traceback"])

    def test_a3_syntax_error_is_structured(self):
        with Sandbox(SandboxConfig(timeout_seconds=5)) as sandbox:
            result = sandbox.run_cpython("def broken(:\n")
            report = _report(sandbox)
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(report["status"], "compile_error")
        self.assertEqual(report["type"], "SyntaxError")

    def test_b_infinite_loop_times_out(self):
        config = SandboxConfig(timeout_seconds=1.0)
        started = time.monotonic()
        with Sandbox(config) as sandbox:
            result = sandbox.run_cpython("while True:\n    pass\n")
        elapsed = time.monotonic() - started
        self.assertTrue(result.timed_out)
        self.assertLess(elapsed, 8.0)

    def test_c_memory_limit(self):
        config = SandboxConfig(timeout_seconds=10, memory_limit_mb=128)
        with Sandbox(config) as sandbox:
            result = sandbox.run_cpython(
                "blocks = []\n"
                "while True:\n"
                "    blocks.append(bytearray(10_000_000))\n"
            )
            report = _report(sandbox)
        self.assertNotEqual(result.exit_code, 0)
        self.assertTrue(result.memory_exceeded or report.get("type") == "MemoryError")

    def test_d_write_outside_workdir_is_denied(self):
        with Sandbox(SandboxConfig(timeout_seconds=5)) as sandbox:
            result = sandbox.run_cpython(
                "open('/usr/autopiton_pwned', 'w').write('x')\n"
            )
            report = _report(sandbox)
        self.assertNotEqual(result.exit_code, 0)
        self.assertEqual(report["status"], "exception")
        self.assertFalse(os.path.exists("/usr/autopiton_pwned"))

    def test_d2_tmp_writes_stay_inside_namespace(self):
        host_path = f"/tmp/autopiton_escape_{time.time_ns()}"
        with Sandbox(SandboxConfig(timeout_seconds=5)) as sandbox:
            result = sandbox.run_cpython(
                f"open({host_path!r}, 'w').write('x')\nprint('wrote')\n"
            )
        self.assertEqual(result.exit_code, 0)
        self.assertFalse(os.path.exists(host_path))

    def test_e_network_is_blocked(self):
        with Sandbox(SandboxConfig(timeout_seconds=10)) as sandbox:
            result = sandbox.run_cpython(
                "import socket\n"
                "socket.create_connection(('1.1.1.1', 80), timeout=2)\n"
            )
            report = _report(sandbox)
        self.assertNotEqual(result.exit_code, 0)
        self.assertEqual(report["status"], "exception")
        self.assertIn(report["type"], {"OSError", "TimeoutError"})

    def test_f_process_tree_is_killed_on_timeout(self):
        config = SandboxConfig(timeout_seconds=1.5)
        with Sandbox(config) as sandbox:
            result = sandbox.run_cpython(
                "import subprocess, time\n"
                "subprocess.Popen(['sleep', '54321'])\n"
                "time.sleep(30)\n"
            )
        self.assertTrue(result.timed_out)
        time.sleep(0.5)
        survivors = subprocess.run(
            ["pgrep", "-f", "sleep 54321"], capture_output=True, text=True
        )
        self.assertEqual(survivors.returncode, 1, survivors.stdout)

    def test_g_environment_is_cleared(self):
        with Sandbox(SandboxConfig(timeout_seconds=5)) as sandbox:
            result = sandbox.run_cpython("import os\nprint(sorted(os.environ))\n")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(
            result.stdout.strip(),
            str(["HOME", "LANG", "LC_ALL", "PATH", "PWD", "TMPDIR"]),
        )

    def test_h_filesystem_changes_are_recorded(self):
        with Sandbox(SandboxConfig(timeout_seconds=5)) as sandbox:
            result = sandbox.run_cpython(
                "with open('out.txt', 'w') as handle:\n"
                "    handle.write('data')\n"
            )
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(len(result.fs_created), 1)
        entry = result.fs_created[0]
        self.assertEqual(entry["path"], "out.txt")
        self.assertEqual(entry["size"], 4)

    def test_i_output_is_truncated(self):
        config = SandboxConfig(timeout_seconds=5, max_output_bytes=1000)
        with Sandbox(config) as sandbox:
            result = sandbox.run_cpython("print('x' * 100000)\n")
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(result.output_truncated)
        self.assertEqual(len(result.stdout), 1000)

    def test_j_children_are_allowed_but_isolated(self):
        with Sandbox(SandboxConfig(timeout_seconds=5)) as sandbox:
            result = sandbox.run_cpython(
                "import subprocess\n"
                "done = subprocess.run(['echo', 'child'], capture_output=True, text=True)\n"
                "print(done.stdout.strip())\n"
            )
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "child\n")


if __name__ == "__main__":
    unittest.main()
