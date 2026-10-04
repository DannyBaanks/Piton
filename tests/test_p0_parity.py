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
        result = compare_native_to_cpython("imprimir([1] + (2,))\n")
        self.assertEqual(result.native.returncode, 1)
        self.assertIn(b"TypeError", result.native.stderr)

    def test_dict_plus_dict_fail_closed(self):
        result = compare_native_to_cpython("imprimir({'a': 1} + {'b': 2})\n")
        self.assertEqual(result.native.returncode, 1)
        self.assertIn(b"TypeError", result.native.stderr)


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

    def test_range_as_value(self):
        """RANGE_VALUE_V1: `rango(...)` is a lazy value, not just loop sugar.

        Este test afirmaba que `rango(3)` como valor debia fallar cerrado: solo
        se consumia en la cabecera `para`. Ahora produce un objeto perezoso
        (start/stop/step, sin materializar) con repr, len, iteracion, indice,
        pertenencia, igualdad y verdad exactos. El `for` conserva su fast path
        de contador, que consume los argumentos directamente.
        """
        for source, esperado in (
            ("imprimir(rango(3))\n", "range(0, 3)\n"),
            ("imprimir(rango(0))\n", "range(0, 0)\n"),
            ("imprimir(rango(1, 4))\n", "range(1, 4)\n"),
            ("imprimir(rango(0, 6, 2))\n", "range(0, 6, 2)\n"),
            ("imprimir(rango(5, 0, -1))\n", "range(5, 0, -1)\n"),
        ):
            with self.subTest(fuente=source):
                result = compare_native_to_cpython(source)
                # CRLF_NORMALIZE_V1: en Windows el runtime emite \r\n
                # (msvcrt text mode); la expectativa canonica es \n.
                self.assertEqual(result.native.stdout.decode().replace("\r\n", "\n"), esperado)
                self.assertEqual(result.native.stdout, result.oracle.stdout)

    def test_range_len_iter_index_contains(self):
        for source in (
            "imprimir(longitud(rango(5)))\n",
            "r = rango(4)\npara x en r:\n    imprimir(x)\n",
            "r = rango(5, 0, -2)\npara x en r:\n    imprimir(x)\n",
            "r = rango(10, 20)\nimprimir(r[0])\nimprimir(r[-1])\n",
            "r = rango(0, 10, 2)\nimprimir(4 en r)\nimprimir(5 en r)\n",
            "imprimir(rango(0, 3) == rango(0, 3))\n",
            "imprimir(rango(0, 0) == rango(5, 5))\n",
            "imprimir(sum(rango(4)))\n",
            "imprimir(lista(rango(3)))\n",
            "imprimir(tipo(rango(3)))\n",
            "r = rango(3)\nsi r:\n    imprimir('T')\nsino:\n    imprimir('F')\n",
            "r = rango(0)\nsi r:\n    imprimir('T')\nsino:\n    imprimir('F')\n",
        ):
            with self.subTest(fuente=source):
                _assert_matches(self, source)

    def test_range_step_zero_raises_and_is_catchable(self):
        for source in ("imprimir(rango(1, 4, 0))\n",):
            result = compare_native_to_cpython(source)
            self.assertEqual(result.native.returncode, 1)
            self.assertEqual(result.oracle.returncode, 1)
        _assert_matches(
            self,
            "intentar:\n    x = rango(1, 2, 0)\nexcepto ValueError:\n    imprimir('caught')\n",
        )

    def test_conversion_builtins_as_values(self):
        """CONV_BUILTINS_V1: `lista`/`tupla`/`conjunto`/`diccionario` convert.

        Este test afirmaba que `lista([1, 2])` debia fallar cerrado. La
        conversion estaba rechazada en toda posicion salvo consumirla
        directamente en una cabecera `para`, lo que hacia fallar 19 casos del
        corpus sin motivo: los helpers de conversion ya existian y solo faltaba
        el dispatch. Ahora convierte como CPython, y lo no representable
        (un default de tipo distinto al de los valores) sigue fallando cerrado.
        """
        for source, esperado in (
            ("imprimir(lista([1, 2]))\n", "[1, 2]\n"),
            ("imprimir(lista((1, 2)))\n", "[1, 2]\n"),
            ("imprimir(lista('ab'))\n", "['a', 'b']\n"),
            ("imprimir(lista(()))\n", "[]\n"),
            ("imprimir(tupla([1, 2]))\n", "(1, 2)\n"),
            ("imprimir(tupla('ab'))\n", "('a', 'b')\n"),
            ("imprimir(conjunto([1, 2, 2]))\n", "{1, 2}\n"),
            ("imprimir(longitud(conjunto('aba')))\n", "2\n"),
            ("imprimir(diccionario([(1, 2)]))\n", "{1: 2}\n"),
            ("imprimir(diccionario())\n", "{}\n"),
        ):
            with self.subTest(fuente=source):
                result = compare_native_to_cpython(source)
                # CRLF_NORMALIZE_V1: ver test_range_as_value.
                self.assertEqual(result.native.stdout.decode().replace("\r\n", "\n"), esperado)
                self.assertEqual(result.native.stdout, result.oracle.stdout)

    def test_converted_collection_keeps_element_type(self):
        # COLL_ELEM_TYPE_V1: la conversion conserva el tipo de elemento, asi que
        # un indice o un `para` imprimen el valor, no un puntero.
        for source in (
            "x = lista('abc')\nimprimir(x[1])\n",
            "para c en lista('ab'):\n    imprimir(c)\n",
            "x = tupla('ab')\nimprimir(x[0])\n",
            "x = [10 ** 20]\nimprimir(x[0])\n",
        ):
            with self.subTest(fuente=source):
                _assert_matches(self, source)

    def test_reversed_as_value_fail_closed(self):
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("imprimir(reversed([1, 2]))\n")

    def test_abrir_fail_closed(self):
        # An EXISTING file still fails closed: the native subset cannot
        # open files (only a provably-missing literal path gets an
        # honest runtime FileNotFoundError).
        with pytest.raises(NativeBuildError):
            compare_native_to_cpython("abrir('tests/test_p0_parity.py')\n")

    def test_abrir_missing_is_runtime_error(self):
        # A provably-missing literal path is an honest runtime
        # FileNotFoundError (rc 1 on both sides), not a build rejection.
        result = compare_native_to_cpython("abrir('x_no_existe_piton_xyz')\n")
        assert result.native.returncode == 1
        assert b"FileNotFoundError" in result.native.stderr
        assert result.oracle.returncode == 1
        assert b"FileNotFoundError" in result.oracle.stderr

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

    def test_dynamic_mixed_types(self):
        # BOOL_SLOT_V1: a VARIABLE head is not statically foldable, so both
        # arms become tagged slots and the printed form follows whichever
        # side won (CPython returns the operand, not a bool).
        for source in (
            "x = 1\nimprimir(x y 'x')\n",
            "x = 0\nimprimir(x y 'x')\n",
            "x = 0\ny = 'z'\nimprimir(x y y)\n",
        ):
            result = compare_native_to_cpython(source)
            self.assertTrue(
                result.equivalent,
                "native=" + repr(result.native) + " oracle=" + repr(result.oracle),
            )


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


