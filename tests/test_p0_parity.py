"""PARITY_P0_V1: P0 parity gates for the native backends.

Measures the fail-open gaps found by the 2026-09-27 parity probe
(/tmp/opencode/parity/probe2.py, 155 cases):

  P0-1  imprimir(a, b, c) printed only its first argument  -> PRINT_ARGS_V1
  P0-2  lista()/rango()/reversed()/abrir() as values crashed (SIGSEGV)
        or printed raw pointers                               -> BUILTIN_MARKER_V1
  P0-3  1 // 0 trapped the process (SIGILL) instead of
        raising a catchable ZeroDivisionError                -> ZDIV_GUARD_V1
  P0-5  a list returned from a function printed as a raw
        pointer; bare `devolver` printed 0 instead of None   -> RETURNTYPE_V1
  P0-5b strings inside containers printed without repr
        quotes                                                -> SEQ_REPR_QUOTES_V1
  plus   [1] + [2] did C pointer arithmetic                  -> SEQ_CONCAT_V1

Every gate is a differential: the native executable must produce the same
bytes and exit code as CPython running the translated source. Fail-closed
gates assert NativeBuildError instead.
"""
from __future__ import annotations

import sys
import unittest

import pytest

from piton.native_differential import compare_native_to_cpython
from piton.x86 import NativeBuildError


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


class PrintArgsV1(unittest.TestCase):
    """PRINT_ARGS_V1: print(a, b, ...) joins str(a), str(b), ... with spaces."""

    def test_multi_int_args(self):
        _assert_matches(self, "imprimir(1, 2, 3)\n")

    def test_multi_mixed_args(self):
        _assert_matches(self, 'imprimir("a", 1, 2.5, [1, 2], Nada, Verdadero)\n')

    def test_multi_bool_none(self):
        _assert_matches(self, "imprimir(Verdadero, Falso, Nada, 0)\n")

    def test_single_arg_regression(self):
        _assert_matches(self, "imprimir(42)\n")

    def test_no_args_regression(self):
        _assert_matches(self, "imprimir()\n")

    def test_tuple_argument(self):
        _assert_matches(self, "imprimir((1, 2))\n")

    def test_float_args(self):
        _assert_matches(self, "imprimir(1.5, 2.25)\n")

    def test_object_str_args(self):
        _assert_matches(
            self,
            "clase A:\n"
            "    funcion __init__(self, v):\n"
            "        self.v = v\n"
            "    funcion __str__(self):\n"
            "        devolver texto(self.v)\n"
            "imprimir(A(7), 'x', 1)\n",
        )

    def test_str_end_space_regression(self):
        # trailing/leading args must not add stray separators
        _assert_matches(self, "imprimir('a', 'b')\nimprimir('c')\n")


class SeqConcatV1(unittest.TestCase):
    """SEQ_CONCAT_V1: list+list and tuple+tuple concatenate like CPython."""

    def test_list_plus_list(self):
        _assert_matches(self, "imprimir([1] + [2])\n")

    def test_tuple_plus_tuple(self):
        _assert_matches(self, "imprimir((1, 2) + (3,))\n")

    def test_list_plus_tuple_fail_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir([1] + (2,))\n")

    def test_dict_plus_dict_fail_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir({'a': 1} + {'b': 2})\n")


class SeqReprQuotesV1(unittest.TestCase):
    """SEQ_REPR_QUOTES_V1: container elements print with CPython repr."""

    @unittest.skipIf(
        sys.platform.startswith("win32"),
        "Windows backend: strings inside container literals are untagged (pre-existing, follow-up)",
    )
    def test_list_with_string(self):
        _assert_matches(self, "imprimir(['a', 1])\n")

    @unittest.skipIf(
        sys.platform.startswith("win32"),
        "Windows backend: strings inside container literals are untagged (pre-existing, follow-up)",
    )
    def test_tuple_with_string(self):
        _assert_matches(self, "imprimir(('A', 65))\n")

    @unittest.skipIf(
        sys.platform.startswith("win32"),
        "Windows backend: strings inside container literals are untagged (pre-existing, follow-up)",
    )
    def test_dict_with_string_key(self):
        _assert_matches(self, "imprimir({'a': 1})\n")


