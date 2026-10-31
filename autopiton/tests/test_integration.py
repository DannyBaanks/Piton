"""End-to-end integration: real CPython oracle + real PITON native pipeline.

Runs only when bubblewrap is available. These are M0 canaries executed through
exactly the same objects the CLI uses.

The assertions deliberately validate the *AutoPiton harness contract*, not a
historical PITON commit. A squash/rebase must not silently disable coverage.
PITON may legitimately move from UNSUPPORTED/INVALID_ACCEPTED to a better
classification as its semantics improve; these tests require a real,
non-harness verdict with the expected CPython reference behavior.
"""

import unittest

from autopiton.comparator import Classification, DifferentialComparator
from autopiton.cpython_oracle import CPythonOracle
from autopiton.generator import ProgramGenerator
from autopiton.piton_runner import PitonRunner
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
