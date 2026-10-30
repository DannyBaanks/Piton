"""Minimizer tests with a synthetic oracle (no sandbox required)."""

import re
import unittest

from autopiton.comparator import Classification, ComparisonResult
from autopiton.cpython_oracle import CPythonResult
from autopiton.minimizer import FailureMinimizer, divergence_kind, iter_candidates
from autopiton.piton_runner import PitonResult, PitonStage


def make_comparison(source, classification, *, cpython_type=None, piton_stage=None,
                    piton_executed=False):
    cpython = CPythonResult(source_hash="synthetic")
    if cpython_type:
        cpython.status = "exception"
        cpython.exit_code = 1
        cpython.exception_type = cpython_type
    else:
        cpython.status = "ok"
        cpython.exit_code = 0
    piton = PitonResult(source_code=source)
    if piton_executed:
        piton.executed = True
        piton.stage_reached = PitonStage.EXECUTE
        piton.exit_code = 0
    if piton_stage is not None:
        piton.failed_stage = piton_stage
        piton.stage_reached = PitonStage.PARSE
    return ComparisonResult(
        source_code=source,
        cpython=cpython,
        piton=piton,
        classification=classification,
        reason="synthetic",
    )


def canary_f_oracle(source):
    """Models canary F: CPython raises TypeError, native PITON executes."""
    has_class = "class A" in source
    has_call_with_args = bool(re.search(r"A\((?!\))", source)) and "A(" in source
    if "A(" in source and not has_class:
        return make_comparison(source, Classification.INVALID_REFERENCE,
                               cpython_type="NameError", piton_stage=PitonStage.MIR)
    if has_call_with_args:
        return make_comparison(source, Classification.INVALID_ACCEPTED,
                               cpython_type="TypeError", piton_executed=True)
    return make_comparison(source, Classification.EQUIV, piton_executed=True)


class MinimizerTests(unittest.TestCase):
    def test_preserves_invalid_accepted_while_shrinking(self):
        source = "class A:\n    pass\n\nA(1, 2)\n"
        minimizer = FailureMinimizer(canary_f_oracle, max_evaluations=40)
        result = minimizer.minimize(source)
        self.assertTrue(result.changed)
        self.assertEqual(result.minimized_classification, "INVALID_ACCEPTED")
        self.assertIn("class A", result.minimized_source)
        self.assertLess(len(result.minimized_source), len(source))
        self.assertEqual(
            divergence_kind(canary_f_oracle(result.minimized_source)),
            result.preserved_kind,
        )

    def test_refuses_non_divergence(self):
        source = "print(1)\n"
        minimizer = FailureMinimizer(
            lambda code: make_comparison(code, Classification.EQUIV, piton_executed=True),
            max_evaluations=10,
        )
        result = minimizer.minimize(source)
        self.assertFalse(result.changed)
        self.assertEqual(result.attempts, 0)
        self.assertIn("not a preservable divergence", result.stopped_reason)

    def test_budget_is_respected(self):
        source = "class A:\n    pass\n\nA(1, 2)\n"
        minimizer = FailureMinimizer(canary_f_oracle, max_evaluations=1)
        result = minimizer.minimize(source)
        self.assertLessEqual(result.attempts, 1)

    def test_divergence_kind_includes_piton_stage(self):
        mir = make_comparison("x\n", Classification.UNSUPPORTED,
                              piton_stage=PitonStage.MIR)
        build = make_comparison("x\n", Classification.UNSUPPORTED,
                                piton_stage=PitonStage.BUILD)
        self.assertNotEqual(divergence_kind(mir), divergence_kind(build))

    def test_candidates_cover_the_structural_reductions(self):
        source = (
            "class A:\n"
            "    @property\n"
            "    def x(self):\n"
            "        return 1 + 2\n"
            "if True:\n"
            "    A()\n"
        )
        descriptions = " | ".join(description for description, _ in iter_candidates(source))
        for expected in (
            "normalize formatting",
            "remove If statement",
            "strip decorators",
            "collapse constant if",
            "fold constant arithmetic",
            "reduce constant",
        ):
            self.assertIn(expected, descriptions)


if __name__ == "__main__":
    unittest.main()