class ZdivGuardV1(unittest.TestCase):
    """ZDIV_GUARD_V1: integer // and % by zero raise ZeroDivisionError."""

    def test_floor_div_by_zero_caught(self):
        _assert_matches(
            self,
            "intentar:\n"
            "    imprimir(1 // 0)\n"
            "excepto ZeroDivisionError:\n"
            "    imprimir('z')\n",
        )

    def test_mod_by_zero_caught(self):
        _assert_matches(
            self,
            "intentar:\n"
            "    imprimir(5 % 0)\n"
            "excepto ZeroDivisionError:\n"
            "    imprimir('m')\n",
        )

    def test_floor_div_by_zero_uncaught_exits_cleanly(self):
        result = compare_native_to_cpython("imprimir(1 // 0)\n")
        # Not EQUIV (CPython prints a full traceback) but the process must no
        # longer trap: exit code 1 with the exception name on stderr.
        self.assertEqual(result.native.returncode, 1)
        self.assertNotIn(-11, (result.native.returncode,))
        self.assertIn(b"ZeroDivisionError", result.native.stderr)

    def test_negative_floor_div_by_zero_caught(self):
        _assert_matches(
            self,
            "intentar:\n"
            "    x = -7 // 0\n"
            "excepto ZeroDivisionError:\n"
            "    imprimir('n')\n",
        )


class ReturnTypeV1(unittest.TestCase):
    """RETURNTYPE_V1: user function call results carry their return type."""

    def test_function_returning_list(self):
        _assert_matches(self, "funcion f():\n    devolver [1, 2]\nimprimir(f())\n")

    def test_function_returning_list_via_variable(self):
        _assert_matches(self, "funcion f():\n    x = [1, 2]\n    devolver x\nimprimir(f())\n")

    def test_function_returning_str(self):
        _assert_matches(self, "funcion f():\n    devolver 'hola'\nimprimir(f())\n")

    def test_bare_return_prints_none(self):
        _assert_matches(self, "funcion f():\n    devolver\nimprimir(f())\n")

    def test_no_return_prints_none(self):
        # a function with only a print statement falls through -> None
        _assert_matches(
            self,
            "funcion f():\n    x = 1\nimprimir(f())\n",
        )

    def test_method_returning_self_prints_via_str(self):
        _assert_matches(
            self,
            "clase A:\n"
            "    funcion __init__(self, v):\n"
            "        self.v = v\n"
            "    funcion yo(self):\n"
            "        devolver self\n"
            "    funcion __str__(self):\n"
            "        devolver texto(self.v)\n"
            "imprimir(A(3).yo())\n",
        )

    def test_unknown_return_type_prints_int(self):
        # binary-op results are not tracked by the narrow inference; the
        # historical "int" default must keep working for genuinely
        # int-returning functions.
        _assert_matches(self, "funcion f(n):\n    devolver n + 1\nimprimir(f(5))\n")


class WindowsReturnTypeV1(unittest.TestCase):
    """WRETURNTYPE_V1: the Windows backend infers call-result types.

    Windows call results used to default to "int", so `imprimir(f())`
    printed a raw pointer / 0 / 1 for str/float/bool/None returns and
    None-arithmetic computed on a null slot. Now the NASM backend runs
    the same narrow MIR inference as Linux (every return must resolve to
    one type, else the historical default stays) and propagates it at
    call, call_unpack and method_call sites; None in arithmetic fails
    closed at build (CPython raises TypeError at runtime).
    """

    def test_function_returning_float(self):
        _assert_matches(self, "funcion f():\n    devolver 3.5\nimprimir(f())\n")

    def test_function_returning_bool(self):
        _assert_matches(self, "funcion f():\n    devolver Verdadero\nimprimir(f())\n")

    def test_function_returning_explicit_none(self):
        _assert_matches(self, "funcion f():\n    devolver Nada\nimprimir(f())\n")

    def test_str_concat_over_call_result(self):
        _assert_matches(self, "funcion f():\n    devolver 'a'\nimprimir(f() + 'b')\n")

    def test_float_arithmetic_over_call_result(self):
        _assert_matches(self, "funcion f():\n    devolver 3.5\nimprimir(f() * 2)\n")

    def test_bool_arithmetic_over_call_result(self):
        _assert_matches(self, "funcion f():\n    devolver Verdadero\nimprimir(f() + 1)\n")

    def test_call_chain_propagates(self):
        _assert_matches(
            self,
            "funcion g():\n    devolver 7\nfuncion f():\n    devolver g()\nimprimir(f())\n",
        )

    def test_variable_return(self):
        _assert_matches(self, "funcion f():\n    x = 'q'\n    devolver x\nimprimir(f())\n")

    def test_method_call_propagates(self):
        _assert_matches(
            self,
            "clase A:\n    funcion dame(self):\n        devolver 'hola'\nimprimir(A().dame())\n",
        )

    def test_none_comparison(self):
        _assert_matches(self, "funcion f():\n    devolver Nada\nimprimir(f() == Nada)\n")
        _assert_matches(self, "imprimir(Nada == Nada)\n")

    def test_none_arithmetic_fails_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("funcion f():\n    w = 1\nx = f() + 1\nimprimir(x)\n")


