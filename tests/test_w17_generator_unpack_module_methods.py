"""Parity checks for unpacked calls in generators and module-name shadowing."""
from __future__ import annotations

import unittest
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from piton.native_differential import compare_native_to_cpython
from piton.x86 import NativeBuildError
from tools.parity_corpus_sec import python_to_piton


def _piton_source(source: str) -> str:
    return python_to_piton(source).replace("nonlocal", "no_local")


def _assert_matches(testcase: unittest.TestCase, source: str) -> None:
    result = compare_native_to_cpython(_piton_source(source))
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


class GeneratorUnpackModuleMethods(unittest.TestCase):
    def test_generator_star_kwargs_resume_and_route_exceptions(self):
        _assert_matches(
            self,
            "def combine(left, right, offset):\n"
            "    return left * 10 + right + offset\n"
            "def fixed_text(left, right):\n"
            "    return 'typed string result'\n"
            "def fail(left, right):\n"
            "    raise ValueError('callee failed')\n"
            "def pair(*values):\n"
            "    return values[0] * 10 + values[1]\n"
            "def fail_many(*values):\n"
            "    raise KeyError('variadic callee failed')\n"
            "def expanded(parts):\n"
            "    yield 'started'\n"
            "    yield combine(*parts, offset=1)\n"
            "    yield pair(*parts)\n"
            "    yield fixed_text(*parts)\n"
            "    yield combine(**{'right': 4, 'left': 2, 'offset': 3})\n"
            "def invalid(parts):\n"
            "    yield 'started'\n"
            "    try:\n"
            "        yield combine(*parts)\n"
            "    except TypeError:\n"
            "        yield 'bad unpack caught'\n"
            "def callee_error(parts):\n"
            "    yield 'started'\n"
            "    try:\n"
            "        yield fail(*parts)\n"
            "    except ValueError:\n"
            "        yield 'callee error caught'\n"
            "def variadic_callee_error(parts):\n"
            "    yield 'started'\n"
            "    try:\n"
            "        yield fail_many(*parts)\n"
            "    except KeyError:\n"
            "        yield 'variadic error caught'\n"
            "print(list(expanded([3, 7])))\n"
            "print(list(invalid(17)))\n"
            "print(list(callee_error(['x', 'y'])))\n"
            "print(list(variadic_callee_error(['x', 'y'])))\n",
        )

    def test_local_decimal_name_shadows_imported_module_for_replace(self):
        _assert_matches(
            self,
            "from decimal import Decimal\n"
            "def remove_separator(value):\n"
            "    decimal = value\n"
            "    return decimal.replace('_', '')\n"
            "print(remove_separator('1_234'))\n",
        )

    def test_generator_unpack_with_unknown_kwargs_mapping_fails_closed(self):
        source = (
            "def target(value):\n"
            "    return value\n"
            "def values(mapping):\n"
            "    yield target(**mapping)\n"
            "print(list(values({'value': 7})))\n"
        )
        with pytest.raises(
            NativeBuildError,
            match="CALL_UNPACKING_DYNAMIC4_V1: \\*\\* operand must be a dict",
        ):
            compare_native_to_cpython(_piton_source(source))


if __name__ == "__main__":
    unittest.main()
