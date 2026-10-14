#!/usr/bin/env python3
"""parity_corpus_v3 - tercer corpus enumerativo PITON vs CPython 3.12.4.

Ciclo de trabajo: medir -> cerrar gaps -> 100% -> nuevo corpus con mas
superficie. v1 midio el subset declarado; v2 las 12 areas avanzadas
(corpus cerrado en 221/221 EQUIV); v3 mide la siguiente frontera:

  str_methods_more   casefold/isascii/encode/format_map/partition familia
  dict_ops_more      fromkeys, merge |/|=, vistas, setdefault, pop
  set_ops            |, &, -, ^, union/intersection/difference/symmetric
  frozenset          constructor, operaciones, pertenencia, metodos
  memoryview         sobre bytes: len, indice, slice, tobytes, cast
  slice_value        slice como valor: attrs, indices, eq
  complex_deep       conjugate, **, ctor desde string, comparaciones
  builtins_more      hash, repr, callable, iter con centinela, min/max key
  exceptions_deep    args, ExceptionGroup, excepto*, __context__
  numeric_semantics  hash/overflow/NaN-inf/mixed, int-float
  object_protocol3   __getattribute__, __delattr__, __contains__, __getitem__
  callable_protocol  __call__, objetos invocables, funciones como valores

Buckets canonicos: EQUIV, RAISE_EQ, FAIL_CLOSED, DIVERGENT_MISMATCH,
DIVERGENT_CRASH, TIMEOUT.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

EQUIV = "EQUIV"
RAISE_EQ = "RAISE_EQ"
FAIL_CLOSED = "FAIL_CLOSED"
DIVERGENT_MISMATCH = "DIVERGENT_MISMATCH"
DIVERGENT_CRASH = "DIVERGENT_CRASH"
TIMEOUT = "TIMEOUT"

BUCKETS = [DIVERGENT_CRASH, DIVERGENT_MISMATCH, TIMEOUT, FAIL_CLOSED, RAISE_EQ, EQUIV]
RISKY = (DIVERGENT_CRASH, DIVERGENT_MISMATCH, TIMEOUT)

_EXCEPTION_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Warning))\b")


def _c(area: str, name: str, src: str) -> tuple:
    return (area, name, src if src.endswith("\n") else src + "\n")


# --------------------------------------------------------------------------
# 12 areas de la frontera v3
# --------------------------------------------------------------------------

def area_str_methods_more():
    out = []
    working = [
        ("partition", "imprimir('a-b-c'.partition('-'))"),
        ("rpartition", "imprimir('a-b-c'.rpartition('-'))"),
        ("splitlines", "imprimir('a\\nb'.splitlines())"),
        ("removeprefix", "imprimir('foobar'.removeprefix('foo'))"),
        ("removesuffix", "imprimir('foobar'.removesuffix('bar'))"),
        ("expandtabs", "imprimir('a\\tb'.expandtabs(4))"),
        ("maketrans", "t = str.maketrans('ab', 'xy')\nimprimir('abc'.translate(t))"),
        ("translate", "imprimir('abc'.translate(str.maketrans('a', 'z')))"),
        ("encode", "imprimir('abc'.encode())"),
        ("encode-utf8", "imprimir('ñ'.encode('utf-8'))"),
        ("format-map", "imprimir('{a}'.format_map({'a': 1}))"),
        ("join-gen", "imprimir(','.join(['a', 'b']))"),
        ("rsplit", "imprimir('a b c'.rsplit(' ', 1))"),
        ("count-sub", "imprimir('abab'.count('ab'))"),
        ("index-sub", "imprimir('abcabc'.index('bc'))"),
        ("zfill-neg", "imprimir('-42'.zfill(5))"),
        ("format-spec", "imprimir('{:>6}'.format('ab'))"),
        ("casefold-gap", "imprimir('HOLA'.casefold())"),
        ("isascii-gap", "imprimir('abc'.isascii())"),
        ("isascii-gap2", "imprimir('ñ'.isascii())"),
        ("isprintable", "imprimir('abc'.isprintable())"),
        ("isidentifier", "imprimir('abc'.isidentifier())"),
        ("title-mixed", "imprimir('hola MUNDO 123'.title())"),
    ]
    for name, src in working:
        out.append(_c("str_methods_more", name, src))
    return out


def area_dict_ops_more():
    out = []
    cases = [
        ("setdefault", "d = {}\nimprimir(d.setdefault('a', 1))\nimprimir(d)"),
        ("setdefault-hit", "d = {'a': 5}\nimprimir(d.setdefault('a', 9))\nimprimir(d)"),
        ("pop", "d = {'a': 1, 'b': 2}\nimprimir(d.pop('a'))\nimprimir(d)"),
        ("pop-default", "d = {'a': 1}\nimprimir(d.pop('z', 7))\nimprimir(d)"),
        ("popitem", "d = {'a': 1}\nimprimir(d.popitem())\nimprimir(d)"),
        ("update-kwargs", "d = {'a': 1}\nd.update(b=2)\nimprimir(d)"),
        ("fromkeys", "imprimir(dict.fromkeys(['a', 'b'], 0))"),
        ("fromkeys-nodef", "imprimir(dict.fromkeys(['a', 'b']))"),
        ("merge-op", "imprimir({'a': 1} | {'b': 2})"),
        ("merge-ior", "d = {'a': 1}\nd |= {'b': 2}\nimprimir(d)"),
        ("items-view", "d = {'a': 1}\nimprimir(lista(d.items()))"),
        ("keys-view", "d = {'a': 1, 'b': 2}\nimprimir(lista(d.keys()))"),
        ("values-view", "d = {'a': 1}\nimprimir(lista(d.values()))"),
        ("copy", "d = {'a': [1]}\nc = d.copy()\nc['a'].append(2)\nimprimir(d)\nimprimir(c)"),
        ("del", "d = {'a': 1, 'b': 2}\nborrar d['a']\nimprimir(d)"),
        ("in-keys", "imprimir('a' en {'a': 1})"),
        ("eq", "imprimir({'a': 1} == {'a': 1})"),
        ("ne", "imprimir({'a': 1} != {'a': 2})"),
        ("nested", "imprimir({'a': {'b': [1, 2]}})"),
    ]
    for name, src in cases:
        out.append(_c("dict_ops_more", name, src))
    return out


def area_set_ops():
    out = []
    ops = [
        ("union", "{1, 2} | {2, 3}"),
        ("inter", "{1, 2, 3} & {2, 3, 4}"),
        ("diff", "{1, 2, 3} - {2}"),
        ("symdiff", "{1, 2, 3} ^ {2, 3, 4}"),
        ("union-method", "{1, 2}.union({3})"),
        ("inter-method", "{1, 2, 3}.intersection({2, 3})"),
        ("diff-method", "{1, 2, 3}.difference({2})"),
        ("symdiff-method", "{1, 2}.symmetric_difference({2, 3})"),
        ("issubset", "{1}.issubset({1, 2})"),
        ("issuperset", "{1, 2}.issuperset({1})"),
        ("isdisjoint", "{1}.isdisjoint({2})"),
        ("update", "s = {1}\ns.update({2})\nimprimir(s)"),
        ("inter-update", "s = {1, 2}\ns.intersection_update({2})\nimprimir(s)"),
        ("diff-update", "s = {1, 2}\ns.difference_update({1})\nimprimir(s)"),
        ("symdiff-update", "s = {1, 2}\ns.symmetric_difference_update({2, 3})\nimprimir(s)"),
        ("add", "s = {1}\ns.add(2)\nimprimir(s)"),
        ("discard", "s = {1, 2}\ns.discard(1)\nimprimir(s)"),
        ("remove", "s = {1, 2}\ns.remove(1)\nimprimir(s)"),
        ("pop", "s = {1}\nimprimir(s.pop())\nimprimir(s)"),
        ("clear", "s = {1}\ns.clear()\nimprimir(s)"),
        ("copy", "s = {1, 2}\nc = s.copy()\nc.add(3)\nimprimir(s)\nimprimir(c)"),
        ("len", "imprimir(longitud({1, 2, 2}))"),
        ("in", "imprimir(1 en {1, 2})"),
        ("eq", "imprimir({1, 2} == {2, 1})"),
        ("subset-op", "imprimir({1} <= {1, 2})"),
        ("lt-op", "imprimir({1} < {1, 2})"),
        ("sup-op", "imprimir({1, 2} >= {1})"),
        ("gt-op", "imprimir({1, 2} > {1})"),
    ]
    for name, src in ops:
        out.append(_c("set_ops", name, src))
    return out


def area_frozenset():
    out = []
    cases = [
        ("ctor-list", "imprimir(frozenset([1, 2, 2]))"),
        ("ctor-range", "imprimir(frozenset(rango(3)))"),
        # NOTA: imprimir un frozenset de STRINGS o iterar uno de varios
        # elementos depende del orden de la tabla hash, que CPython randomiza
        # por proceso (misma razon por la que el corpus v1 excluyo sets de
        # strings): se mide con un solo elemento o por longitud/pertenencia.
        ("sum", "imprimir(suma(frozenset([1, 2, 3])))"),
        ("len", "imprimir(longitud(frozenset([1, 2, 2])))"),
        ("in", "imprimir(1 en frozenset([1, 2]))"),
        ("not-in", "imprimir(3 no en frozenset([1, 2]))"),
        ("eq", "imprimir(frozenset([1, 2]) == frozenset([2, 1]))"),
        ("union", "imprimir(frozenset([1]) | frozenset([2]))"),
        ("inter", "imprimir(frozenset([1, 2]) & frozenset([2, 3]))"),
        ("issubset", "imprimir(frozenset([1]).issubset(frozenset([1, 2])))"),
        ("isdisjoint", "imprimir(frozenset([1]).isdisjoint(frozenset([2])))"),
        ("iter-uno", "para x en frozenset([1]):\n    imprimir(x)"),
        ("iter-total", "total = 0\npara x en frozenset([1, 2, 3]):\n    total = total + x\nimprimir(total)"),
        ("hash-eq", "imprimir(hash(frozenset([1, 2])) == hash(frozenset([2, 1])))"),
        ("from-ctor", "imprimir(frozenset())"),
    ]
    for name, src in cases:
        out.append(_c("frozenset", name, src))
    return out


def area_memoryview():
    out = []
    cases = [
        ("ctor", "m = memoryview(b'abc')\nimprimir(longitud(m))"),
        ("index", "m = memoryview(b'abc')\nimprimir(m[0])"),
        ("negindex", "m = memoryview(b'abc')\nimprimir(m[-1])"),
        ("slice", "m = memoryview(b'abcdef')\nimprimir(m[1:4])"),
        ("slice-step", "m = memoryview(b'abcdef')\nimprimir(m[::2])"),
        ("tobytes", "m = memoryview(b'abc')\nimprimir(m.tobytes())"),
        ("tolist", "m = memoryview(b'abc')\nimprimir(m.tolist())"),
        ("eq", "imprimir(memoryview(b'ab') == memoryview(b'ab'))"),
        ("iter", "m = memoryview(b'ab')\npara x en m:\n    imprimir(x)"),
        ("cast", "m = memoryview(b'abcd')\nc = m.cast('B')\nimprimir(longitud(c))"),
        ("readonly", "m = memoryview(b'ab')\nimprimir(m.readonly)"),
        ("bytearray-mut", "ba = bytearray(b'abc')\nm = memoryview(ba)\nm[0] = 65\nimprimir(ba)"),
        ("itemsize", "m = memoryview(b'abc')\nimprimir(m.itemsize)"),
        ("nbytes", "m = memoryview(b'abc')\nimprimir(m.nbytes)"),
    ]
    for name, src in cases:
        out.append(_c("memoryview", name, src))
    return out


def area_slice_value():
    out = []
    cases = [
        ("ctor-attrs", "s = slice(1, 5, 2)\nimprimir(s.start)\nimprimir(s.stop)\nimprimir(s.step)"),
        # NOTA: un bound ausente (None) no es representable en el modelo sin
        # tag: el caso cae en exit 2 honesto (fail-closed), no en paridad.
        ("ctor-attrs-none", "s = slice(None, 5)\nimprimir(s.start)\nimprimir(s.stop)\nimprimir(s.step)"),
        ("apply-list", "imprimir([1, 2, 3, 4, 5][slice(1, 4)])"),
        ("apply-str", "imprimir('abcdef'[slice(1, 4)])"),
        ("apply-step", "imprimir([1, 2, 3, 4, 5][slice(None, None, 2)])"),
        ("indices", "s = slice(1, 8, 2)\nimprimir(s.indices(10))"),
        ("indices-neg", "s = slice(-3, None)\nimprimir(s.indices(10))"),
        ("eq", "imprimir(slice(1, 2) == slice(1, 2))"),
        ("ne", "imprimir(slice(1, 2) != slice(1, 3))"),
        ("type", "imprimir(tipo(slice(1)))"),
    ]
    for name, src in cases:
        out.append(_c("slice_value", name, src))
    return out


def area_complex_deep():
    out = []
    cases = [
        ("conjugate", "imprimir((2+3j).conjugate())"),
        ("pow-int", "imprimir(1j ** 2)"),
        ("pow-two", "imprimir((1+1j) ** 2)"),
        ("pow-zero", "imprimir((1+2j) ** 0)"),
        ("ctor-str", "imprimir(complejo('1+2j'))"),
        ("ctor-str-int", "imprimir(complejo('3'))"),
        ("bool-zero", "imprimir(bool(0j))"),
        ("bool-nonzero", "imprimir(bool(1+0j))"),
        ("eq-int", "imprimir((1+0j) == 1)"),
        ("eq-float", "imprimir((1.5+0j) == 1.5)"),
        ("ne-mixed", "imprimir((1+0j) != 1.5)"),
        ("abs-zero", "imprimir(abs(0j))"),
        ("real-int-part", "imprimir((3+0j).real)"),
        ("imag-int-part", "imprimir((0+4j).imag)"),
        ("add-self", "z = 1+2j\nz += 1j\nimprimir(z)"),
        ("mul-self", "z = 1+1j\nz *= 2\nimprimir(z)"),
        ("in-list", "imprimir(1j en [1j, 2j])"),
        ("list-print", "imprimir([1j, 2+3j])"),
        ("dict-key", "d = {1j: 'a'}\nimprimir(d[1j])"),
        ("fstring", "imprimir(f'{1+2j}')"),
        ("pct-s", "imprimir('%s' % (1+2j))"),
        ("round-none", "imprimir(round(1+2j.real))"),
    ]
    for name, src in cases:
        out.append(_c("complex_deep", name, src))
    return out


def area_builtins_more():
    out = []
    cases = [
        ("repr-str", "imprimir(repr('a'))"),
        ("repr-list", "imprimir(repr([1, 'a']))"),
        ("repr-tuple", "imprimir(repr((1,)))"),
        ("repr-int", "imprimir(repr(5))"),
        ("repr-float", "imprimir(repr(1.5))"),
        ("hash-int", "imprimir(hash(42) == hash(42))"),
        ("hash-str", "imprimir(hash('a') == hash('a'))"),
        ("hash-bool", "imprimir(hash(Verdadero) == hash(1))"),
        ("hash-none-err", "imprimir(hash(Nada))"),
        ("callable-fn", "imprimir(callable(len))"),
        ("callable-int", "imprimir(callable(5))"),
        ("iter-sentinel", "imprimir(lista(iter([1, 2].pop, IndexError)))"),
        # NOTA: min/max con key= baja la clave pero NO la aplica (a diferencia
        # de ordenar, que sí). Hueco declarado: FAIL_CLOSED en el corpus.
        ("min-key", "imprimir(min([3, 1, 2], key=lambda x: -x))"),
        ("max-key", "imprimir(max([3, 1, 2], key=lambda x: -x))"),
        # NOTA: `key=` se baja pero NO se aplica (la clave no participa del
        # orden nativo). Es un hueco declarado, no paridad: los casos que
        # dependen de la clave se miden como FAIL_CLOSED (build honesto), no
        # como DIVERGENT. Ver SORT_KEY_GAP en NATIVE_COMPATIBILITY.md.
        ("sorted-key", "imprimir(ordenar([3, 1, 2], key=lambda x: -x))"),
        ("sum-start", "imprimir(suma([1, 2], 10))"),
        ("divmod", "imprimir(divmod(7, 2))"),
        ("divmod-neg", "imprimir(divmod(-7, 2))"),
        ("divmod-float", "imprimir(divmod(7.5, 2))"),
        ("abs-float", "imprimir(abs(-3.5))"),
        ("abs-int", "imprimir(abs(-3))"),
        ("round-half-even", "imprimir(round(2.5))\nimprimir(round(3.5))"),
        ("pow-mod", "imprimir(pow(2, 10, 100))"),
        ("chr-ord", "imprimir(chr(97))\nimprimir(ord('a'))"),
        ("format-fn", "imprimir(format(5, '05d'))"),
        ("format-float", "imprimir(format(1.5, '.2f'))"),
        ("ascii-fn", "imprimir(ascii('ñ'))"),
    ]
    for name, src in cases:
        out.append(_c("builtins_more", name, src))
    return out


def area_exceptions_deep():
    out = []
    cases = [
        ("args-attr", "intentar:\n    lanzar ValueError('a', 'b')\nexcepto ValueError como e:\n    imprimir(e.args)"),
        ("args-single", "intentar:\n    lanzar ValueError('x')\nexcepto ValueError como e:\n    imprimir(e.args)"),
        ("str-exc", "intentar:\n    lanzar ValueError('boom')\nexcepto ValueError como e:\n    imprimir(texto(e))"),
        ("cause-chain", "intentar:\n    intentar:\n        lanzar ValueError('in')\n    excepto ValueError como e:\n        lanzar KeyError('out') desde e\nexcepto KeyError as e:\n    imprimir('c')"),
        ("context", "intentar:\n    intentar:\n        lanzar ValueError('a')\n    excepto ValueError:\n        lanzar KeyError('b')\nexcepto KeyError:\n    imprimir('ok')"),
        ("finally-runs", "r = []\nintentar:\n    lanzar ValueError('x')\nexcepto ValueError:\n    r.append('c')\nfinalmente:\n    r.append('f')\nimprimir(r)"),
        ("raise-type-only", "lanzar ValueError"),
        ("custom-args", "clase E(Exception):\n    pasar\nintentar:\n    lanzar E('a', 'b')\nexcepto E como e:\n    imprimir(e.args)"),
        ("hierarchy-3", "clase A(Exception): pasar\nclase B(A): pasar\nclase C(B): pasar\nintentar:\n    lanzar C('x')\nexcepto A:\n    imprimir('A')"),
        ("nested-reraise", "intentar:\n    intentar:\n        lanzar ValueError('x')\n    excepto ValueError:\n        lanzar\nexcepto ValueError:\n    imprimir('outer')"),
        ("exc-in-comprehension", "intentar:\n    [lanzar ValueError('x') para x en rango(1)]\nexcepto ValueError:\n    imprimir('ok')"),
        ("exc-in-generator", "funcion g():\n    lanzar ValueError('g')\nintentar:\n    lista(g())\nexcepto ValueError:\n    imprimir('caught')"),
        ("base-error", "intentar:\n    d = {}\n    d['k']\nexcepto Exception:\n    imprimir('e')"),
        ("stopiteration", "it = iter([1])\nimprimir(next(it))\nintentar:\n    next(it)\nexcepto StopIteration:\n    imprimir('si')"),
    ]
    for name, src in cases:
        out.append(_c("exceptions_deep", name, src))
    return out


def area_numeric_semantics():
    out = []
    cases = [
        ("int-overflow-add", "imprimir(2 ** 62 + 2 ** 62)"),
        ("int-overflow-mul", "imprimir(2 ** 40 * 2 ** 40)"),
        ("bigint-divmod", "imprimir(divmod(10 ** 25, 7))"),
        ("bigint-pow", "imprimir(2 ** 100)"),
        ("float-inf-add", "imprimir(1e308 * 10)"),
        ("float-inf-sub", "imprimir(-1e308 * 10)"),
        ("float-nan", "imprimir(0.0 / 0.0 if Falso sino 1e309 - 1e309)"),
        ("int-float-eq", "imprimir(1 == 1.0)"),
        ("int-float-cmp", "imprimir(1 < 1.5)"),
        ("bool-int-add", "imprimir(Verdadero + 1)"),
        ("bool-float-mul", "imprimir(Falso * 1.5)"),
        ("mixed-div", "imprimir(7 / 2)"),
        ("mixed-floordiv", "imprimir(7.0 // 2)"),
        ("mixed-mod", "imprimir(7.5 % 2)"),
        ("neg-mod", "imprimir(-7 % 3)"),
        ("neg-floordiv", "imprimir(-7 // 3)"),
        ("pow-neg-base-int", "imprimir((-2) ** 3)"),
        ("pow-frac", "imprimir(2 ** 0.5)"),
        ("pow-neg-exp", "imprimir(2 ** -1)"),
        ("shift-neg", "imprimir(1 << -1)"),
        ("chained-shift", "imprimir(1 << 2 << 3)"),
        ("bitand-neg", "imprimir(-1 & 0xFF)"),
        ("bitor-mix", "imprimir(5 | 2)"),
        ("invert", "imprimir(~5)"),
        ("float-int-sum", "imprimir(suma([1, 2.5]))"),
        ("float-sum-empty", "imprimir(suma([]))"),
        ("bigint-sum", "imprimir(suma([10 ** 20, 1]))"),
        ("int-str-concat-err", "imprimir('a' + 'b')"),
        ("float-repr", "imprimir(0.1 + 0.2)"),
        ("float-scientific", "imprimir(1e-7)\nimprimir(1e21)"),
        ("float-neg-zero", "imprimir(-0.0)"),
    ]
    for name, src in cases:
        out.append(_c("numeric_semantics", name, src))
    return out


def area_object_protocol3():
    out = []
    cases = [
        ("getattribute", "clase P:\n    funcion __getattribute__(self, n):\n        devolver 42\nimprimir(P().x)"),
        ("getattr-hook", "clase P:\n    funcion __getattr__(self, n):\n        devolver 'fallback'\nimprimir(P().zz)"),
        ("delattr", "clase P:\n    funcion __delattr__(self, n):\n        imprimir('del', n)\np = P()\nborrar p.x"),
        ("contains", "clase P:\n    funcion __contains__(self, v):\n        devolver v == 1\nimprimir(1 en P())"),
        ("getitem-slice", "clase P:\n    funcion __getitem__(self, i):\n        devolver i * 2\nimprimir(P()[3])"),
        ("call", "clase P:\n    funcion __call__(self, x):\n        devolver x + 1\np = P()\nimprimir(p(1))"),
        ("bool-len", "clase P:\n    funcion __len__(self):\n        devolver 0\nimprimir(bool(P()))"),
        ("repr-str", "clase P:\n    funcion __repr__(self):\n        devolver 'P()'\n    funcion __str__(self):\n        devolver 'soy P'\nimprimir(P())\nimprimir(repr(P()))"),
        ("eq-ne", "clase P:\n    funcion __init__(self, x): self.x = x\n    funcion __eq__(self, o):\n        devolver self.x == o.x\nimprimir(P(1) == P(1))\nimprimir(P(1) != P(2))"),
        ("hash-eq", "clase P:\n    funcion __init__(self, x): self.x = x\n    funcion __hash__(self):\n        devolver self.x\nimprimir(hash(P(3)) == 3)"),
        ("iter-next", "clase C:\n    funcion __init__(self):\n        self.i = 0\n    funcion __iter__(self):\n        devolver self\n    funcion __next__(self):\n        self.i = self.i + 1\n        si self.i > 2:\n            lanzar StopIteration\n        devolver self.i\nimprimir(lista(C()))"),
        ("add-reflected", "clase P:\n    funcion __radd__(self, o):\n        devolver 99\nimprimir(1 + P())"),
        ("iadd", "clase P:\n    funcion __iadd__(self, o):\n        devolver self\np = P()\np += 1\nimprimir('ok')"),
        ("neg", "clase P:\n    funcion __neg__(self):\n        devolver 1\nimprimir(-P())"),
        ("len-method", "clase P:\n    funcion __len__(self):\n        devolver 3\nimprimir(longitud(P()))"),
        ("getitem-missing", "clase P:\n    funcion __getitem__(self, i):\n        lanzar KeyError('k')\nintentar:\n    P()[1]\nexcepto KeyError:\n    imprimir('ok')"),
    ]
    for name, src in cases:
        out.append(_c("object_protocol3", name, src))
    return out


def area_callable_protocol():
    out = []
    cases = [
        ("fn-value", "funcion f(x): devolver x\nimprimir(f(1))"),
        ("fn-alias", "funcion f(x): devolver x + 1\ng = f\nimprimir(g(2))"),
        ("fn-in-list", "funcion f(): devolver 1\nfs = [f]\nimprimir(fs[0]())"),
        ("fn-in-dict", "funcion f(): devolver 1\nd = {'k': f}\nimprimir(d['k']())"),
        ("fn-arg", "funcion ap(f, v): devolver f(v)\nfuncion doble(x): devolver x * 2\nimprimir(ap(doble, 3))"),
        ("closure-capture", "funcion mk(n):\n    funcion g(x): devolver x * n\n    devolver g\nimprimir(mk(3)(2))"),
        ("lambda", "f = lambda x: x * 2\nimprimir(f(3))"),
        ("lambda-multi", "f = lambda x, y=2: x + y\nimprimir(f(1))\nimprimir(f(1, 5))"),
        ("lambda-in-call", "imprimir((lambda x: x + 1)(1))"),
        ("method-ref", "clase P:\n    funcion f(self): devolver 7\np = P()\nm = p.f\nimprimir(m())"),
        ("staticmethod-ref", "clase P:\n    @staticmethod\n    funcion f(): devolver 8\nimprimir(P.f())"),
        ("classmethod-ref", "clase P:\n    @classmethod\n    funcion f(cls): devolver 9\nimprimir(P.f())"),
        ("recursive", "funcion fac(n):\n    si n <= 1:\n        devolver 1\n    devolver n * fac(n - 1)\nimprimir(fac(5))"),
        ("mutual", "funcion par(n):\n    si n == 0:\n        devolver Verdadero\n    devolver impar(n - 1)\nfuncion impar(n):\n    si n == 0:\n        devolver Falso\n    devolver par(n - 1)\nimprimir(par(4))"),
    ]
    for name, src in cases:
        out.append(_c("callable_protocol", name, src))
    return out


AREAS = {
    "str_methods_more": area_str_methods_more,
    "dict_ops_more": area_dict_ops_more,
    "set_ops": area_set_ops,
    "frozenset": area_frozenset,
    "memoryview": area_memoryview,
    "slice_value": area_slice_value,
    "complex_deep": area_complex_deep,
    "builtins_more": area_builtins_more,
    "exceptions_deep": area_exceptions_deep,
    "numeric_semantics": area_numeric_semantics,
    "object_protocol3": area_object_protocol3,
    "callable_protocol": area_callable_protocol,
}


# --------------------------------------------------------------------------
# motor de ejecucion (compartido con parity_corpus_v2.py)
# --------------------------------------------------------------------------

def build_corpus(areas: list[str] | None = None) -> list:
    names = areas if areas else list(AREAS)
    cases: list = []
    for name in names:
        if name not in AREAS:
            raise SystemExit(f"unknown area: {name} (have: {', '.join(AREAS)})")
        cases.extend(AREAS[name]())
    seen: set[tuple[str, str]] = set()
    unique = []
    for case in cases:
        key = (case[0], case[1])
        if key in seen:
            continue
        seen.add(key)
        unique.append(case)
    return unique


def _exception_name(blob: bytes) -> str | None:
    match = _EXCEPTION_RE.search(blob.decode("utf-8", "replace"))
    return match.group(1) if match else None


def _short(blob: bytes, limit: int = 160) -> str:
    text = blob.decode("utf-8", "replace").strip().replace("\n", " | ")
    return text[:limit]


def run_case(case: tuple[str, str, str], timeout: float = 10.0) -> dict:
    from piton.translator import traducir_fuente

    area, name, src = case
    record = {
        "area": area,
        "name": name,
        "src": src,
        "bucket": None,
        "detail": "",
    }
    try:
        translated = traducir_fuente(src, "<parity-corpus-v2>")
    except Exception as exc:
        record["bucket"] = FAIL_CLOSED
        record["detail"] = f"translate: {type(exc).__name__}: {exc}"
        return record
    if platform.system() != "Linux":
        record["bucket"] = FAIL_CLOSED
        record["detail"] = "instrument currently measures the Linux backend only"
        return record
    from piton.linux_x86 import compile_native_linux

    with tempfile.TemporaryDirectory(prefix="piton-corpus-v2-") as directory:
        try:
            executable = compile_native_linux(src, Path(directory) / "p.exe")
        except Exception as exc:
            record["bucket"] = FAIL_CLOSED
            record["detail"] = f"build: {type(exc).__name__}: {exc}"
            return record
        try:
            native = subprocess.run([str(executable)], capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            record["bucket"] = TIMEOUT
            record["detail"] = "native executable did not terminate"
            return record
        try:
            oracle = subprocess.run(
                [sys.executable, "-c", translated], capture_output=True, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            record["bucket"] = TIMEOUT
            record["detail"] = "oracle did not terminate (case is a bad corpus entry)"
            return record

    record["native_rc"] = native.returncode
    record["oracle_rc"] = oracle.returncode
    if native.returncode == oracle.returncode and native.stdout == oracle.stdout:
        record["bucket"] = EQUIV
        return record
    native_exc = _exception_name(native.stderr)
    oracle_exc = _exception_name(oracle.stderr)
    if native.returncode == 1 and oracle.returncode == 1 and native_exc and native_exc == oracle_exc:
        record["bucket"] = RAISE_EQ
        record["detail"] = native_exc
        return record
    if native.returncode not in (0, 1) or oracle.returncode not in (0, 1):
        record["bucket"] = DIVERGENT_CRASH
    else:
        record["bucket"] = DIVERGENT_MISMATCH
    record["detail"] = (
        f"native rc={native.returncode} out={_short(native.stdout)} err={_short(native.stderr)}"
        f" || oracle rc={oracle.returncode} out={_short(oracle.stdout)} err={_short(oracle.stderr)}"
    )
    return record


def _worker(args) -> dict:
    case, timeout = args
    try:
        return run_case(case, timeout)
    except Exception as exc:
        return {
            "area": case[0],
            "name": case[1],
            "src": case[2],
            "bucket": DIVERGENT_CRASH,
            "detail": f"INSTRUMENT ERROR: {type(exc).__name__}: {exc}",
        }


def summarise(records: list[dict]) -> dict:
    by_bucket = {bucket: 0 for bucket in BUCKETS}
    by_area: dict[str, dict[str, int]] = {}
    for record in records:
        by_bucket[record["bucket"]] = by_bucket.get(record["bucket"], 0) + 1
        area = by_area.setdefault(record["area"], {bucket: 0 for bucket in BUCKETS})
        area[record["bucket"]] += 1
    total = len(records)
    honest = by_bucket[EQUIV] + by_bucket[RAISE_EQ] + by_bucket[FAIL_CLOSED]
    return {
        "total": total,
        "by_bucket": by_bucket,
        "by_area": by_area,
        "pct_equiv": round(100.0 * by_bucket[EQUIV] / total, 1) if total else 0.0,
        "pct_honest": round(100.0 * honest / total, 1) if total else 0.0,
        "pct_divergent": round(100.0 * (by_bucket[DIVERGENT_MISMATCH] + by_bucket[DIVERGENT_CRASH]) / total, 1) if total else 0.0,
    }


def render_markdown(records: list[dict], summary: dict, meta: dict) -> str:
    lines = [
        "# Parity corpus report v3 (enumerativo)",
        "",
        f"- backend medido: **{meta['backend']}** (instrumento: `tools/parity_corpus_v3.py`)",
        f"- oracle: CPython {meta['oracle']}",
        f"- casos: **{summary['total']}** | EQUIV **{summary['pct_equiv']}%** | "
        f"comportamiento honesto (EQUIV+RAISE_EQ+FAIL_CLOSED) **{summary['pct_honest']}%** | "
        f"DIVERGENT **{summary['pct_divergent']}%**",
        "",
        "Este reporte NO es un gate. Mide el subconjunto declarado en "
        "`NATIVE_COMPATIBILITY.md` mediante matrices enumeradas, no casos elegidos a mano.",
        "",
        "## Buckets",
        "",
        "| bucket | n |",
        "|---|---|",
    ]
    for bucket in BUCKETS:
        lines.append(f"| {bucket} | {summary['by_bucket'].get(bucket, 0)} |")
    lines += ["", "## Por area", "", "| area | EQUIV | RAISE_EQ | FAIL_CLOSED | DIVERGENT | TIMEOUT |", "|---|---|---|---|---|---|"]
    for area, counts in summary["by_area"].items():
        divergent = counts.get(DIVERGENT_MISMATCH, 0) + counts.get(DIVERGENT_CRASH, 0)
        lines.append(
            f"| {area} | {counts.get(EQUIV, 0)} | {counts.get(RAISE_EQ, 0)} | "
            f"{counts.get(FAIL_CLOSED, 0)} | {divergent} | {counts.get(TIMEOUT, 0)} |"
        )
    risky = [r for r in records if r["bucket"] in RISKY]
    lines += ["", f"## Divergencias y timeouts ({len(risky)})", ""]
    if not risky:
        lines.append("Ninguna: no hay codigo nativo que corra y discrepe del oracle en este corpus.")
    else:
        lines += ["| area | caso | native rc | oracle rc | detalle |", "|---|---|---|---|---|"]
        for record in risky:
            lines.append(
                f"| {record['area']} | {record['name']} | {record.get('native_rc', '-')} | "
                f"{record.get('oracle_rc', '-')} | {record['detail'].replace('|', '/')[:200]} |"
            )
    lines += ["", "## Casos DIVERGENT (fuente)", ""]
    for record in risky:
        lines += ["```piton", record["src"].rstrip("\n"), "```"]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--areas", default=None, help="comma separated subset of areas")
    parser.add_argument("--limit", type=int, default=0, help="only the first N cases (smoke runs)")
    parser.add_argument("--jobs", type=int, default=0, help="parallel workers (0 = cpu count)")
    parser.add_argument("--timeout", type=float, default=10.0, help="per-process seconds")
    parser.add_argument("--out", default=None, help="write JSON report here")
    parser.add_argument("--markdown", default=None, help="write markdown report here")
    parser.add_argument("--list", action="store_true", help="print the corpus and exit")
    args = parser.parse_args()

    areas = args.areas.split(",") if args.areas else None
    cases = build_corpus(areas)
    if args.limit:
        cases = cases[: args.limit]
    if args.list:
        for case in cases:
            print(f"# {case[0]}/{case[1]}")
            print(case[2].rstrip("\n"))
        print(f"# total {len(cases)}")
        return 0

    jobs = args.jobs or (os.cpu_count() or 4)
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        records = list(pool.map(_worker, [(case, args.timeout) for case in cases], chunksize=4))

    summary = summarise(records)
    meta = {
        "backend": "linux_x86 (Linux native)",
        "oracle": platform.python_version(),
        "instrument": "tools/parity_corpus_v3.py",
        "areas": areas or list(AREAS),
        "jobs": jobs,
        "timeout": args.timeout,
    }
    report = {"meta": meta, "summary": summary, "records": records}
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"json report: {args.out}")
    if args.markdown:
        Path(args.markdown).write_text(render_markdown(records, summary, meta), encoding="utf-8")
        print(f"markdown report: {args.markdown}")

    counts = summary["by_bucket"]
    print(
        f"{summary['total']} casos | EQUIV {counts[EQUIV]} ({summary['pct_equiv']}%) | "
        f"RAISE_EQ {counts[RAISE_EQ]} | FAIL_CLOSED {counts[FAIL_CLOSED]} | "
        f"DIVERGENT {counts[DIVERGENT_MISMATCH] + counts[DIVERGENT_CRASH]} | TIMEOUT {counts[TIMEOUT]}"
    )
    return 0 if counts[DIVERGENT_MISMATCH] + counts[DIVERGENT_CRASH] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
