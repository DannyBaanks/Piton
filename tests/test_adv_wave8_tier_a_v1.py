"""Adversarial parity cases for Tier A native build gaps.

These use the live CPython interpreter as the oracle and compare complete
native observations so the implementation cannot pass by returning a stub.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from piton.native_differential import compare_native_to_cpython


def _assert_exact_parity(source: str) -> None:
    result = compare_native_to_cpython(source)
    assert result.native == result.oracle, (
        f"native={result.native!r}\nCPython={result.oracle!r}"
    )


def test_vars_object_dict_is_live_and_mutable() -> None:
    _assert_exact_parity(
        "clase Options:\n"
        "    pasar\n"
        "options = Options()\n"
        "options.count = 1\n"
        "namespace = vars(options)\n"
        "namespace.setdefault('name', 'initial')\n"
        "options.count = 2\n"
        "imprimir(namespace['count'], namespace['name'])\n"
        "namespace['count'] = 3\n"
        "namespace.update({'enabled': 4})\n"
        "imprimir(options.count, options.enabled, namespace)\n"
    )


def test_vars_clear_keeps_class_attributes() -> None:
    _assert_exact_parity(
        "clase Options:\n"
        "    funcion enabled(self):\n"
        "        devolver 7\n"
        "options = Options()\n"
        "options.count = 1\n"
        "namespace = vars(options)\n"
        "namespace.clear()\n"
        "imprimir(options.enabled())\n"
    )


def test_int_from_bytes_unbound_classmethod_byteorder_and_signedness() -> None:
    _assert_exact_parity(
        "imprimir(int.from_bytes(b'\\x01\\x02'))\n"
        "imprimir(int.from_bytes(b'\\x01\\x02', 'little'))\n"
        "imprimir(int.from_bytes(b'\\xff\\xfe', signed=Verdadero))\n"
        "imprimir(int.from_bytes(b'\\xff\\xfe', byteorder='little', signed=Verdadero))\n"
    )


def test_map_native_callbacks_over_strings_and_iterators() -> None:
    _assert_exact_parity(
        "funcion integerize(values):\n"
        "    para number en map(int, values):\n"
        "        imprimir(number)\n"
        "integerize('39')\n"
        "integerize(['4', '8'])\n"
        "para number en map(int, '507'):\n"
        "    imprimir(number)\n"
        "para text en map(str, map(int, '24')):\n"
        "    imprimir(text)\n"
        "para inner en map(iter, [[3, 4], [5]]):\n"
        "    para value en inner:\n"
        "        imprimir(value)\n"
    )