class NonCallableCallV1(unittest.TestCase):
    """CALL_NONCALLABLE_V1: llamar un valor no invocable da TypeError.

    `x = 5; x()` hacia que el backend genérico emitted
    `piton_closure_call_frame(<valor>, ...)` y el runtime lo TOMABA por una
    direccion de codigo: SIGSEGV con un int (desreferencia la direccion 5) o
    un salto selvaje con un puntero del heap. El tipo estatico no puede
    decidirlo — en este modelo sin tags un valor de funcion y un int se
    representan igual — asi que el runtime discrimina por rango: un valor
    invocable es un objeto magic-tagged del heap o una direccion dentro del
    texto del ejecutable.

    El corpus diferencial (tools/parity_corpus.py, area functions) encontre el
    crash; antes de este fix era DIVERGENT_CRASH (rc=-11).
    """

    def test_non_callable_scalars_raise_typeerror(self):
        for value in ("5", "1.5", "Verdadero", "Falso", "Nada", "'abc'"):
            result = compare_native_to_cpython(f"x = {value}\nx()\n")
            self.assertEqual(result.native.returncode, 1, f"{value} should exit 1")
            self.assertIn(b"TypeError", result.native.stderr)
            self.assertEqual(result.oracle.returncode, 1)

    def test_non_callable_collections_raise_typeerror(self):
        for value in ("[1]", "()", "{}", "{1}"):
            result = compare_native_to_cpython(f"x = {value}\nx()\n")
            self.assertEqual(result.native.returncode, 1, f"{value} should exit 1")
            self.assertIn(b"TypeError", result.native.stderr)

    def test_function_value_stays_callable(self):
        # el guard NO puede romper la funcion como valor (g = f)
        _assert_matches(self, "funcion f(x):\n    devolver x\ng = f\nimprimir(g(4))\n")
        _assert_matches(self, "funcion f(x):\n    devolver x + 1\ng = f\nh = g\nimprimir(h(1))\n")

    def test_plain_function_callbacks(self):
        _assert_matches(
            self, "funcion f(x):\n    devolver x * 2\npara v en map(f, [1, 2]):\n    imprimir(v)\n"
        )

    def test_recursion_and_closures_unaffected(self):
        _assert_matches(
            self,
            "funcion fac(n):\n    si n <= 1:\n        devolver 1\n"
            "    devolver n * fac(n - 1)\nimprimir(fac(5))\n",
        )


