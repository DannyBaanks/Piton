"""Differential coverage for literal-source native timeit timers."""
from __future__ import annotations

import unittest

import pytest

from piton.native_differential import compare_native_to_cpython
from piton.x86 import NativeBuildError


CASES = {
    "timer_timeit_type": (
        "desde timeit importar Timer\n"
        "t = Timer('a = 1')\n"
        "v = t.timeit(number=10)\n"
        "imprimir(type(v).__name__, v >= 0)\n"
    ),
    "timeit_func": (
        "importar timeit\n"
        "v = timeit.timeit('x = 2 + 2', number=5)\n"
        "imprimir(isinstance(v, float))\n"
    ),
    "tutorial_chunk": (
        "desde timeit importar Timer\n"
        "Timer('t=a; a=b; b=t', 'a=1; b=2').timeit()\n"
        "Timer('a,b = b,a', 'a=1; b=2').timeit()\n"
        "imprimir('done')\n"
    ),
}


def _assert_matches(testcase: unittest.TestCase, source: str) -> None:
    result = compare_native_to_cpython(source)
    testcase.assertEqual(
        result.native.returncode,
        result.oracle.returncode,
        f"returncode mismatch\nnative stderr: {result.native.stderr!r}\noracle stderr: {result.oracle.stderr!r}",
    )
    testcase.assertEqual(
        result.native.stdout,
        result.oracle.stdout,
        f"stdout mismatch\nnative stderr: {result.native.stderr!r}\noracle stderr: {result.oracle.stderr!r}",
    )


class TimeitTimerNativeParity(unittest.TestCase):
    def test_task_reproducers(self):
        for source in CASES.values():
            with self.subTest(source=source):
                _assert_matches(self, source)

    def test_timer_setup_runs_once_and_stmt_runs_number_times(self):
        _assert_matches(
            self,
            "desde timeit importar Timer\n"
            "timer = Timer(\"print('stmt')\", \"print('setup')\")\n"
            "elapsed = timer.timeit(number=3)\n"
            "imprimir(type(elapsed).__name__, elapsed >= 0)\n",
        )

    def test_module_timeit_negative_count_and_invalid_number(self):
        _assert_matches(
            self,
            "desde timeit importar Timer\n"
            "elapsed = Timer(\"print('stmt')\", \"print('setup')\").timeit(-2)\n"
            "imprimir(type(elapsed).__name__, elapsed >= 0)\n"
            "intentar:\n"
            "    Timer('pass').timeit(2.5)\n"
            "excepto TypeError:\n"
            "    imprimir('TypeError')\n",
        )

    def test_nonliteral_source_fails_closed_at_native_build_time(self):
        with pytest.raises(
            NativeBuildError,
            match="native Timer.timeit requires literal stmt and setup source strings",
        ):
            compare_native_to_cpython(
                "desde timeit importar Timer\n"
                "stmt = 'pass'\n"
                "Timer(stmt).timeit(1)\n"
            )

    def test_non_string_source_fails_closed_at_native_build_time(self):
        with pytest.raises(
            NativeBuildError,
            match="native Timer.timeit requires literal stmt and setup source strings",
        ):
            compare_native_to_cpython(
                "desde timeit importar Timer\n"
                "Timer(None).timeit(1)\n"
            )

    def test_dynamic_namespace_source_fails_closed_at_native_build_time(self):
        with pytest.raises(
            NativeBuildError,
            match="timeit source uses unsupported dynamic namespace access: globals",
        ):
            compare_native_to_cpython(
                "desde timeit importar Timer\n"
                "Timer(\"print(globals())\").timeit(1)\n"
            )

    def test_unsupported_timeit_arguments_fail_closed(self):
        with pytest.raises(
            NativeBuildError,
            match="native Timer.timeit supports only the number argument",
        ):
            compare_native_to_cpython(
                "desde timeit importar Timer\n"
                "Timer('pass').timeit(1, extra=2)\n"
            )
        with pytest.raises(
            NativeBuildError,
            match="native timeit.timeit supports only stmt, setup, and number arguments",
        ):
            compare_native_to_cpython(
                "importar timeit\n"
                "timeit.timeit('pass', extra=2)\n"
            )


if __name__ == "__main__":
    unittest.main()
