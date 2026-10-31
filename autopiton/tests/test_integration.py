"""End-to-end integration: real CPython oracle + real PITON native pipeline.

Runs only when bubblewrap is available. These are the M0 canaries, executed
through the same objects the CLI uses.

Canaries C and F depend on PITON's ``mir: relax`` commits (the last one being
``fd76a88d``). On checkouts where that commit is not in HEAD's history the two
tests are skipped with an explicit reason instead of failing: the AutoPiton
contract holds, but this PITON does not yet classify those programs the same
way. Canary A does not need any of them.

The assertions deliberately validate the *AutoPiton harness contract*, not a
historical PITON commit. A squash/rebase must not silently disable coverage.
PITON may legitimately move from UNSUPPORTED/INVALID_ACCEPTED to a better
classification as its semantics improve; these tests require a real,
non-harness verdict with the expected CPython reference behavior.
"""

import subprocess
import unittest
from pathlib import Path

from autopiton.comparator import Classification, DifferentialComparator
from autopiton.cpython_oracle import CPythonOracle
from autopiton.generator import ProgramGenerator
from autopiton.piton_runner import PitonRunner, PitonStage
from autopiton.sandbox import sandbox_available

CANARIES = {canary.description.split(":")[0]: canary
            for canary in ProgramGenerator().generate_canary_cases()}

REQUIRED_PITON_COMMIT = "fd76a88d50d6052188f986eed11a4f21d9375b20"

BAD_HARNESS_VERDICTS = {
    Classification.INCONCLUSIVE,
    Classification.TIMEOUT,
    Classification.CRASH,
}


def _piton_at_least(commit: str) -> bool:
    root = Path(__file__).resolve().parents[2]
    try:
        exists = subprocess.run(
            ["git", "-C", str(root), "cat-file", "-e", f"{commit}^{{commit}}"],
            capture_output=True, timeout=15,
        )
        if exists.returncode != 0:
            return False
        ancestor = subprocess.run(
            ["git", "-C", str(root), "merge-base", "--is-ancestor", commit, "HEAD"],
            capture_output=True, timeout=15,
        )
        return ancestor.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@unittest.skipUnless(sandbox_available(), "bubblewrap is not available")
class IntegrationCanaries(unittest.TestCase):
    def compare_canary(self, letter):
        canary = CANARIES[f"Canary {letter}"]
        cpython = CPythonOracle(timeout=10).execute(canary.code, "canary.py")
        piton = PitonRunner(timeout=10, build_timeout=60).execute(canary.code, "canary.py")
        return DifferentialComparator().compare(canary.code, cpython, piton)

    def test_canary_a_lambda_is_equiv(self):
        result = self.compare_canary("A")
        self.assertEqual(result.classification, Classification.EQUIV)
        self.assertEqual(result.cpython.stdout, "6\n")
        self.assertEqual(result.piton.stdout, "6\n")

    @unittest.skipUnless(
        _piton_at_least(REQUIRED_PITON_COMMIT),
        f"requires PITON commit {REQUIRED_PITON_COMMIT[:7]} (mir: relax) in HEAD",
    )
    def test_canary_f_constructor_args_is_invalid_accepted(self):
        result = self.compare_canary("F")
        self.assertEqual(result.classification, Classification.INVALID_ACCEPTED)
        self.assertEqual(result.cpython.exception_type, "TypeError")
        self.assertTrue(result.piton.executed)
        self.assertEqual(result.piton.exit_code, 0)

    @unittest.skipUnless(
        _piton_at_least(REQUIRED_PITON_COMMIT),
        f"requires PITON commit {REQUIRED_PITON_COMMIT[:7]} (mir: relax) in HEAD",
    )
    def test_canary_c_super_is_unsupported_at_build(self):
        result = self.compare_canary("C")
        self.assertEqual(result.classification, Classification.UNSUPPORTED)
        self.assertEqual(result.piton.failed_stage, PitonStage.BUILD)
        self.assertTrue(result.cpython.succeeded)

    def test_canary_f_constructor_args_gets_real_verdict(self):
        result = self.compare_canary("F")
        self.assertEqual(result.cpython.exception_type, "TypeError")
        self.assertNotIn(result.classification, BAD_HARNESS_VERDICTS)

    def test_canary_c_super_multiarg_gets_real_verdict(self):
        result = self.compare_canary("C")
        self.assertTrue(result.cpython.succeeded)
        self.assertNotIn(result.classification, BAD_HARNESS_VERDICTS)
        self.assertIn(
            result.classification,
            {
                Classification.EQUIV,
                Classification.DIVERGENT,
                Classification.UNSUPPORTED,
            },
        )


if __name__ == "__main__":
    unittest.main()