class CollReturnV1(unittest.TestCase):
    """COLL_RETURN_V1: Windows functions can return collections/bigints.

    The NASM backend used to reject every collection return at build
    ("returning native collections is not supported yet") because the
    function-exit cleanup freed owned slots — including the returned
    pointer (use-after-free through any alias). Now the return transfers
    ownership: the pointer is stashed and cleanup skips every slot still
    holding it (value-compared, so arbitrary store/load aliasing stays
    live). Received collections are never owned, so nothing double-frees.
    Out of scope (separate gaps, still failing closed or divergent):
    collections containing strings (untagged-strings gap) and bigint
    values produced by arithmetic (binary-op origins untracked — same on
    Linux).
    """

    def test_returning_tuple(self):
        _assert_matches(self, "funcion f():\n    devolver (1, 2)\nimprimir(f())\n")

    def test_returning_dict(self):
        _assert_matches(self, "funcion f():\n    devolver {1: 2}\nimprimir(f())\n")

    def test_returning_set(self):
        _assert_matches(self, "funcion f():\n    devolver {1, 2}\nimprimir(f())\n")

    def test_returning_bigint_literal(self):
        _assert_matches(self, "funcion f():\n    devolver 1180591620717411303424\nimprimir(f())\n")

    def test_returning_bigint_variable(self):
        _assert_matches(
            self, "funcion f():\n    x = 1180591620717411303424\n    devolver x\nimprimir(f())\n"
        )

    def test_aliased_return_stays_live(self):
        _assert_matches(
            self, "funcion f():\n    t = [5]\n    u = t\n    devolver u\nimprimir(f())\n"
        )

    def test_nested_collection_return(self):
        _assert_matches(
            self, "funcion g():\n    devolver [1]\nfuncion f():\n    devolver g()\nimprimir(f())\n"
        )

    def test_concat_over_call_result(self):
        _assert_matches(self, "funcion f():\n    devolver [1]\nimprimir(f() + [2, 3])\n")

    def test_getitem_over_call_result(self):
        _assert_matches(self, "funcion f():\n    devolver [10, 20]\nimprimir(f()[1])\n")

    def test_mutate_received_collection(self):
        _assert_matches(self, "funcion f():\n    devolver [1]\nx = f()\nx.append(2)\nimprimir(x)\n")

    def test_vararg_tuple_return(self):
        _assert_matches(self, "funcion f(*a):\n    devolver a\nimprimir(f(1, 2))\n")