class DictKeyTypeV1(unittest.TestCase):
    """DICT_KEY_TYPE_V1: iterar un dict rinde el tipo real de sus CLAVES.

    El iterador de dict se tipaba "str" fijo, asi que `para k en {1: 'a'}`
    imprimia la clave int 1 con el printer de cadenas: leia un puntero del
    heap como si fuera texto y moria con SIGSEGV. Ahora el tipo de clave se
    registra al construir el dict y se propaga por store/load, y una clave de
    tipo desconocido falla cerrado en vez de imprimir basura.
    """

    def test_int_keys(self):
        _assert_matches(self, "d = {1: 'a', 2: 'b'}\npara k en d:\n    imprimir(k)\n")
        _assert_matches(self, "d = {1: 'a'}\npara k en d:\n    imprimir(k * 10)\n")
        _assert_matches(self, "para k en {1: 'a'}:\n    imprimir(k)\n")

    def test_str_keys_unchanged(self):
        _assert_matches(self, "d = {'a': 1, 'b': 2}\npara k en d:\n    imprimir(k)\n")

    def test_bool_and_float_keys(self):
        _assert_matches(self, "d = {Verdadero: 'a'}\npara k en d:\n    imprimir(k)\n")
        _assert_matches(self, "d = {1.5: 'a'}\npara k en d:\n    imprimir(k)\n")

    def test_reassignment_retypes(self):
        _assert_matches(
            self,
            "d = {1: 'a'}\npara k en d:\n    imprimir(k)\n"
            "d = {'x': 1}\npara j in d:\n    imprimir(j)\n".replace(" in ", " en "),
        )


class BigintCompareV1(unittest.TestCase):
    """BI_CMP_MAG_FIX_V1 + BIGINT_CMP_MIXED_V1: comparar bigints correctamente.

    Dos bugs en la misma familia, encontrados por el corpus enumerativo:
    (1) `bi_cmp_mag` calculaba el ancho en bits llamando `bi_bit_width` con un
        LIMB, pero esa funcion toma un `PitonBigInt*`: reinterpretaba el valor
        del limb como puntero y producia un ancho basura. TODA comparacion
        bigint-vs-bigint era incorrecta (`10**40 > 10**20` daba False), y como
        `+`/`-` usan `bi_cmp_mag` para los signos, la aritmetica con signos
        mixtos tambien.
    (2) `piton_bigint_cmp` casteaba AMBOS operandos a `PitonBigInt*`, asi que
        comparar un bigint con un int desreferenciaba el entero como struct:
        SIGSEGV.
    """

    def test_bigint_vs_bigint(self):
        for source in (
            "a = 10 ** 20\nb = 10 ** 30\nimprimir(a > b)\n",
            "a = 10 ** 30\nb = 10 ** 20\nimprimir(a > b)\n",
            "a = 10 ** 40\nb = 10 ** 20\nimprimir(a > b)\n",
            "a = 10 ** 99\nb = 10 ** 100\nimprimir(a > b)\n",
            # BI_CMP_SIGN_FIX_V1: `x->sign?-c:c` negated the result for
            # POSITIVE bigints (sign == 1), inverting every ordering.
            "a = 10 ** 40\nb = 10 ** 20\nimprimir(a > b)\n",
            "a = 10 ** 30\nb = 10 ** 20\nimprimir(a <= b)\n",
            "a = 10 ** 20\nb = 10 ** 30\nimprimir(a >= b)\n",
            "a = -(10 ** 40)\nb = -(10 ** 20)\nimprimir(a < b)\n",
            "a = 10 ** 20\nb = 10 ** 20\nimprimir(a == b)\n",
            "a = 10 ** 20\nb = 10 ** 30\nimprimir(a != b)\n",
            "a = 10 ** 20\nb = 10 ** 30\nimprimir(a < b)\n",
        ):
            _assert_matches(self, source)

    def test_bigint_vs_int(self):
        for source in (
            "imprimir(10 ** 20 > 5)\n",
            "x = 10 ** 20\nimprimir(5 > x)\n",
            "x = 10 ** 20\nimprimir(x < 5)\n",
            "x = 10 ** 20\nimprimir(x == 5)\n",
            "x = 10 ** 20\nimprimir(x >= 5)\n",
        ):
            _assert_matches(self, source)

    def test_bigint_arithmetic_with_mixed_signs(self):
        # bi_cmp_mag tambien decide el signo en + y -
        for source in (
            "a = 10 ** 20\nb = 10 ** 30\nimprimir(a + b)\n",
            "a = 10 ** 30\nb = 10 ** 20\nimprimir(a - b)\n",
            "a = 10 ** 20\nb = 10 ** 20\nimprimir(a * b)\n",
            "a = -(10 ** 20)\nb = 10 ** 20\nimprimir(a < b)\n",
            "x = -(10 ** 20)\nimprimir(x < 0)\n",
        ):
            _assert_matches(self, source)


