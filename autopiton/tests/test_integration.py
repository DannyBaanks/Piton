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
from autopiton.generator import BoundedConfig, ProgramGenerator
from autopiton.piton_runner import PitonRunner, PitonStage
from autopiton.sandbox import sandbox_available

CANARIES = {canary.description.split(":")[0]: canary
            for canary in ProgramGenerator().generate_canary_cases()}

BAD_HARNESS_VERDICTS = {
    Classification.INCONCLUSIVE,
    Classification.TIMEOUT,
    Classification.CRASH,
}


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

    def test_canary_f_constructor_args_is_invalid_accepted(self):
        result = self.compare_canary("F")
        self.assertEqual(result.classification, Classification.EQUIV)
        self.assertEqual(result.cpython.exception_type, "TypeError")
        self.assertTrue(result.piton.executed)
        self.assertEqual(result.piton.exception_type, "TypeError")
        self.assertNotEqual(result.piton.exit_code, 0)

    def test_canary_c_super_is_unsupported_at_build(self):
        result = self.compare_canary("C")
        self.assertEqual(result.classification, Classification.EQUIV)
        self.assertTrue(result.cpython.succeeded)
        self.assertEqual(result.cpython.stdout, "4\n")
        self.assertEqual(result.piton.stdout, "4\n")

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

    def test_regression_list_reversed_range_zero_element(self):
        code = "print(list(reversed(range(3))))\n"
        cpython = CPythonOracle(timeout=10).execute(code, "test_rev.py")
        piton = PitonRunner(timeout=10, build_timeout=60).execute(code, "test_rev.py")
        result = DifferentialComparator().compare(code, cpython, piton)
        self.assertEqual(result.classification, Classification.EQUIV)
        self.assertEqual(result.cpython.stdout, "[2, 1, 0]\n")
        self.assertEqual(result.piton.stdout, "[2, 1, 0]\n")

    def test_frontier_e_differential_execution_sample(self):
        gen = ProgramGenerator(seed=101)
        cfg = BoundedConfig(frontier="e")
        programs = gen.generate_bounded(count=5, config=cfg)
        comparator = DifferentialComparator()
        oracle = CPythonOracle(timeout=10)
        runner = PitonRunner(timeout=10, build_timeout=60)
        for prog in programs:
            cpython = oracle.execute(prog.code, "fe.py")
            piton = runner.execute(prog.code, "fe.py")
            res = comparator.compare(prog.code, cpython, piton)
            self.assertEqual(res.classification, Classification.EQUIV, f"Failed for {prog.description}")


if __name__ == "__main__":
    unittest.main()