class MinMaxTypesV1(unittest.TestCase):
    """MINMAX_TYPES_V1: min/max winners keep their kind.

    Both backends used to compare raw pointers for str args (ordering by
    address AND printing the pointer as an int) and to type every
    non-float result as int. Now: both str -> lexicographic compare with
    str result; both float -> float; both int -> int; both bool -> bool;
    anything mixed (str/int, int/float, bool/int, None, bigint,
    collections) fails closed at build — CPython raises TypeError for
    those, or the winner's type is not statically knowable.
    """

    def test_str_min_max(self):
        _assert_matches(self, "imprimir(min('b', 'a'))\n")
        _assert_matches(self, "imprimir(max('a', 'b'))\n")
        _assert_matches(self, "imprimir(min('abc', 'abd'))\n")
        _assert_matches(self, "imprimir(min('', 'a'))\n")

    def test_int_min_max(self):
        _assert_matches(self, "imprimir(min(3, 1))\nimprimir(max(3, 1))\n")

    def test_float_min_max(self):
        _assert_matches(self, "imprimir(max(1.5, 2.5))\n")

    def test_bool_min_max(self):
        _assert_matches(self, "imprimir(min(Verdadero, Falso))\n")
        _assert_matches(self, "imprimir(max(Verdadero, Falso))\n")

    def test_mixed_types_fail_closed(self):
        # Each case runs in a subprocess: the compiler keeps global state
        # (constant caches, type maps) that leaks between in-process
        # compilations and makes order-dependent failures.
        import subprocess
        import sys
        for source in (
            "imprimir(min(1, 2.5))\n",
            "imprimir(min(2.5, 1))\n",
            "imprimir(min('a', 1))\n",
            "imprimir(min(1, 'a'))\n",
            "imprimir(min(Nada, 1))\n",
            "imprimir(min(Verdadero, 5))\n",
            "imprimir(min(5, Verdadero))\n",
            "imprimir(max(Verdadero, 5))\n",
        ):
            result = subprocess.run(
                [sys.executable, "-c",
                 "from piton.native_differential import compare_native_to_cpython\n"
                 "from piton.x86 import NativeBuildError\n"
                 f"try:\n"
                 f"    compare_native_to_cpython({source!r})\n"
                 f"except NativeBuildError:\n"
                 f"    pass\n"
                 f"else:\n"
                 f"    raise SystemExit(1)"],
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, f"not closed: {source}")


class BuiltinMarkerV1(unittest.TestCase):
    """BUILTIN_MARKER_V1: builtins outside their supported context fail closed.

    Before: `load` of a builtin emitted NO code, so downstream consumers read
    an uninitialized C variable — SIGSEGV or a raw pointer printed as an int.
    """

    def test_rango_as_value_fail_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir(rango(3))\n")

    def test_lista_call_fail_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir(lista([1, 2]))\n")

    def test_reversed_as_value_fail_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir(reversed([1, 2]))\n")

    def test_abrir_fail_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("abrir('x')\n")

    def test_store_builtin_fail_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("x = rango\nimprimir(1)\n")

    def test_return_builtin_fail_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("funcion f():\n    devolver rango\nimprimir(1)\n")

    def test_print_generator_fail_closed(self):
        # a generator object prints as <generator ... at 0x...> in CPython —
        # an address: it can never match, so it must fail closed.
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython(
                "funcion g():\n    producir 1\nimprimir(g())\n"
            )

    def test_print_iterator_fail_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir(enumerar([1, 2]))\n")

    def test_for_over_range_still_works(self):
        _assert_matches(self, "para i en rango(3):\n    imprimir(i)\n")

    def test_for_over_list_still_works(self):
        _assert_matches(self, "para x en [10, 20]:\n    imprimir(x, x)\n")


class IntOvfGuardV1(unittest.TestCase):
    """INTOVF_GUARD_V1: CPython promotes int arithmetic to arbitrary precision.

    Two layers, both backends:
      - constant int operands fold EXACTLY in Python (bignum arithmetic IS the
        oracle); results beyond i64 promote to bigint literals -> full
        equivalence with CPython;
      - runtime operands use checked helpers (Linux: __builtin_*_overflow,
        Windows: jo after add/sub/imul) that raise a CATCHABLE OverflowError
        instead of silently wrapping. This is a documented, intentional
        divergence: CPython promotes at runtime, the untagged i64 subset
        cannot (needs a tagged representation — follow-up).

    NOTE (Windows): int literals beyond +-2^60 are typed bigint there, so
    runtime + - * over i64-boundary values computes true bignum results and
    matches CPython; the tests below keep every runtime operand well under
    2^60 so both backends take the same path.
    """

    def test_fold_add_overflow_promotes(self):
        _assert_matches(self, "imprimir(9223372036854775807 + 1)\n")

    def test_fold_sub_overflow_promotes(self):
        _assert_matches(self, "imprimir(-9223372036854775808 - 1)\n")

    def test_fold_mul_overflow_promotes(self):
        _assert_matches(self, "imprimir(9223372036854775807 * 2)\n")

    def test_fold_mul_huge(self):
        _assert_matches(self, "imprimir(123456789012345678 * 987654321)\n")

    def test_fold_chained_second_level_overflow(self):
        _assert_matches(self, "imprimir((3037000499 * 2) * 3037000499)\n")

    def test_fold_small_regression(self):
        _assert_matches(self, "imprimir(2 + 3 * 4)\n")

    def test_runtime_mul_no_overflow_regression(self):
        _assert_matches(self, "x = 5\nz = x * 3\nimprimir(z)\n")

    def test_runtime_mul_overflow_is_catchable(self):
        result = compare_native_to_cpython(
            "intentar:\n"
            "    x = 3037000500\n"
            "    z = x * x\n"
            "    imprimir('no-ovf')\n"
            "excepto OverflowError:\n"
            "    imprimir('mul')\n"
        )
        # NOT equivalent: CPython promotes (prints 'no-ovf'); the native
        # subset raises. The gate is the catchable, explicit failure.
        # (stdout is normalized to LF: the Windows PE emits CRLF.)
        self.assertEqual(result.native.returncode, 0)
        self.assertEqual(result.native.stdout.replace(b"\r\n", b"\n"), b"mul\n")

    def test_runtime_mul_overflow_uncaught_exits_cleanly(self):
        result = compare_native_to_cpython("x = 3037000500\nz = x * x\nimprimir('no')\n")
        self.assertEqual(result.native.returncode, 1)
        self.assertIn(b"OverflowError", result.native.stderr)