class OrderingMixedTypesV1(unittest.TestCase):
    """ORDER_MIXED_TYPES_V1: los operadores de orden no comparan representaciones.

    El emitter caia a una comparacion C cruda entre las dos representaciones, de
    modo que `None < None` respondia False y `1 < '1'` respondia True. CPython
    lanza TypeError. Ahora se falla cerrado al compilar. `==`/`!=` entre tipos
    distintos sigue siendo legal, como en Python.
    """

    def test_unorderable_pairs_fail_closed(self):
        for source in (
            "imprimir(1 < '1')\n",
            "imprimir('1' > 1)\n",
            "imprimir(Nada < Nada)\n",
            "imprimir(Nada >= Nada)\n",
            "imprimir([1] < 'a')\n",
        ):
            result = compare_native_to_cpython(source)
            self.assertEqual(result.native.returncode, 1, source)
            self.assertIn(b"TypeError", result.native.stderr, source)

    def test_orderable_pairs_still_work(self):
        for source in (
            "imprimir(1 < 2)\n",
            "imprimir(1.5 < 2)\n",
            "imprimir(2 < 1.5)\n",
            "imprimir('a' < 'b')\n",
            "imprimir([1] < [2])\n",
            "imprimir((1,) < (2,))\n",
            "imprimir(Verdadero < 2)\n",
        ):
            _assert_matches(self, source)

    def test_equality_across_types_is_still_legal(self):
        for source in (
            "imprimir(1 == '1')\n",
            "imprimir(1 != '1')\n",
            "imprimir(Nada == Nada)\n",
            "imprimir(1 == 1.0)\n",
        ):
            _assert_matches(self, source)


class SumElementTypeV1(unittest.TestCase):
    """COLL_ELEM_TYPE_V1: `sum` acumula lo que la coleccion contiene.

    `sum` fuerzaba el tipo de resultado a `int` y el helper runtime suma los
    `bits` crudos de cada elemento, asi que un elemento bigint aportaba su
    PUNTER: `sum([10 ** 20])` imprimia 4210752 (una direccion). Ahora se
    registra el tipo de elemento de la coleccion y `sum` rechaza lo que no sea
    int en vez de acumular basura.
    """

    def test_int_collections_still_sum(self):
        for source in (
            "imprimir(sum([1, 2, 3]))\n",
            "imprimir(sum([]))\n",
            "a = [1, 2]\nimprimir(sum(a))\n",
            "imprimir(sum((1, 2)))\n",
        ):
            _assert_matches(self, source)

    def test_non_int_elements_fail_closed(self):
        # SUM_ELEM_V1: bigint y float ya suman como CPython; solo los
        # elementos no numericos siguen siendo un TypeError en runtime.
        _assert_matches(self, "imprimir(sum([10 ** 20]))\n")
        _assert_matches(self, "imprimir(sum([1.5, 2.5]))\n")
        result = compare_native_to_cpython("imprimir(sum(['a', 'b']))\n")
        self.assertEqual(result.native.returncode, 1)
        self.assertIn(b"TypeError", result.native.stderr)

    def test_list_printing_unaffected(self):
        _assert_matches(self, "imprimir([1, 2])\n")
        _assert_matches(self, "imprimir(['a'])\n")


