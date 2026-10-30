"""Comparator controls: artificial divergences must never be classified EQUIV.

These tests use synthetic CPython/PITON results, so they run without the
sandbox. The real engines are exercised by the integration canaries.
"""

import unittest

from autopiton.comparator import Classification, DifferentialComparator
from autopiton.cpython_oracle import CPythonResult
from autopiton.piton_runner import PitonResult, PitonStage

SOURCE = "print(1)\n"


def cpython_ok(stdout="", stderr="", exit_code=0):
    return CPythonResult(
        source_hash="synthetic",
        status="ok",
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
    )


def cpython_exception(exc_type, message="", stdout="", exit_code=1):
    return CPythonResult(
        source_hash="synthetic",
        status="exception",
        exit_code=exit_code,
        stdout=stdout,
        exception_type=exc_type,
        exception_message=message,
    )


def piton_rejected(stage, error_type="UnsupportedFeature", message="nope"):
    order = [PitonStage.ADAPT, PitonStage.PARSE, PitonStage.MIR, PitonStage.BUILD]
    index = order.index(stage)
    reached = order[index - 1] if index > 0 else PitonStage.UNKNOWN
    return PitonResult(
        source_code=SOURCE,
        executed=False,
        stage_reached=reached,
        failed_stage=stage,
        stages={stage.value: {"error_type": error_type, "error_message": message}},
    )


def piton_executed(exit_code=0, stdout="", stderr="", exc_type=None, exc_message=None):
    return PitonResult(
        source_code=SOURCE,
        executed=True,
        stage_reached=PitonStage.EXECUTE,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        exception_type=exc_type,
        exception_message=exc_message,
    )


class ComparatorControls(unittest.TestCase):
    def setUp(self):
        self.comparator = DifferentialComparator()

    def classify(self, cpython, piton):
        return self.comparator.compare(SOURCE, cpython, piton).classification

    def test_identical_success_is_equiv(self):
        self.assertEqual(
            self.classify(cpython_ok("6\n"), piton_executed(stdout="6\n")),
            Classification.EQUIV,
        )

    def test_different_stdout_is_divergent(self):
        """Control 1: identical exits, different output must not be EQUIV."""
        self.assertEqual(
            self.classify(cpython_ok("6\n"), piton_executed(stdout="None\n")),
            Classification.DIVERGENT,
        )

    def test_empty_stdout_with_different_exit_is_divergent(self):
        """Control 2: empty output must not hide a failed execution."""
        self.assertEqual(
            self.classify(cpython_ok(""), piton_executed(exit_code=3)),
            Classification.DIVERGENT,
        )

    def test_different_stderr_is_divergent(self):
        self.assertEqual(
            self.classify(cpython_ok("x\n", ""), piton_executed(stdout="x\n", stderr="warning\n")),
            Classification.DIVERGENT,
        )

    def test_preexecute_rejection_after_reference_success_is_unsupported(self):
        self.assertEqual(
            self.classify(cpython_ok("ok\n"), piton_rejected(PitonStage.MIR)),
            Classification.UNSUPPORTED,
        )

    def test_preexecute_rejection_after_reference_failure_is_invalid_reference(self):
        self.assertEqual(
            self.classify(cpython_exception("TypeError", "bad"), piton_rejected(PitonStage.MIR)),
            Classification.INVALID_REFERENCE,
        )

    def test_reference_failure_accepted_is_invalid_accepted(self):
        self.assertEqual(
            self.classify(cpython_exception("TypeError", "A() takes no arguments"),
                          piton_executed(stdout="")),
            Classification.INVALID_ACCEPTED,
        )

    def test_same_exception_type_and_message_is_equiv(self):
        self.assertEqual(
            self.classify(cpython_exception("ValueError", "boom"),
                          piton_executed(exc_type="ValueError", exc_message="boom")),
            Classification.EQUIV,
        )

    def test_different_exception_types_is_divergent(self):
        """Control 3: exception type must never be ignored."""
        self.assertEqual(
            self.classify(cpython_exception("ValueError", "boom"),
                          piton_executed(exc_type="ZeroDivisionError", exc_message="boom")),
            Classification.DIVERGENT,
        )

    def test_same_exception_type_different_message_is_divergent(self):
        self.assertEqual(
            self.classify(cpython_exception("ValueError", "boom"),
                          piton_executed(exc_type="ValueError", exc_message="bam")),
            Classification.DIVERGENT,
        )

    def test_timeout_is_timeout(self):
        cpython = cpython_ok()
        cpython.timed_out = True
        self.assertEqual(self.classify(cpython, piton_executed()), Classification.TIMEOUT)

    def test_crash_is_crash(self):
        piton = piton_executed()
        piton.crash = True
        self.assertEqual(self.classify(cpython_ok(), piton), Classification.CRASH)

    def test_harness_failure_is_inconclusive(self):
        piton = PitonResult(source_code=SOURCE, failed_stage=PitonStage.HARNESS)
        self.assertEqual(self.classify(cpython_ok(), piton), Classification.INCONCLUSIVE)

    def test_memory_addresses_are_normalised(self):
        self.assertEqual(
            self.classify(cpython_exception("ValueError", "object at 0x7f00aa"),
                          piton_executed(exc_type="ValueError", exc_message="object at 0xdeadbe")),
            Classification.EQUIV,
        )

    def test_controls_have_teeth_against_a_broken_comparator(self):
        """A comparator that blindly returns EQUIV fails these controls."""
        controls = [
            (cpython_ok("6\n"), piton_executed(stdout="None\n")),
            (cpython_ok(""), piton_executed(exit_code=3)),
            (cpython_exception("ValueError", "boom"),
             piton_executed(exc_type="TypeError", exc_message="boom")),
            (cpython_ok("ok\n"), piton_rejected(PitonStage.MIR)),
            (cpython_exception("TypeError", "x"), piton_executed(stdout="")),
        ]
        classifications = [self.classify(cpython, piton) for cpython, piton in controls]
        self.assertTrue(all(item != Classification.EQUIV for item in classifications))
        self.assertGreaterEqual(len(set(classifications)), 3)

        class BrokenComparator(DifferentialComparator):
            def compare(self, source_code, cpython=None, piton=None):
                result = super().compare(source_code, cpython, piton)
                result.classification = Classification.EQUIV
                return result

        broken = BrokenComparator()
        for cpython, piton in controls:
            self.assertEqual(
                broken.compare(SOURCE, cpython, piton).classification,
                Classification.EQUIV,
            )


if __name__ == "__main__":
    unittest.main()
