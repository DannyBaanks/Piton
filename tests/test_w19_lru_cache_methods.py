"""Differential coverage for functools cache wrappers and descriptors."""
from __future__ import annotations

import pytest

from piton.native_differential import compare_native_to_cpython
from piton.x86 import NativeBuildError


CASES = {
    "lru_method": "importar functools\nclase C:\n    @functools.lru_cache(maxsize=None)\n    funcion fib(este, n):\n        devolver n si n < 2 sino este.fib(n - 1) + este.fib(n - 2)\nimprimir(C().fib(30))\n",
    "lru_cache_info": "importar functools\n@functools.lru_cache(maxsize=2)\nfuncion sq(x):\n    devolver x * x\nsq(1)\nsq(2)\nsq(1)\nimprimir(sq.cache_info())\n",
    "cached_property": "importar functools\nclase P:\n    funcion __init__(este):\n        este.n = 0\n    @functools.cached_property\n    funcion valor(este):\n        este.n = este.n + 1\n        devolver 42\np = P()\nimprimir(p.valor, p.valor, p.n)\n",
}


def _assert_matches(source: str) -> None:
    result = compare_native_to_cpython(source)
    assert result.native.returncode == result.oracle.returncode, (
        f"returncode mismatch\nnative stderr: {result.native.stderr!r}\n"
        f"oracle stderr: {result.oracle.stderr!r}"
    )
    assert result.native.stdout == result.oracle.stdout, (
        f"stdout mismatch\nnative stdout: {result.native.stdout!r}\n"
        f"oracle stdout: {result.oracle.stdout!r}\nnative stderr: {result.native.stderr!r}\n"
        f"oracle stderr: {result.oracle.stderr!r}"
    )


@pytest.mark.parametrize("source", CASES.values(), ids=CASES.keys())
def test_exact_reproducer_cases(source: str) -> None:
    _assert_matches(source)


def test_cache_info_is_named_tuple_shaped_and_clear_resets_stats() -> None:
    _assert_matches(
        "importar functools\n"
        "@functools.lru_cache(maxsize=2)\n"
        "funcion sq(x):\n"
        "    devolver x * x\n"
        "sq(1)\n"
        "sq(2)\n"
        "sq(1)\n"
        "info = sq.cache_info()\n"
        "imprimir(info, info._fields, info.hits, info.misses, info.maxsize, info.currsize)\n"
        "sq.cache_clear()\n"
        "imprimir(sq.cache_info())\n"
        "@functools.lru_cache(maxsize=Nada)\n"
        "funcion ident(x):\n"
        "    devolver x\n"
        "ident(1)\n"
        "imprimir(ident.cache_info(), ident.cache_info().maxsize)\n"
    )


def test_method_cache_key_includes_instance_and_hits_refresh_lru_order() -> None:
    _assert_matches(
        "importar functools\n"
        "llamadas = [0]\n"
        "clase M:\n"
        "    @functools.lru_cache(maxsize=2)\n"
        "    funcion calc(este, n):\n"
        "        llamadas[0] = llamadas[0] + 1\n"
        "        devolver llamadas[0]\n"
        "a = M()\n"
        "b = M()\n"
        "imprimir(a.calc(7), b.calc(7), a.calc(7), llamadas[0])\n"
        "imprimir(a.calc(1), a.calc(2), a.calc(1), a.calc(3), a.calc(2), llamadas[0])\n"
    )


def test_cache_maxsize_zero_and_clear_edge_case() -> None:
    _assert_matches(
        "importar functools\n"
        "llamadas = [0]\n"
        "@functools.lru_cache(maxsize=0)\n"
        "funcion f(x):\n"
        "    llamadas[0] = llamadas[0] + 1\n"
        "    devolver llamadas[0]\n"
        "imprimir(f(1), f(1), f.cache_info())\n"
        "f.cache_clear()\n"
        "imprimir(f.cache_info())\n"
    )


def test_cached_property_value_is_cached_in_instance_dict() -> None:
    _assert_matches(
        "importar functools\n"
        "clase P:\n"
        "    funcion __init__(este):\n"
        "        este.n = 0\n"
        "    @functools.cached_property\n"
        "    funcion valor(este):\n"
        "        este.n = este.n + 1\n"
        "        devolver 42\n"
        "p = P()\n"
        "imprimir(p.valor, p.valor, p.n, vars(p))\n"
        "q = P()\n"
        "imprimir(q.valor, q.n)\n"
    )


def test_cached_property_without_instance_read_stays_fail_closed() -> None:
    source = (
        "importar functools\n"
        "clase Handler:\n"
        "    @functools.cached_property\n"
        "    funcion token(este):\n"
        "        devolver 7\n"
    )
    with pytest.raises(
        NativeBuildError,
        match="cached descriptor storage is unavailable",
    ):
        compare_native_to_cpython(source)


def test_lru_cache_on_special_method_matches_cpython() -> None:
    # Replaces obsolete fail-closed test_lru_cache_on_special_method_stays_fail_closed:
    # W21 decorated_dunder_methods added runtime dispatch forwarders for __repr__.
    _assert_matches(
        "importar functools\n"
        "clase Handler:\n"
        "    @functools.lru_cache(maxsize=Nada)\n"
        "    funcion __repr__(este):\n"
        "        devolver 'handler'\n"
        "h = Handler()\n"
        "imprimir(repr(h), h.__repr__())\n"
    )