class DivergentCluster29V1(unittest.TestCase):
    """Los DIVERGENT que quedaban tras el corpus: tipo(), dict.get/[], comp
    sobre dict, shift negativo, abs, identidad y sum con bigints."""

    def test_tipo_call_and_instance(self):
        for source in (
            "imprimir(tipo(1))\n",
            "imprimir(tipo('a'))\n",
            "imprimir(tipo([1]))\n",
            "imprimir(tipo(Nada))\n",
            "clase P:\n    pasar\nimprimir(tipo(P()))\n",
        ):
            _assert_matches(self, source)

    def test_dict_subscript_and_get_carry_value_type(self):
        for source in (
            "d = {1: 'a'}\nimprimir(d[1])\n",
            "d = {'a': 7}\nimprimir(d['a'])\n",
            "d = {1: 2.5}\nimprimir(d[1])\n",
            "d = {1: 'v'}\nimprimir(d.get(1))\n",
            "d = {'a': 2.5}\nimprimir(d.get('a'))\n",
            "d = {1: 'v'}\nimprimir(d.get(1, 'z'))\n",
        ):
            _assert_matches(self, source)

    def test_comprehension_over_dict_and_set_yields_keys(self):
        for source in (
            "imprimir([k para k en {'a': 1}])\n",
            "d = {'a': 1}\nimprimir([k para k en d])\n",
            "imprimir([k para k en {1: 'a', 2: 'b'}])\n",
            "imprimir([x para x en {1, 2}])\n",
        ):
            _assert_matches(self, source)

    def test_explicit_dict_subscript_still_raises(self):
        result = compare_native_to_cpython("d = {'a': 1}\nimprimir(d[0])\n")
        self.assertEqual(result.native.returncode, 1)
        self.assertEqual(result.oracle.returncode, 1)

    def test_negative_shift_raises_and_is_catchable(self):
        for source in ("imprimir(2 << -3)\n", "imprimir(2 >> -3)\n"):
            result = compare_native_to_cpython(source)
            self.assertEqual(result.native.returncode, 1)
            self.assertEqual(result.oracle.returncode, 1)
        _assert_matches(
            self,
            "intentar:\n    x = 2 << -1\nexcepto ValueError:\n    imprimir('caught')\n",
        )
        _assert_matches(self, "imprimir(2 << 3)\nimprimir(64 >> 3)\n")

    def test_abs_rejects_non_numeric(self):
        for source in ("imprimir(abs(Nada))\n", "imprimir(abs('a'))\n", "imprimir(abs([1]))\n"):
            result = compare_native_to_cpython(source)
            self.assertEqual(result.native.returncode, 1, source)
            self.assertIn(b"TypeError", result.native.stderr, source)
        for source in ("imprimir(abs(-3))\n", "imprimir(abs(-2.5))\n"):
            _assert_matches(self, source)

    def test_identity_is_not_equality(self):
        _assert_matches(self, "imprimir(1 es Verdadero)\n")
        _assert_matches(self, "imprimir(1 es 1)\n")
        _assert_matches(self, "imprimir(1 no es 2)\n")
        _assert_matches(self, "imprimir(Nada es Nada)\n")

    def test_sum_over_bigint_or_mixed_refuses(self):
        # SUM_ELEM_V1: bigint ya suma; solo la mezcla con str sigue siendo
        # un TypeError en runtime.
        _assert_matches(self, "imprimir(sum([10 ** 20, 1]))\n")
        _assert_matches(self, "imprimir(sum([10 ** 20]))\n")
        result = compare_native_to_cpython("imprimir(sum([1, 'a']))\n")
        self.assertEqual(result.native.returncode, 1)
        self.assertIn(b"TypeError", result.native.stderr)
        _assert_matches(self, "imprimir(sum([1, 2, 3]))\n")


