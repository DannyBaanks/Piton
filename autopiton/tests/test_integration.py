"""End-to-end integration: real CPython oracle + real PITON native pipeline.

Runs only when bubblewrap is available. These are the M0 canaries, executed
through the same objects the CLI uses.
"""

import unittest

from autopiton.comparator import Classification, DifferentialComparator
from autopiton.cpython_oracle import CPythonOracle
from autopiton.generator import ProgramGenerator
from autopiton.piton_runner import PitonRunner, PitonStage
from autopiton.sandbox import sandbox_available

CANARIES = {canary.description.split(":")[0]: canary
            for canary in ProgramGenerator().generate_canary_cases()}


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
        self.assertEqual(result.classification, Classification.INVALID_ACCEPTED)
        self.assertEqual(result.cpython.exception_type, "TypeError")
        self.assertTrue(result.piton.executed)
        self.assertEqual(result.piton.exit_code, 0)

    def test_canary_c_super_is_unsupported_at_build(self):
        result = self.compare_canary("C")
        self.assertEqual(result.classification, Classification.UNSUPPORTED)
        self.assertEqual(result.piton.failed_stage, PitonStage.BUILD)
        self.assertTrue(result.cpython.succeeded)


if __name__ == "__main__":
    unittest.main()
