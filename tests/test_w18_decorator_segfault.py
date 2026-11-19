"""Differential coverage for decorated functions forwarding variadic calls."""
from __future__ import annotations

import pytest

from piton.native_differential import compare_native_to_cpython
from piton.x86 import NativeBuildError


CASES = {
    "plain_deco": (
        "funcion registra(fn):\n"
        "    funcion w(*a):\n"
        "        devolver fn(*a)\n"
        "    devolver w\n"
        "@registra\n"
        "funcion suma(a, b):\n"
        "    devolver a + b\n"
        "imprimir(suma(2, 3))\n"
    ),
    "deco_kwargs": (
        "funcion registra(fn):\n"
        "    funcion w(*a, **k):\n"
        "        devolver fn(*a, **k)\n"
        "    devolver w\n"
        "@registra\n"
        "funcion f(a, b=10):\n"
        "    devolver a * b\n"
        "imprimir(f(2), f(2, b=3))\n"
    ),
    "wraps": (
        "importar functools\n"
        "funcion registra(fn):\n"
        "    @functools.wraps(fn)\n"
        "    funcion w(*a):\n"
        "        devolver fn(*a)\n"
        "    devolver w\n"
        "@registra\n"
        "funcion suma(a, b):\n"
        "    devolver a + b\n"
        "imprimir(suma(2, 3), suma.__name__)\n"
    ),
    "deco_counter": (
        "funcion cuenta(fn):\n"
        "    n = [0]\n"
        "    funcion w(*a):\n"
        "        n[0] = n[0] + 1\n"
        "        devolver (n[0], fn(*a))\n"
        "    devolver w\n"
        "@cuenta\n"
        "funcion uno():\n"
        "    devolver 1\n"
        "uno()\n"
        "imprimir(uno())\n"
    ),
    "deco_factory": (
        "funcion veces(k):\n"
        "    funcion deco(fn):\n"
        "        funcion w(x):\n"
        "            devolver fn(x) * k\n"
        "        devolver w\n"
        "    devolver deco\n"
        "@veces(3)\n"
        "funcion f(x):\n"
        "    devolver x + 1\n"
        "imprimir(f(1))\n"
    ),
}


EXTRA_PARITY_CASES = {
    "empty_args_and_string_defaults": (
        "funcion registra(fn):\n"
        "    funcion w(*a, **k):\n"
        "        devolver fn(*a, **k)\n"
        "    devolver w\n"
        "@registra\n"
        "funcion pega(a='hola', b='!'):\n"
        "    devolver a + b\n"
        "imprimir(pega(), pega('adios', b='?'))\n"
    ),
    "frame_abi_keyword_forwarding": (
        "funcion registra(fn):\n"
        "    funcion w(*a, **k):\n"
        "        devolver fn(*a, **k)\n"
        "    devolver w\n"
        "@registra\n"
        "funcion f(a, b, c, d, e=5):\n"
        "    devolver a + b + c + d + e\n"
        "imprimir(f(1, 2, 3, 4), f(1, 2, c=3, d=4, e=6))\n"
    ),
    "nested_function_target": (
        "funcion registra(fn):\n"
        "    funcion w(*a, **k):\n"
        "        devolver fn(*a, **k)\n"
        "    devolver w\n"
        "funcion make():\n"
        "    @registra\n"
        "    funcion inner(a, b=4):\n"
        "        devolver a + b\n"
        "    devolver inner\n"
        "f = make()\n"
        "imprimir(f(2), f(2, b=6))\n"
    ),
}


def _assert_matches_cpython(source: str) -> None:
    result = compare_native_to_cpython(source)
    assert result.native.returncode == result.oracle.returncode, (
        f"returncode mismatch\nnative stderr: {result.native.stderr!r}\n"
        f"oracle stderr: {result.oracle.stderr!r}"
    )
    assert result.native.stdout == result.oracle.stdout, (
        f"stdout mismatch\nnative stderr: {result.native.stderr!r}\n"
        f"oracle stderr: {result.oracle.stderr!r}"
    )


@pytest.mark.parametrize("name,source", CASES.items())
def test_exact_decorator_reproducers_match_cpython(name: str, source: str) -> None:
    _assert_matches_cpython(source)


@pytest.mark.parametrize("name,source", EXTRA_PARITY_CASES.items())
def test_signature_aware_forwarding_edges_match_cpython(name: str, source: str) -> None:
    _assert_matches_cpython(source)


def test_forwarded_unknown_keyword_raises_typeerror_like_cpython() -> None:
    source = (
        "funcion registra(fn):\n"
        "    funcion w(*a, **k):\n"
        "        devolver fn(*a, **k)\n"
        "    devolver w\n"
        "@registra\n"
        "funcion f(a):\n"
        "    devolver a\n"
        "f(1, missing=2)\n"
    )
    result = compare_native_to_cpython(source)
    assert result.native.returncode == result.oracle.returncode != 0
    assert result.native.stdout == result.oracle.stdout == b""
    assert b"TypeError:" in result.native.stderr
    assert b"TypeError:" in result.oracle.stderr


def test_callable_signatures_over_63_parameters_fail_closed() -> None:
    params = ", ".join(f"a{i}" for i in range(64))
    source = (
        "funcion registra(fn):\n"
        "    funcion w(*a, **k):\n"
        "        devolver fn(*a, **k)\n"
        "    devolver w\n"
        f"@registra\nfuncion wide({params}):\n"
        "    devolver a0\n"
    )
    with pytest.raises(
        NativeBuildError,
        match="dynamic call binding for .* supports at most 63 parameters",
    ):
        compare_native_to_cpython(source)