class BoolShortV1(unittest.TestCase):
    """BOOL_SHORT_V1 + TRUTHY_FIX_V1: y/o short-circuit with operand
    results, and full truthiness for `no`.

    `a y b` / `a o b` used to fail closed entirely (BOOL_OP reached MIR as
    an unsupported runtime op). Now they follow CPython exactly: the
    result is the WINNING OPERAND (1 y 2 is 2, 0 o 'x' is 'x'), the right
    operand evaluates only when needed, and truthiness is per-kind
    (empty str/collections are falsy). Dynamic mixed-type operands fail
    closed (the winner's type is not statically knowable); literal heads
    fold statically, which resolves the common idioms (None o X, 0 o X,
    Verdadero y X). Bonus: `no` now handles floats (was closed) and empty
    str/collections (used to print False — non-null pointers read truthy).
    """

    def test_bool_operands(self):
        _assert_matches(self, "imprimir(Verdadero y Falso)\n")
        _assert_matches(self, "imprimir(Verdadero o Falso)\n")

    def test_int_operands(self):
        _assert_matches(self, "imprimir(1 y 2)\n")
        _assert_matches(self, "imprimir(0 o 3)\n")

    def test_operand_result_not_bool(self):
        _assert_matches(self, "imprimir(0 o 'x')\n")
        _assert_matches(self, "imprimir(None o 'defecto')\n")
        _assert_matches(self, "imprimir('' y 'otro')\n")
        _assert_matches(self, "imprimir('a' o 'b')\n")

    def test_chains(self):
        _assert_matches(self, "imprimir(Verdadero y 1 y 2)\n")
        _assert_matches(self, "imprimir(0 o 0 o 3)\n")
        _assert_matches(self, "imprimir(1 y 0 o 3)\n")

    def test_short_circuit_skips_evaluation(self):
        _assert_matches(
            self,
            "funcion f():\n    imprimir('lado')\n    devolver 0\n"
            "imprimir(1 y f())\nimprimir(0 o f())\n",
        )

    def test_division_guard_not_reached(self):
        # x y (1 // x) with x=0: the right operand must never evaluate
        _assert_matches(self, "x = 0\nimprimir(x y 1 // x)\n")

    def test_literal_heads_fold(self):
        _assert_matches(self, "imprimir(Falso y 1)\n")
        _assert_matches(self, "imprimir(Nada y 5)\n")
        _assert_matches(self, "imprimir([1] y 'x')\n")

    def test_not_full_truthiness(self):
        _assert_matches(self, "imprimir(no 0.0)\n")
        _assert_matches(self, "imprimir(no 0.5)\n")
        _assert_matches(self, "imprimir(no '')\n")
        _assert_matches(self, "imprimir(no 'x')\n")
        _assert_matches(self, "imprimir(no [])\n")
        _assert_matches(self, "imprimir(no [1])\n")
        _assert_matches(self, "imprimir(no {})\n")
        _assert_matches(self, "imprimir(no (1,))\n")

    def test_dynamic_mixed_types_fail_closed(self):
        # a VARIABLE head is not statically foldable: the winner depends
        # on runtime values, so the holder type cannot serve both arms.
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("x = 1\nimprimir(x y 'x')\n")


