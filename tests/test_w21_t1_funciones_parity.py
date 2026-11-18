"""W21 T1 funciones parity: slot-tagged arguments passed to methods.

Root cause: a call site that passes a slot-tagged (boxed) argument to a
method did not mark the callee parameter as slot (SLOT_PARAM_V1 existed only
for plain functions). The callee then read the box address as raw bits, so
`self._d[x] = 1` derived the key kind from the box pointer and
`Counter(datos).most_common()` returned corrupted str keys.
"""
from __future__ import annotations

import unittest

from piton.native_differential import compare_native_to_cpython


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


class W21T1FuncionesParity(unittest.TestCase):
    def test_counter_most_common_with_n_keeps_str_keys(self):
        # Reproducer 1 (tools/parity_corpus_t1_funciones.py 46).
        _assert_matches(
            self,
            "desde collections importar Counter\n"
            "funcion frecuentes(datos, n):\n"
            "    devolver Counter(datos).most_common(n)\n"
            "imprimir(frecuentes(['a', 'b', 'a', 'a', 'c'], 2))\n",
        )

    def test_counter_most_common_all_keeps_str_keys(self):
        # Reproducer 2 (tools/parity_corpus_t1_funciones.py 47).
        _assert_matches(
            self,
            "desde collections importar Counter\n"
            "funcion frecuentes(datos):\n"
            "    devolver Counter(datos).most_common()\n"
            "imprimir(frecuentes(['a', 'b', 'a', 'c']))\n",
        )

    def test_method_stores_str_key_from_ctor_slot_arg(self):
        # Minimal shape: __init__ passes a list element (slot) to a method
        # that uses it as a dict key.
        _assert_matches(
            self,
            "clase C:\n"
            "    funcion __init__(self, datos):\n"
            "        self._d = {}\n"
            "        self.inc(datos[0])\n"
            "    funcion inc(self, x):\n"
            "        self._d[x] = 1\n"
            "    funcion keys(self):\n"
            "        d = self._d\n"
            "        claves = []\n"
            "        para k en d:\n"
            "            claves.append(k)\n"
            "        devolver claves\n"
            "c = C(['a', 'b'])\n"
            "imprimir(c.keys())\n",
        )

    def test_method_loop_over_slot_list_builds_counter_keys(self):
        _assert_matches(
            self,
            "clase C:\n"
            "    funcion __init__(self, datos):\n"
            "        self._d = {}\n"
            "        para x en datos:\n"
            "            self._inc(x)\n"
            "    funcion _inc(self, x):\n"
            "        d = self._d\n"
            "        si x en d:\n"
            "            d[x] = d[x] + 1\n"
            "        sino:\n"
            "            d[x] = 1\n"
            "    funcion most_common(self):\n"
            "        d = self._d\n"
            "        out = []\n"
            "        para k en d:\n"
            "            out.append((k, d[k]))\n"
            "        devolver out\n"
            "imprimir(C(['a', 'b', 'a', 'c']).most_common())\n",
        )

    def test_method_int_slot_args_keep_int_values(self):
        # Negative/edge: the same method receives boxed ints, a None and a
        # float through slot-tagged arguments; values must stay intact.
        _assert_matches(
            self,
            "clase Acum:\n"
            "    funcion __init__(self):\n"
            "        self.vals = []\n"
            "    funcion add(self, v):\n"
            "        self.vals.append(v)\n"
            "xs = [7, -3, 0, 2**40]\n"
            "a = Acum()\n"
            "para v en xs:\n"
            "    a.add(v)\n"
            "a.add(None)\n"
            "a.add(1.5)\n"
            "imprimir(a.vals)\n",
        )

    def test_method_str_slot_arg_roundtrip_and_membership(self):
        _assert_matches(
            self,
            "clase Ref:\n"
            "    funcion __init__(self, datos):\n"
            "        self._s = set()\n"
            "        self.guardar(datos[0])\n"
            "        self.guardar(datos[1])\n"
            "    funcion guardar(self, x):\n"
            "        self._s.add(x)\n"
            "    funcion tiene(self, x):\n"
            "        devolver x en self._s\n"
            "r = Ref(['uno', 'dos'])\n"
            "imprimir(r.tiene('uno'), r.tiene('tres'))\n",
        )

    def test_counter_most_common_empty_input(self):
        # Edge: empty input yields an empty list in both runtimes.
        _assert_matches(
            self,
            "desde collections importar Counter\n"
            "imprimir(Counter([]).most_common())\n",
        )

    def test_tipo_of_parameter_compares_equal_to_builtin_type(self):
        # TYPE_SLOT_RUNTIME_V1: tipo(x) de un parametro (caja slot) debe leer la
        # kind real; antes era siempre PK_OBJECT y nunca igualaba tipo([]).
        _assert_matches(
            self,
            "funcion k(x):\n"
            "    t = tipo(x)\n"
            "    si t == tipo([]):\n"
            "        devolver 'L'\n"
            "    sino:\n"
            "        devolver 'O'\n"
            "imprimir(k([1]))\n"
            "funcion es_lista(x):\n"
            "    devolver tipo(x) == tipo([])\n"
            "imprimir(es_lista([1, 2]))\n"
            "imprimir(es_lista([]))\n",
        )

    def test_tipo_param_mixed_argument_kinds_fail_closed(self):
        # Negative: un mismo parametro que recibe list y str y se usa en un
        # dispatch por tipo sigue fallando cerrado (fail closed, no se emite).
        from piton.native_differential import compare_native_to_cpython
        from piton.x86 import NativeBuildError

        with self.assertRaises(NativeBuildError):
            compare_native_to_cpython(
                "funcion es_lista(x):\n"
                "    devolver tipo(x) == tipo([])\n"
                "imprimir(es_lista([1, 2]))\n"
                "imprimir(es_lista('ab'))\n"
            )

    def test_slot_tagged_str_argument_used_as_str_method(self):
        # SLOT_PARAM_UNBOX_V1: el argumento llega como caja slot (elemento de
        # lista) y el parametro se usa como str (.find): debe desempaquetarse.
        _assert_matches(
            self,
            "funcion g(s):\n"
            "    devolver s.find('b') >= 0\n"
            "datos = ['abc', 'x']\n"
            "imprimir(g(datos[0]))\n",
        )

    def test_reprlib_repr_truncates_list_and_str(self):
        # Corpus 15/17 (reprlib): repr() debe truncar con '...' como CPython.
        # Un programa por tipo de argumento: reprlib.repr recibe list y str en
        # el mismo programa es fail closed (parametro multi-tipo en dispatch).
        _assert_matches(
            self,
            "importar reprlib\n"
            "imprimir(reprlib.repr([0, 1, 2, 3, 4, 5, 6, 7]))\n",
        )
        _assert_matches(
            self,
            "importar reprlib\n"
            "imprimir(reprlib.repr('abcdefghijklmnopqrstuvwxyzABCDEFG'))\n",
        )

    def test_str_method_on_raw_int_argument_still_fails_closed(self):
        # Negative: un literal int pasado a un parametro usado como str sigue
        # fallando cerrado (no se compila ni se ejecuta).
        from piton.native_differential import compare_native_to_cpython
        from piton.x86 import NativeBuildError

        with self.assertRaises(NativeBuildError):
            compare_native_to_cpython(
                "funcion g(s):\n"
                "    devolver s.find('a')\n"
                "imprimir(g(1))\n"
            )


if __name__ == "__main__":
    unittest.main()