class ExceptionPropagationV1(unittest.TestCase):
    """CALL_PROPAGATE_V1: `lanzar` cruza los limites de llamada.

    Un `raise` sin handler en SU funcion hacia exit directo, asi que la
    excepcion nunca llegaba al `intentar` del llamador. Ahora el raise retorna
    con el flag puesto y cada sitio de llamada lo enruta (al handler o mas
    arriba); solo el nivel superior y los generadores salen terminalmente.
    """

    def test_raise_in_function_caught_by_caller(self):
        _assert_matches(
            self,
            "funcion f():\n    lanzar ValueError('in func')\n"
            "intentar:\n    f()\nexcepto ValueError:\n    imprimir('ok')\n",
        )

    def test_raise_propagates_through_nested_calls(self):
        _assert_matches(
            self,
            "funcion g():\n    lanzar ValueError('deep')\n"
            "funcion f():\n    g()\n"
            "intentar:\n    f()\nexcepto ValueError:\n    imprimir('ok')\n",
        )

    def test_raise_with_message_across_calls(self):
        _assert_matches(
            self,
            "funcion f():\n    lanzar ValueError('detail')\n"
            "intentar:\n    f()\nexcepto ValueError como e:\n    imprimir(e)\n",
        )

    def test_uncaught_raise_still_exits(self):
        for source in (
            "funcion f():\n    lanzar ValueError('x')\nf()\n",
            "funcion g():\n    lanzar ValueError('x')\nfuncion f():\n    g()\nf()\n",
        ):
            result = compare_native_to_cpython(source)
            self.assertEqual(result.native.returncode, 1)
            self.assertEqual(result.oracle.returncode, 1)

    def test_mid_chain_catch(self):
        _assert_matches(
            self,
            "funcion g():\n    lanzar ValueError('x')\n"
            "funcion f():\n    intentar:\n        g()\n"
            "    excepto ValueError:\n        devolver 99\n"
            "imprimir(f())\n",
        )


class SuperAndDunderV1(unittest.TestCase):
    """METHODTYPE_V1 + DUNDER_ARITH_V1: super() y dunders aritmeticos.

    `P() + 1` hacia una suma entera del PUNTERO del objeto. `super().f()`
    devolvia basura porque la inferencia no tipaba el resultado del metodo
    llamado (todo method_call/binary caia a int).
    """

    def test_super_dispatch(self):
        _assert_matches(
            self,
            "clase A:\n    funcion f(self):\n        devolver 'A'\n"
            "clase B(A):\n    funcion f(self):\n        devolver 'B' + super().f()\n"
            "imprimir(B().f())\n",
        )

    def test_dunder_arithmetic(self):
        for source in (
            "clase P:\n    funcion __add__(self, o):\n        devolver 99\nimprimir(P() + 1)\n",
            "clase P:\n    funcion __radd__(self, o):\n        devolver 7\nimprimir(1 + P())\n",
            "clase P:\n    funcion __sub__(self, o):\n        devolver 3\nimprimir(P() - 1)\n",
            "clase P:\n    funcion __mul__(self, o):\n        devolver 4\nimprimir(P() * 2)\n",
        ):
            _assert_matches(self, source)

    def test_missing_dunder_fails_closed(self):
        try:
            compare_native_to_cpython("clase P:\n    pasar\nimprimir(P() + 1)\n")
        except NativeBuildError:
            pass
        else:
            self.fail("arithmetic without dunder not closed")


class ChrNulV1(unittest.TestCase):
    """CHR_NUL_V1: chr() results carry their encoded length.

    strlen stops at NUL, so `chr(0)` printed empty and measured length 0.
    Every chr() result is exactly one character; print writes its encoded
    bytes and len() answers 1. Equality of two chr() results via strcmp is
    already correct (distinct single chars differ in the first byte).
    """

    def test_chr_nul_prints_and_measures_one(self):
        for source in (
            "imprimir(chr(0))\n",
            "imprimir(longitud(chr(0)))\n",
            "x = chr(0)\nimprimir(x)\nimprimir(longitud(x))\n",
        ):
            _assert_matches(self, source)

    def test_chr_multibyte_roundtrips(self):
        for source in (
            "imprimir(chr(65))\n",
            "imprimir(chr(233))\n",
            "imprimir(chr(20013))\n",
            "imprimir(chr(1114111))\n",
            "imprimir(longitud(chr(1114111)))\n",
        ):
            _assert_matches(self, source)

    def test_chr_out_of_range_raises(self):
        for source in ("imprimir(chr(1114112))\n", "imprimir(chr(-1))\n"):
            result = compare_native_to_cpython(source)
            self.assertEqual(result.native.returncode, 1)
            self.assertEqual(result.oracle.returncode, 1)


if __name__ == "__main__":
    unittest.main()