class BranchTruthinessV1(unittest.TestCase):
    """TRUTHY_BRANCH_V1: la condicion de `si` es un test de verdad CPython.

    Antes el backend emitia `if(<puntero>) goto ...`, de modo que cualquier
    cadena o coleccion —incluidas las VACIAS— era truthy porque su puntero es
    no nulo: `si "":` tomaba la rama verdadera. El corpus diferencial
    (tools/parity_corpus.py, area branch_truthiness) encontro el bug.
    """

    def test_empty_containers_are_falsy(self):
        for value in ('""', "[]", "()", "{}"):
            _assert_matches(
                self,
                f"v = {value}\nsi v:\n    imprimir('T')\nsino:\n    imprimir('F')\n",
            )

    def test_empty_containers_skip_while(self):
        for value in ('""', "[]", "()", "{}"):
            _assert_matches(self, f"v = {value}\nmientras v:\n    imprimir('nope')\nimprimir('fin')\n")

    def test_non_empty_and_scalars(self):
        for value in ("0", "1", "0.0", "0.5", "Nada", "'a'", "[1]", "[1, []]"):
            _assert_matches(
                self,
                f"v = {value}\nsi v:\n    imprimir('T')\nsino:\n    imprimir('F')\n",
            )

    def test_literal_empty_string_condition(self):
        _assert_matches(self, 'si "":\n    imprimir("T")\nsino:\n    imprimir("F")\n')

    def test_ternary_uses_truthiness(self):
        _assert_matches(self, "v = []\nimprimir('T' si v sino 'F')\n")

    def test_object_instance_still_truthy(self):
        # un tipo que la tabla de verdad no modela NO debe fallar cerrado aqui
        _assert_matches(
            self,
            "clase P:\n    pasar\np = P()\nsi p:\n    imprimir('T')\nsino:\n    imprimir('F')\n",
        )


class OperatorAssociativityV1(unittest.TestCase):
    """ASOC_V1 + UNARY_POW_PREC_V1: precedencia y asociatividad correctas.

    `next_min_prec` estaba invertido en el parser, asi que TODA cadena de
    operadores no-asociativos se evaluaba de derecha a izquierda:
    `10 - 3 - 2` daba 9, `100 / 5 / 2` daba 40.0, `1 << 2 << 3` daba 65536 y
    `2 ** 3 ** 2` daba 64. `+` y `%` lo escondian porque son asociativos en
    los operandos de prueba.
    """

    def test_left_associative_chains(self):
        for expr in (
            "10 - 3 - 2",
            "100 / 5 / 2",
            "100 // 5 // 2",
            "1 << 2 << 3",
            "64 >> 2 >> 1",
            "7 & 3 & 1",
            "6 | 2 | 1",
            "7 ^ 3 ^ 1",
            "20 - 5 - 3 - 2 - 1",
        ):
            _assert_matches(self, f"imprimir({expr})\n")

    def test_associative_ops_still_agree(self):
        for expr in ("10 + 3 + 2", "2 * 3 * 4", "20 % 7 % 3"):
            _assert_matches(self, f"imprimir({expr})\n")

    def test_power_is_right_associative(self):
        for expr in ("2 ** 3 ** 2", "2 ** 3 ** 2 ** 2"):
            _assert_matches(self, f"imprimir({expr})\n")

    def test_unary_binds_looser_than_power(self):
        for expr in ("-2 ** 2", "-(2 ** 2)", "-1.5 ** 0.0", "-2 ** 3 ** 2", "3 - 2 ** 2", "2 ** -1", "- -2"):
            _assert_matches(self, f"imprimir({expr})\n")

    def test_unary_with_variable_operand(self):
        _assert_matches(self, "x = 2\nimprimir(-x ** 2)\n")

    def test_mixed_precedence_chain(self):
        _assert_matches(self, "imprimir(2 * 3 + 4 - 5 // 2)\n")


if __name__ == "__main__":
    unittest.main()
