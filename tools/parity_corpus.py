#!/usr/bin/env python3
"""parity_corpus - corpus diferencial enumerativo PITON vs CPython 3.12.4.

INSTRUMENTO DE MEDICION, no un gate. Responde una pregunta por caso: dado un
programa construido a partir de la superficie nativa declarada, el backend
nativo coincide con el oracle?

Por que enumerativo y no "a mano": un corpus escrito a mano mide lo que su
autor Thickness个子 recuerda. Este generador recorre MATRICES (operadores x
operandos, comparadores x pares, slices x tripletas, metodos x argumentos), de
modo que el hueco encontrado no depende de mi memoria de lo implementado.
Las tablas de alias y builtins se LEEN del compilador, para que el corpus no
pueda derivar de la superficie real.

Buckets, ordenados por riesgo:

  DIVERGENT_CRASH     el nativo corrio y discrepo de forma grave (rc no 0/1)
  DIVERGENT_MISMATCH  el nativo corrio y su salida NO coincide con el oracle
  TIMEOUT             el nativo (o el oracle) no termina
  FAIL_CLOSED         rechazo honesto en translate/build/parse (no ejecuta)
  RAISE_EQ            ambos lanzan el mismo tipo de excepcion, rc 1
  EQUIV               rc identico y stdout byte-identico

FAIL_CLOSED no es un fallo: es el comportamiento correcto para lo no
soportado (ver NATIVE_COMPATIBILITY.md). DIVERGENT si lo es: es codigo que
corre y mente.

Ejecucion:

    python3 tools/parity_corpus.py --out docs/parity_corpus_report.json
    python3 tools/parity_corpus.py --areas collections,slices --list
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
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Case:
    area: str
    name: str
    src: str


# --------------------------------------------------------------------------
# matrices de operandos
# --------------------------------------------------------------------------

INT_PAIRS = [(1, 2), (7, 3), (0, 5), (5, 0), (-4, 3), (10, 2), (2, -3)]
FLOAT_PAIRS = [(1.5, 2.0), (0.0, 0.0), (-1.5, 0.0), (2.5, -0.5), (1.0, 3.0)]
ARITH_OPS = ["+", "-", "*", "/", "//", "%", "**", "&", "|", "^", "<<", ">>"]
FLOAT_OPS = ["+", "-", "*", "/", "//", "%", "**"]
CMP_OPS = ["==", "!=", "<", ">", "<=", ">="]
IDENT_OPS = ["es", "no es"]
# Source fragments, NOT python values: f-string interpolation of a python str
# yields a bare identifier (`imprimir(a == a)` -> 'a' undeclared), which looked
# like a compiler bug in the report but was a corpus bug.
CMP_PAIRS = [
    ("1", "1.0"),
    ("1", "'1'"),
    ("0", "1"),
    ("Falso", "1"),
    ("1", "Verdadero"),
    ("'a'", "'a'"),
    ("''", "''"),
    ("'a'", "'b'"),
    ("Nada", "Nada"),
    ("'1'", "1"),
]

TRUTHY_VALUES = [
    ("cero", "0"),
    ("uno", "1"),
    ("neg", "-1"),
    ("fzero", "0.0"),
    ("fhalf", "0.5"),
    ("emptystr", '""'),
    ("str", '"x"'),
    ("falso", "Falso"),
    ("verdadero", "Verdadero"),
    ("nada", "Nada"),
    ("emptylist", "[]"),
    ("listzero", "[0]"),
    ("emptytuple", "()"),
    ("emptydict", "{}"),
    ("list", "[1, 2]"),
]
INT_BUILTINS = [
    ("abs", [("-3",), ("2.5",), ("Nada",)]),
    ("abs", [("-3.5",)]),
    ("abs", [("decimal('-3.5')",)]),
    ("all", [("[]",), ("[1, 2]",), ("[0]",), ("['a', '']",)]),
    ("any", [("[]",), ("[0]",), ("[0, 1]",), ("['', 'a']",)]),
    ("bin", [("5",), ("0",), ("-3",)]),
    ("chr", [("65",), ("0",), ("1114111",)]),
    ("longitud", [("[1, 2]",), ('"abc"',), ("{}",), ("()",), ("(rango(3),)",)]),
    ("ord", [("'A'",), ("'0'",)]),
    ("entero", [("'12'",), ('"1.5"',), ("3.9",), ("-2.7",), ("Verdadero",)]),
    ("decimal", [("'1.5'",), ("2",), ("'abc'",)]),
    ("texto", [("12",), ("1.5",), ("Verdadero",), ("Nada",)]),
    ("booleano", [("0",), ("1",), ("''",), ("[]",), ("[0]",)]),
    ("tipo", [("1",), ("'a'",), ("1.0",), ("[1]",), ("{}",)]),
    ("rango", [("3",), ("0",), ("1, 4",), ("0, 6, 2",)]),
    ("lista", [("(1, 2)",), ("'ab'",), ("rango(3)",), ("()",)]),
    ("tupla", [("[1, 2]",), ("'ab'",)]),
    ("diccionario", [("()",), ("([(1, 2)])",), ("([('a', 1)])",)]),
    # sets of STRS are order-unstable across CPython processes (hash seed):
    # printing one is not byte-comparable, so only int/range inputs stay here
    ("conjunto", [("[1, 2, 2]",), ("rango(3)",)]),
    ("sum", [("[1, 2, 3]",), ("rango(4)",), ("('a', 'b')",)]),
    ("min", [("[3, 1, 2]",), ("'abc'",), ("[1.5, 2]",), ("[[1], [2]]",)]),
    ("max", [("[3, 1, 2]",), ("'abc'",), ("[1.5, 2]",)]),
    ("ordenar", [("[3, 1, 2]",), ("'cba'",), ("rango(3)",)]),
    # enumerar(...) imprime un <enumerate object at 0x...>: la direccion
    # cambia en cada proceso, asi que comparar stdout no significa nada
    # (mismo motivo por el que un set de strings salio del corpus). La
    # cobertura de enumerar se mide iterando, no imprimiendo el objeto.
    ("enumerar", []),
    ("pow", [("(2, 10)",), ("(2, 0)",), ("(2.0, 0.5)",), ("(2, -1)",), ("(2, 1000)",)]),
    ("redondear", [("(2.567, 2)",), ("(2.5,)",), ("(3.14159, 3)",)]),
    ("pow", [("(2, 62)",)]),
    ("pow", [("(2, 64)",)]),
]
STR_METHODS = [
    # (label, full call source). The receiver is part of the fragment: an
    # earlier version passed it as the first argument and generated
    # `'abc'.upper('Hola mundo',)`, which is a PARSE error, not a gap.
    ("upper", "'Hola mundo'.upper()"),
    ("lower", "'Hola Mundo'.lower()"),
    ("strip", "'  x  '.strip()"),
    ("lstrip", "'xxay'.lstrip('x')"),
    ("rstrip", "'ayxx'.rstrip('x')"),
    ("split", "'a b  c'.split()"),
    ("split-sep", "'a,b'.split(',')"),
    ("join", "','.join(['a', 'b'])"),
    ("replace", "'aXa'.replace('X', 'y')"),
    ("startswith", "'abc'.startswith('a')"),
    ("endswith", "'abc'.endswith('c')"),
    ("find", "'abcabc'.find('bc')"),
    ("rfind", "'abcabc'.rfind('bc')"),
    ("count", "'aaa'.count('a')"),
    ("index", "'abc'.index('b')"),
    ("rindex", "'abcb'.rindex('b')"),
    ("capitalize", "'hola MUNDO'.capitalize()"),
    ("title", "'hola mundo'.title()"),
    ("swapcase", "'Hola 123'.swapcase()"),
    ("isalpha", "'abc'.isalpha()"),
    ("isalpha-digit", "'ab1'.isalpha()"),
    ("isdigit", "'123'.isdigit()"),
    ("isalnum", "'ab1'.isalnum()"),
    ("isspace", "'  '.isspace()"),
    ("istitle", "'Hola Mundo'.istitle()"),
    ("isupper", "'ABC'.isupper()"),
    ("islower", "'abc'.islower()"),
    ("zfill", "'42'.zfill(5)"),
    ("ljust", "'x'.ljust(4)"),
    ("rjust", "'x'.rjust(4)"),
    ("center", "'x'.center(5)"),
    ("format", "'{} {}'.format(1, 2)"),
]
FMT_SPECS = [
    "'{:>5}'", "'{:<5}'", "'{:^5}'", "'{:05d}'", "'{:+d}'", "'{: d}'",
    "'{:,}'.format(1234567)", "'{:.3f}'", "'{:e}'", "'{:g}'", "'{:%}'",
    "'{:x}'.format(255)", "'{:X}'.format(255)", "'{:o}'.format(8)", "'{:b}'.format(5)",
    "'{:08.2f}'", "'{:*^7}'", "'{:,d}'.format(1234567)",
    "'{0} {1} {0}'.format('a', 'b')", "'{{}}'", "'{{{}}}'.format(1)",
]
PCT_TYPES = [
    ("%d", "5"), ("%i", "5"), ("%s", "'txt'"), ("%r", "'txt'"), ("%f", "1.5"),
    ("%e", "1.5"), ("%g", "1.5"), ("%G", "1.5"), ("%x", "255"), ("%X", "255"),
    ("%o", "8"), ("%c", "'A'"), ("%%", "0"),
    ("%(k)s", "{'k': 3}"), ("%-5d|", "5"), ("%5d|", "5"), ("%05d", "5"),
    ("%.2f", "1.567"), ("%08.3f", "1.5"), ("%.3s", "'abcdef'"),
]


def _c(area: str, name: str, src: str) -> Case:
    return Case(area, name, src if src.endswith("\n") else src + "\n")


# --------------------------------------------------------------------------
# generadores por area
# --------------------------------------------------------------------------

def area_literals() -> list[Case]:
    out = [
        _c("literals", "int", "imprimir(42)"),
        _c("literals", "negint", "imprimir(-42)"),
        _c("literals", "bigint", "imprimir(123456789012345678901234567890)"),
        _c("literals", "float", "imprimir(1.5)"),
        _c("literals", "floatexp", "imprimir(1e3)"),
        _c("literals", "bool", "imprimir(Verdadero)\nimprimir(Falso)"),
        _c("literals", "none", "imprimir(Nada)"),
        _c("literals", "str", "imprimir('hola')"),
        _c("literals", "strescapes", r"""imprimir('a\nb\tc\\d\'e')"""),
        _c("literals", "strunicode", "imprimir('áéíóú ñ 中文')"),
        _c("literals", "list", "imprimir([1, 2, 3])"),
        _c("literals", "tuple", "imprimir((1, 'a', 2.0))"),
        _c("literals", "dict", "imprimir({'a': 1, 'b': 2})"),
        _c("literals", "set", "imprimir({1, 2, 2, 3})"),
        _c("literals", "nested", "imprimir([1, (2, 3), {'k': [4]}, {5, 6}])"),
        _c("literals", "empties", "imprimir([])\nimprimir(())\nimprimir({})\nimprimir('')"),
    ]
    return out


def area_arithmetic() -> list[Case]:
    out = []
    for op in ARITH_OPS:
        for i, (a, b) in enumerate(INT_PAIRS):
            out.append(_c("arithmetic", f"int{op}{i}", f"imprimir({a} {op} {b})"))
    for op in FLOAT_OPS:
        for i, (a, b) in enumerate(FLOAT_PAIRS):
            out.append(_c("arithmetic", f"float{op}{i}", f"imprimir({a} {op} {b})"))
    out += [
        _c("arithmetic", "mixed-add", "imprimir(1 + 2.5)"),
        _c("arithmetic", "mixed-mul", "imprimir(2 * 1.5)"),
        _c("arithmetic", "unary-minus", "imprimir(-(3))"),
        _c("arithmetic", "unary-plus", "imprimir(+3)"),
        _c("arithmetic", "unary-minus-float", "imprimir(-1.5)"),
        _c("arithmetic", "chained-arith", "imprimir(1 + 2 * 3 - 4 // 2)"),
        _c("arithmetic", "paren", "imprimir((1 + 2) * (3 - 1))"),
        _c("arithmetic", "pow-right-neg", "imprimir(2 ** -1)"),
        _c("arithmetic", "pow-zero-zeropow", "imprimir(0 ** 0)"),
        _c("arithmetic", "div-zero", "imprimir(1 // 0)"),
        _c("arithmetic", "mod-zero", "imprimir(1 % 0)"),
        _c("arithmetic", "bigint-mul", "x = 10 ** 30\nimprimir(x * x)"),
        _c("arithmetic", "int-overflow", "imprimir(2 ** 63)"),
        _c("arithmetic", "int-neg", "imprimir(-2 ** 2)"),
    ]
    return out


def area_comparisons() -> list[Case]:
    out = []
    for op in CMP_OPS:
        for i, (a, b) in enumerate(CMP_PAIRS):
            out.append(_c("comparisons", f"{op}{i}", f"imprimir({a} {op} {b})"))
    for op in IDENT_OPS:
        for i, (a, b) in enumerate(CMP_PAIRS[:6]):
            out.append(_c("comparisons", f"id{op}{i}", f"imprimir({a} {op} {b})"))
    # matriz de comparacion encadenada: todos los pares de operadores
    for o1 in CMP_OPS:
        for o2 in CMP_OPS:
            out.append(
                _c("comparisons", f"chain{o1}{o2}", f"imprimir(1 {o1} 2 {o2} 1)")
            )
    out += [
        _c("comparisons", "chain-eval", "funcion f(x):\n    imprimir('f')\n    devolver x\nimprimir(f(1) == f(1) == 1)"),
        _c("comparisons", "chain-mixed", "imprimir(1 < 2 < 3)"),
        _c("comparisons", "chain-false", "imprimir(1 < 2 < 1)"),
    ]
    return out


def area_truthiness() -> list[Case]:
    out = []
    contexts = [
        ("si", "si v:\n    imprimir('T')\nsino:\n    imprimir('F')"),
        ("y", "imprimir(v y 'T' o 'F')"),
        ("o", "imprimir(v o 'F')"),
        ("no", "imprimir(no v)"),
        ("sino", "si no v:\n    imprimir('T')\nsino:\n    imprimir('F')"),
    ]
    for name, value in TRUTHY_VALUES:
        for cname, template in contexts:
            body = template.replace("v", value)
            out.append(_c("truthiness", f"{cname}-{name}", f"v = {value}\n" + body))
    return out


def area_builtins() -> list[Case]:
    out = []
    for fn, shapes in INT_BUILTINS:
        for i, (args,) in enumerate(shapes):
            out.append(_c("builtins", f"{fn}{i}", f"imprimir({fn}({args}))"))
    return out


def area_strings() -> list[Case]:
    out = []
    for label, call in STR_METHODS:
        out.append(_c("strings", label, f"imprimir({call})"))
    out += [
        _c("strings", "concat", "imprimir('a' + 'b')"),
        _c("strings", "mul", "imprimir('ab' * 3)"),
        _c("strings", "cmp", "imprimir('a' < 'b')"),
        _c("strings", "eq", "imprimir('a' == 'a')"),
        _c("strings", "ineq", "imprimir('a' != 'b')"),
        _c("strings", "len", "imprimir(longitud('hola'))"),
        _c("strings", "in", "imprimir('a' in 'abc')"),
        _c("strings", "iter", "imprimir([c para c en 'abc'])"),
        _c("strings", "index", "imprimir('abc'[1])"),
        _c("strings", "negindex", "imprimir('abc'[-1])"),
        _c("strings", "not-in", "imprimir('z' no en 'abc')"),
    ]
    return out


def area_formatting() -> list[Case]:
    out = []
    for spec in FMT_SPECS:
        call = spec if ".format" in spec else spec + ".format(42)"
        out.append(_c("formatting", f"fmt{spec}", f"imprimir({call})"))
    for i, (spec, value) in enumerate(PCT_TYPES):
        out.append(_c("formatting", f"pct{spec}", f"imprimir('{spec}' % ({value}))"))
    out += [
        _c("formatting", "fstring-basic", "x = 5\nimprimir(f'x={x}')"),
        _c("formatting", "fstring-expr", "imprimir(f'{1 + 2}')"),
        _c("formatting", "fstring-spec-d", "imprimir(f'{42:5d}')"),
        _c("formatting", "fstring-spec-f", "imprimir(f'{1.5:.2f}')"),
        _c("formatting", "fstring-nested", "imprimir(f\"{'a' + 'b'}\")"),
        _c("formatting", "fstring-escape", "imprimir(f'{{literal}}')"),
        _c("formatting", "format-nested", "imprimir('{0[1]}'.format([9, 8]))"),
        _c("formatting", "format-attr", "imprimir('{0.real}'.format(1.5))"),
    ]
    return out


def area_slices() -> list[Case]:
    """Matriz completa start/end/step sobre una lista de 5 elementos."""
    out = []
    base = "[10, 20, 30, 40, 50]"
    for start in (None, "0", "1", "2", "4", "5", "-1", "-6"):
        for end in (None, "0", "1", "3", "5", "9", "-1", "-7"):
            for step in (None, "1", "2", "3", "-1", "-2"):
                inner = f"{start if start is not None else ''}:{end if end is not None else ''}"
                if step is not None:
                    inner = f"{inner}:{step}"
                src = "a = " + base + f"\nimprimir(a[{inner}])"
                name = f"slice{start}-{end}-{step}"
                out.append(_c("slices", name, src))
    for label, src in (("str", "'abcdefg'"), ("range", "rango(7)")):
        for bounds in ("[1:4]", "[:3]", "[::2]", "[::-1]", "[-2:]"):
            out.append(_c("slices", f"{label}{bounds}", f"a = {src}\nimprimir(a{bounds})"))
    return out


def area_collections() -> list[Case]:
    return [
        _c("collections", "len", "imprimir(longitud([1, 2, 3]))"),
        _c("collections", "index", "imprimir([1, 2, 3][1])"),
        _c("collections", "negindex", "imprimir([1, 2, 3][-1])"),
        _c("collections", "index-oob", "imprimir([1, 2, 3][9])"),
        _c("collections", "concat", "imprimir([1] + [2, 3])"),
        _c("collections", "tuple-concat", "imprimir((1,) + (2,))"),
        _c("collections", "repeat", "imprimir([1, 2] * 2)"),
        _c("collections", "repeat-tuple", "imprimir((1, 2) * 3)"),
        _c("collections", "repeat-zero", "imprimir([1] * 0)"),
        _c("collections", "repeat-neg", "imprimir([1] * -3)"),
        _c("collections", "in", "imprimir(2 en [1, 2])"),
        _c("collections", "not-in", "imprimir(9 no en [1, 2])"),
        _c("collections", "append", "a = [1]\na.append(2)\nimprimir(a)"),
        _c("collections", "insert", "a = [1, 3]\na.insert(1, 2)\nimprimir(a)"),
        _c("collections", "pop", "a = [1, 2, 3]\nimprimir(a.pop())\nimprimir(a)"),
        _c("collections", "pop-index", "a = [1, 2, 3]\nimprimir(a.pop(0))"),
        _c("collections", "remove", "a = [1, 2, 3]\na.remove(2)\nimprimir(a)"),
        _c("collections", "extend", "a = [1]\na.extend([2, 3])\nimprimir(a)"),
        _c("collections", "clear", "a = [1, 2]\na.clear()\nimprimir(a)"),
        _c("collections", "copy", "a = [1, 2]\nb = a.copy()\nb.append(3)\nimprimir(a)\nimprimir(b)"),
        _c("collections", "nested-index", "imprimir([[1, 2], [3, 4]][1][0])"),
        _c("collections", "nested-len", "imprimir(longitud([[1], [2, 3]]))"),
        _c("collections", "for-loop", "a = [1, 2]\npara x en a:\n    imprimir(x)"),
        _c("collections", "for-enumerate", "para i, v en enumerar(['a', 'b']):\n    imprimir(i)\n    imprimir(v)"),
        _c("collections", "for-range", "para i en rango(3):\n    imprimir(i)"),
        _c("collections", "for-str", "para c en 'ab':\n    imprimir(c)"),
        _c("collections", "for-dict", "para k en {'a': 1}:\n    imprimir(k)"),
        _c("collections", "sum", "imprimir(sum([1, 2, 3]))"),
        _c("collections", "minmax-list", "imprimir(min([3, 1]))\nimprimir(max([3, 1]))"),
        _c("collections", "minmax-str", "imprimir(min('cba'))\nimprimir(max('cba'))"),
        _c("collections", "sorted-list", "imprimir(ordenar([3, 1, 2]))"),
        _c("collections", "sorted-str", "imprimir(ordenar('cba'))"),
        _c("collections", "sorted-kw", "imprimir(ordenar([3, 1, 2], reversa=Verdadero))"),
        _c("collections", "list-of-list-print", "imprimir([[1, 2], [3]])\nimprimir({'a': [1]})"),
    ]


def area_dicts() -> list[Case]:
    return [
        _c("dicts", "keys", "d = {'a': 1, 'b': 2}\npara k en d.keys():\n    imprimir(k)"),
        _c("dicts", "values", "d = {'a': 1, 'b': 2}\npara v en d.values():\n    imprimir(v)"),
        _c("dicts", "items", "d = {'a': 1}\npara k, v en d.items():\n    imprimir(k)\n    imprimir(v)"),
        _c("dicts", "get", "d = {'a': 1}\nimprimir(d.get('a'))\nimprimir(d.get('z'))"),
        _c("dicts", "get-default", "d = {}\nimprimir(d.get('a', 5))"),
        _c("dicts", "get-callable-default", "d = {}\nimprimir(d.get('a', list))"),
        _c("dicts", "in", "d = {'a': 1}\nimprimir('a' en d)"),
        _c("dicts", "not-in", "d = {'a': 1}\nimprimir('z' no en d)"),
        _c("dicts", "len", "imprimir(longitud({'a': 1, 'b': 2}))"),
        _c("dicts", "iter-keys", "para k en {'a': 1, 'b': 2}:\n    imprimir(k)"),
        _c("dicts", "iter-values-view", "imprimir([k para k en {'a': 1}])"),
        _c("dicts", "del-key", "d = {'a': 1, 'b': 2}\nborrar d['a']\nimprimir(d)"),
        _c("dicts", "assign", "d = {}\nd['a'] = 1\nimprimir(d)"),
        _c("dicts", "update", "d = {'a': 1}\nd.update({'b': 2})\nimprimir(d)"),
        _c("dicts", "keys-sorted", "d = {'b': 1, 'a': 2}\nimprimir(ordenar(d.keys()))"),
        _c("dicts", "values-list", "d = {'b': 2, 'a': 1}\nimprimir(ordenar(d.values()))"),
        _c("dicts", "items-sorted", "d = {'b': 2, 'a': 1}\npara k, v en ordenar(d.items()):\n    imprimir(k)\n    imprimir(v)"),
        _c("dicts", "missing-key", "d = {}\nimprimir(d['z'])"),
        _c("dicts", "int-keys", "d = {1: 'a', 2: 'b'}\npara k en d:\n    imprimir(k)"),
        _c("dicts", "comp-over-dict", "d = {'a': 1, 'b': 2}\nimprimir({k: v * 10 para k, v en d.items()})"),
    ]


def area_sets() -> list[Case]:
    return [
        _c("sets", "add", "s = {1}\ns.add(2)\nimprimir(s)"),
        _c("sets", "in", "imprimir(1 en {1, 2})"),
        _c("sets", "not-in", "imprimir(3 no en {1, 2})"),
        _c("sets", "len", "imprimir(longitud({1, 2, 2}))"),
        _c("sets", "iter", "para x en {1}:\n    imprimir(x)"),
        _c("sets", "comp", "imprimir({x * 2 para x en rango(4)})"),
        _c("sets", "discard", "s = {1, 2}\ns.discard(1)\nimprimir(s)"),
        _c("sets", "remove", "s = {1, 2}\ns.remove(1)\nimprimir(s)"),
        _c("sets", "from-list", "imprimir(conjunto([1, 1, 2]))"),
    ]


def area_comprehensions() -> list[Case]:
    out = []
    iters = [
        ("rango", "rango(4)"),
        ("lista", "[1, 2, 3]"),
        ("cadena", "'ab'"),
        ("diccionario", "{'a': 1}"),
        ("enumerado", "enumerar(['x', 'y'])"),
        ("ordenado", "ordenar([2, 1])"),
    ]
    for iname, src_iter in iters:
        out.append(_c("comprehensions", f"list-{iname}", f"imprimir([x para x en {src_iter}])"))
        out.append(_c("comprehensions", f"listexpr-{iname}", f"imprimir([x + 1 para x en {src_iter}])"))
        out.append(_c("comprehensions", f"listfilter-{iname}", f"imprimir([x para x en {src_iter} si x])"))
    out += [
        _c("comprehensions", "set", "imprimir({x % 2 para x en rango(5)})"),
        _c("comprehensions", "dict", "imprimir({x: x * 2 para x en rango(3)})"),
        _c("comprehensions", "genexp-sum", "imprimir(sum(x para x en rango(4)))"),
        _c("comprehensions", "genexp-list", "imprimir(lista(x para x en rango(3)))"),
        _c("comprehensions", "nested", "imprimir([y para x en rango(3) para y en rango(x)])"),
        _c("comprehensions", "list-tuple", "imprimir([(x, y) para x en rango(2) para y en rango(2)])"),
        _c("comprehensions", "unpack-tuple", "imprimir([a + b para a, b en [(1, 2), (3, 4)]])"),
        _c("comprehensions", "cond-expr", "imprimir([x si x > 1 sino -x para x en rango(3)])"),
        _c("comprehensions", "enumerar-tupla", "imprimir([(i, v) para i, v en enumerar('ab')])"),
    ]
    return out


def area_control_flow() -> list[Case]:
    return [
        _c("control_flow", "if", "si 1 < 2:\n    imprimir('a')\nsino:\n    imprimir('b')"),
        _c("control_flow", "elif", "x = 3\nsi x == 1:\n    imprimir('uno')\nsino_si x == 2:\n    imprimir('dos')\nsino_si x == 3:\n    imprimir('tres')\nsino:\n    imprimir('otro')"),
        _c("control_flow", "while", "i = 0\nmientras i < 3:\n    imprimir(i)\n    i = i + 1"),
        _c("control_flow", "while-break", "i = 0\nmientras Verdadero:\n    i = i + 1\n    si i == 2:\n        romper\nimprimir(i)"),
        _c("control_flow", "while-continue", "i = 0\nmientras i < 5:\n    i = i + 1\n    si i % 2 == 0:\n        continuar\n    imprimir(i)"),
        _c("control_flow", "for-else", "para x en [1, 2]:\n    imprimir(x)\nsino:\n    imprimir('fin')"),
        _c("control_flow", "for-break", "para x en [1, 2, 3]:\n    si x == 2:\n        romper\n    imprimir(x)"),
        _c("control_flow", "for-continue", "para x en [1, 2, 3]:\n    si x == 2:\n        continuar\n    imprimir(x)"),
        _c("control_flow", "nested-for", "para x en rango(2):\n    para y en rango(2):\n        imprimir(x * 10 + y)"),
        _c("control_flow", "break-inner", "para x en rango(2):\n    para y en rango(3):\n        romper\n    imprimir(x)"),
        _c("control_flow", "pass", "si Verdadero:\n    pasar\nimprimir('ok')"),
        _c("control_flow", "augassign", "x = 5\nx += 3\nx -= 1\nx *= 2\nx //= 3\nimprimir(x)"),
        _c("control_flow", "chained-assign", "a = b = 7\nimprimir(a)\nimprimir(b)"),
        _c("control_flow", "multi-assign", "a, b = 1, 2\nimprimir(a)\nimprimir(b)"),
        _c("control_flow", "swap", "a, b = 1, 2\na, b = b, a\nimprimir(a)\nimprimir(b)"),
        _c("control_flow", "starred", "a, *r = [1, 2, 3]\nimprimir(a)\nimprimir(r)"),
        _c("control_flow", "walrus", "si (n := 5) > 1:\n    imprimir(n)"),
        _c("control_flow", "ternary", "imprimir('si' si 1 < 2 sino 'no')"),
        _c("control_flow", "del-local", "a = [1, 2]\nborrar a[0]\nimprimir(a)"),
        _c("control_flow", "del-var", "a = 1\nborrar a\nimprimir('ok')"),
    ]


def area_functions() -> list[Case]:
    return [
        _c("functions", "def-basic", "funcion f(x):\n    devolver x + 1\nimprimir(f(1))"),
        _c("functions", "def-none", "funcion f():\n    devolver\nx = f()\nimprimir(x)"),
        _c("functions", "def-default", "funcion f(a, b=2):\n    devolver a + b\nimprimir(f(1))\nimprimir(f(1, 5))"),
        _c("functions", "def-kwarg", "funcion f(a, b=2):\n    devolver a + b\nimprimir(f(a=1, b=3))"),
        _c("functions", "def-varargs", "funcion f(*args):\n    devolver suma(args)\nimprimir(f(1, 2, 3))"),
        _c("functions", "def-kwargs", "funcion f(**kw):\n    devolver longitud(kw)\nimprimir(f(a=1, b=2))"),
        _c("functions", "def-mixed", "funcion f(a, *args, **kw):\n    devolver a + suma(args) + longitud(kw)\nimprimir(f(1, 2, 3, x=4))"),
        _c("functions", "def-recursion", "funcion fac(n):\n    si n <= 1:\n        devolver 1\n    devolver n * fac(n - 1)\nimprimir(fac(5))"),
        _c("functions", "def-closure", "funcion externo(x):\n    funcion interno():\n        devolver x * 2\n    devolver interno()\nimprimir(externo(3))"),
        _c("functions", "def-nested-capture", "funcion a():\n    x = 1\n    funcion b():\n        devolver x + 1\n    devolver b()\nimprimir(a())"),
        _c("functions", "def-lambda", "f = lambda x: x + 1\nimprimir(f(1))"),
        _c("functions", "def-lambda-2", "f = lambda x, y=2: x * y\nimprimir(f(3))"),
        _c("functions", "global", "x = 1\nfuncion f():\n    global x\n    x = 5\nf()\nimprimir(x)"),
        _c("functions", "nonlocal", "funcion ext():\n    y = 1\n    funcion int():\n        no_local y\n        y = 7\n    int()\n    devolver y\nimprimir(ext())"),
        _c("functions", "func-as-value", "funcion f(x):\n    devolver x\nimprimir(f(3))\ng = f\nimprimir(g(4))"),
        _c("functions", "call-in-expr", "funcion f():\n    devolver 2\nimprimir(f() * f() + 1)"),
        _c("functions", "too-many-args", "funcion f(a):\n    devolver a\nf(1, 2)"),
        _c("functions", "too-few-args", "funcion f(a, b):\n    devolver a\nf(1)"),
        _c("functions", "undefined-name", "imprimir(no_existe)"),
        _c("functions", "call-nonfunc", "x = 5\nx()"),
        _c("functions", "docstring-ignored", "funcion f():\n    'doc'\n    devolver 1\nimprimir(f())"),
    ]


def area_exceptions() -> list[Case]:
    return [
        _c("exceptions", "raise-basic", "lanzar ValueError('x')"),
        _c("exceptions", "raise-bare-class", "lanzar ValueError"),
        _c("exceptions", "try-except", "intentar:\n    lanzar ValueError('x')\nexcepto ValueError como e:\n    imprimir('caught')"),
        _c("exceptions", "try-msg", "intentar:\n    lanzar ValueError('boom')\nexcepto ValueError como e:\n    imprimir(e)"),
        _c("exceptions", "try-finally", "intentar:\n    imprimir('a')\nfinalmente:\n    imprimir('b')"),
        _c("exceptions", "try-else", "intentar:\n    imprimir('a')\nexcepto ValueError:\n    imprimir('b')\nsino:\n    imprimir('c')\nfinalmente:\n    imprimir('d')"),
        _c("exceptions", "try-two-excepts", "intentar:\n    lanzar KeyError('k')\nexcepto ValueError:\n    imprimir('v')\nexcepto KeyError:\n    imprimir('k')"),
        _c("exceptions", "try-nested", "intentar:\n    intentar:\n        lanzar ValueError('in')\n    excepto ValueError:\n        imprimir('inner')\nexcepto Exception:\n    imprimir('outer')"),
        _c("exceptions", "try-else-raises", "intentar:\n    lanzar ValueError('x')\nexcepto ValueError:\n    imprimir('c')\nsino:\n    imprimir('no')"),
        _c("exceptions", "reraise", "intentar:\n    intentar:\n        lanzar ValueError('x')\n    excepto ValueError:\n        lanzar\nexcepto ValueError:\n    imprimir('outer caught')"),
        _c("exceptions", "custom-exc", "clase E(Exception):\n    pasar\nintentar:\n    lanzar E('x')\nexcepto E:\n    imprimir('custom')"),
        _c("exceptions", "custom-exc-msg", "clase E(Exception):\n    pasar\nintentar:\n    lanzar E('detalle')\nexcepto E como e:\n    imprimir(e)"),
        _c("exceptions", "custom-exc-subclass", "clase A(Exception):\n    pasar\nclase B(A):\n    pasar\nintentar:\n    lanzar B('x')\nexcepto A:\n    imprimir('A')"),
        _c("exceptions", "assert", "afirmar 1 == 1\nimprimir('ok')"),
        _c("exceptions", "assert-msg", "afirmar 1 == 2, 'no son iguales'"),
        _c("exceptions", "assert-caught", "intentar:\n    afirmar Falso, 'razon'\nexcepto AssertionError como e:\n    imprimir(e)"),
        _c("exceptions", "div-zero", "imprimir(1 / 0)"),
        _c("exceptions", "index-error", "imprimir([1][5])"),
        _c("exceptions", "key-error", "d = {}\nimprimir(d['k'])"),
        _c("exceptions", "finally-reraise", "intentar:\n    lanzar ValueError('x')\nfinalmente:\n    imprimir('cleanup')"),
        _c("exceptions", "exc-in-func", "funcion f():\n    lanzar ValueError('in func')\nintentar:\n    f()\nexcepto ValueError:\n    imprimir('ok')"),
    ]


def area_classes() -> list[Case]:
    return [
        _c("classes", "init", "clase P:\n    funcion __init__(self, x):\n        self.x = x\n    funcion ver(self):\n        devolver self.x\nimprimir(P(3).ver())"),
        _c("classes", "attr", "clase P:\n    funcion __init__(self):\n        self.x = 1\np = P()\nimprimir(p.x)"),
        _c("classes", "method-call", "clase P:\n    funcion f(self, a):\n        devolver a * 2\nimprimir(P().f(3))"),
        _c("classes", "str-dunder", "clase P:\n    funcion __str__(self):\n        devolver 'soy P'\nimprimir(P())"),
        _c("classes", "repr-dunder", "clase P:\n    funcion __repr__(self):\n        devolver '<P>'\nimprimir(P())"),
        _c("classes", "eq-dunder", "clase P:\n    funcion __init__(self, x):\n        self.x = x\n    funcion __eq__(self, o):\n        devolver self.x == o.x\nimprimir(P(1) == P(1))"),
        _c("classes", "len-dunder", "clase P:\n    funcion __len__(self):\n        devolver 3\nimprimir(longitud(P()))"),
        _c("classes", "iter-dunder", "clase P:\n    funcion __iter__(self):\n    devolver iter([1, 2])\npara x en P():\n    imprimir(x)"),
        _c("classes", "inheritance", "clase A:\n    funcion f(self):\n        devolver 'A'\nclase B(A):\n    pasar\nimprimir(B().f())"),
        _c("classes", "override", "clase A:\n    funcion f(self):\n        devolver 'A'\nclase B(A):\n    funcion f(self):\n        devolver 'B'\nimprimir(B().f())"),
        _c("classes", "super", "clase A:\n    funcion f(self):\n        devolver 'A'\nclase B(A):\n    funcion f(self):\n        devolver 'B' + super().f()\nimprimir(B().f())"),
        _c("classes", "super-init", "clase A:\n    funcion __init__(self, x):\n        self.x = x\nclase B(A):\n    funcion __init__(self):\n        super().__init__(7)\nimprimir(B().x)"),
        _c("classes", "class-attr", "clase P:\n    v = 5\nimprimir(P.v)"),
        _c("classes", "instance-shared", "clase P:\n    v = 5\nimprimir(P().v)"),
        _c("classes", "multi-base", "clase A:\n    funcion f(self):\n        devolver 'A'\nclase B:\n    funcion f(self):\n        devolver 'B'\nclase C(A, B):\n    pasar\nimprimir(C().f())"),
        _c("classes", "type-of", "clase P:\n    pasar\nimprimir(tipo(P()))"),
        _c("classes", "method-as-value", "clase P:\n    funcion f(self):\n        devolver 1\nimprimir(P.f(P()))"),
        _c("classes", "attr-missing", "clase P:\n    pasar\nimprimir(P().zzz)"),
        _c("classes", "constructor-args", "clase P:\n    funcion __init__(self, a, b=2):\n        self.s = a + b\nimprimir(P(1).s)"),
        _c("classes", "dunder-add", "clase P:\n    funcion __add__(self, o):\n        devolver 99\nimprimir(P() + 1)"),
        _c("classes", "bool-dunder", "clase P:\n    funcion __bool__(self):\n        devolver Falso\nimprimir(P() o 'fallback')"),
    ]


def area_generators() -> list[Case]:
    return [
        _c("generators", "yield-simple", "funcion f():\n    producir 1\n    producir 2\npara x en f():\n    imprimir(x)"),
        _c("generators", "yield-list", "funcion f():\n    para x en [1, 2]:\n        producir x\nimprimir(lista(f()))"),
        _c("generators", "yield-infinite-take", "funcion f():\n    i = 0\n    mientras i < 3:\n        producir i\n        i = i + 1\nimprimir(lista(f()))"),
        _c("generators", "yield-varargs", "funcion f(*args):\n    para a en args:\n        producir a * 2\nimprimir(lista(f(1, 2)))"),
        _c("generators", "yield-sum", "funcion f():\n    producir 1\n    producir 2\nimprimir(sum(f()))"),
        _c("generators", "gen-in-comprehension", "imprimir(sum(x para x en rango(4)))"),
        _c("generators", "class-generator", "clase G:\n    funcion __iter__(self):\n        devolver iter([1, 2])\nimprimir(lista(G()))"),
        _c("generators", "class-iter-self", "clase G:\n    funcion __iter__(self):\n    devolver self\n    funcion __next__(self):\n        devolver 1\nimprimir(lista(G()))"),
    ]


def area_generics_extra() -> list[Case]:
    """Construcciones que se espera que fallen cerradas: useful como control."""
    return [
        _c("controls_closed", "walrus", "si (n := 5) > 1:\n    imprimir(n)"),
        _c("controls_closed", "match", "x = 1\nsegun x:\n    caso 1:\n        imprimir('uno')"),
        _c("controls_closed", "map", "imprimir(lista(map(lambda x: x * 2, [1, 2])))"),
        _c("controls_closed", "filter", "imprimir(lista(filter(lambda x: x, [0, 1])))"),
        _c("controls_closed", "zip", "imprimir(lista(zip([1, 2], [3, 4])))"),
        _c("controls_closed", "divmod", "imprimir(divmod(7, 2))"),
        _c("controls_closed", "reversed", "imprimir(lista(reversed([1, 2])))"),
        _c("controls_closed", "import", "importar os\nimprimir(os.name)"),
        _c("controls_closed", "with", "con abrir('x') como f:\n    imprimir(f)"),
        _c("controls_closed", "subscript-assign", "a = [0, 0]\na[0] = 1\nimprimir(a)"),
        _c("controls_closed", "str-partition", "imprimir('a-b'.partition('-'))"),
        _c("controls_closed", "str-rsplit", "imprimir('a b'.rsplit())"),
        _c("controls_closed", "str-splitlines", "imprimir('a\\nb'.splitlines())"),
        _c("controls_closed", "str-expandtabs", "imprimir('a\\tb'.expandtabs())"),
        _c("controls_closed", "list-sort-inplace", "a = [2, 1]\na.sort()\nimprimir(a)"),
        _c("controls_closed", "type-pattern-match", "funcion f(x):\n    segun x:\n        caso int:\n            devolver 1\nimprimir(f(1))"),
    ]


def area_associativity() -> list[Case]:
    """Cadenas de 3+ operandos del mismo operador.

    La matriz de 2 operandos de `arithmetic` NO puede detectar un error de
    asociatividad (`+` y `%` además son asociativos en los operandos de
    prueba, así que un parser right-assoc pasaba verde). Estas cadenas si.
    """
    out = []
    chains = [
        ("sub", "-", "10 - 3 - 2"),
        ("add", "+", "10 + 3 + 2"),
        ("mul", "*", "2 * 3 * 4"),
        ("div", "/", "100 / 5 / 2"),
        ("floordiv", "//", "100 // 5 // 2"),
        ("mod", "%", "20 % 7 % 3"),
        ("shl", "<<", "1 << 2 << 3"),
        ("shr", ">>", "64 >> 2 >> 1"),
        ("pow", "**", "2 ** 3 ** 2"),
        ("pow3", "**", "2 ** 3 ** 2 ** 2"),
        ("band", "&", "7 & 3 & 1"),
        ("bor", "|", "6 | 2 | 1"),
        ("bxor", "^", "7 ^ 3 ^ 1"),
        ("len5", "-", "20 - 5 - 3 - 2 - 1"),
        ("mixed", "*", "2 * 3 + 4 - 5 // 2"),
    ]
    for name, _op, expr in chains:
        out.append(_c("associativity", name, f"imprimir({expr})"))
    # UNARY_POW_PREC_V1: `**` liga mas fuerte que el signo y que `no`.
    for name, expr in (
        ("neg", "-2 ** 2"),
        ("negvar", "x = 2\nimprimir(-x ** 2)"),
        ("negchain", "-2 ** 3 ** 2"),
        ("parens", "-(2 ** 2)"),
        ("negfloat", "-1.5 ** 0.0"),
        ("negsub", "-2 + 3"),
        ("negtimes", "-2 * 3"),
        ("pownegexp", "2 ** -1"),
        ("dblneg", "- -2"),
        ("subinpow", "3 - 2 ** 2"),
    ):
        out.append(_c("associativity", name, expr))
    return out


def area_bigint() -> list[Case]:
    """BI_CMP_MAG_FIX_V1 / BI_CMP_SIGN_FIX_V1 / BIGINT_CMP_MIXED_V1.

    El corpus no cubria bigints, y ahi estaban los bugs mas graves: el ancho en
    bits se calculaba pasando un LIMB donde se esperaba un puntero, y el signo
    de la comparacion se negaba para los positivos. Ordenar dos bigintsposite
    daba siempre lo contrario.
    """
    out = []
    pairs = [("20", "30"), ("30", "20"), ("40", "20"), ("20", "20"), ("100", "99"), ("99", "100")]
    for a, b in pairs:
        out.append(_c("bigint", f"gt-{a}-{b}", f"x = 10 ** {a}\ny = 10 ** {b}\nimprimir(x > y)"))
        out.append(_c("bigint", f"lt-{a}-{b}", f"x = 10 ** {a}\ny = 10 ** {b}\nimprimir(x < y)"))
        out.append(_c("bigint", f"eq-{a}-{b}", f"x = 10 ** {a}\ny = 10 ** {b}\nimprimir(x == y)"))
    for op in ("+", "-", "*"):
        out.append(_c("bigint", f"arith{op}", f"x = 10 ** 20\ny = 10 ** 30\nimprimir(x {op} y)"))
    out += [
        _c("bigint", "print", "x = 10 ** 40\nimprimir(x)"),
        _c("bigint", "neg-print", "x = -(10 ** 20)\nimprimir(x)"),
        _c("bigint", "neg-cmp", "x = -(10 ** 20)\ny = 10 ** 20\nimprimir(x < y)"),
        _c("bigint", "neg-neg-cmp", "x = -(10 ** 40)\ny = -(10 ** 20)\nimprimir(x < y)"),
        _c("bigint", "vs-int-gt", "imprimir(10 ** 20 > 5)"),
        _c("bigint", "vs-int-lt", "x = 10 ** 20\nimprimir(x < 5)"),
        _c("bigint", "int-vs-big", "x = 10 ** 20\nimprimir(5 > x)"),
        _c("bigint", "vs-int-eq", "x = 10 ** 20\nimprimir(x == 5)"),
        _c("bigint", "sum-small", "imprimir(sum([10 ** 20, 1]))"),
        _c("bigint", "pow-chain", "imprimir(2 ** 100)"),
        _c("bigint", "big-plus-small", "x = 10 ** 20\nimprimir(x + 1)"),
        _c("bigint", "compare-chain", "x = 10 ** 20\nimprimir(1 < x)"),
    ]
    return out


def area_ranges() -> list[Case]:
    """RANGE_VALUE_V1: `rango(...)` as a lazy value.

    Iteration of a bare call already worked (the `for` header consumes the
    arguments), but the VALUE had no object: repr, len, indexing, membership,
    equality, truthiness, sum, conversions and sorting all failed closed.
    """
    out = []
    for label, call in (
        ("n3", "rango(3)"), ("n0", "rango(0)"), ("s1e4", "rango(1, 4)"),
        ("step2", "rango(0, 6, 2)"), ("neg", "rango(5, 0, -1)"),
        ("empty", "rango(1, 1)"), ("empty0", "rango(0, 0)"),
    ):
        out.append(_c("ranges", f"repr-{label}", f"imprimir({call})"))
        out.append(_c("ranges", f"len-{label}", f"imprimir(longitud({call}))"))
    out += [
        _c("ranges", "iter-var", "r = rango(4)\npara x en r:\n    imprimir(x)"),
        _c("ranges", "iter-neg", "r = rango(5, 0, -2)\npara x en r:\n    imprimir(x)"),
        _c("ranges", "iter-twice", "r = rango(2)\npara x en r:\n    imprimir(x)\npara y en r:\n    imprimir(y)"),
        _c("ranges", "index", "r = rango(10, 20)\nimprimir(r[0])\nimprimir(r[3])\nimprimir(r[-1])"),
        _c("ranges", "index-oob", "r = rango(3)\nimprimir(r[9])"),
        _c("ranges", "in-hit", "r = rango(0, 10, 2)\nimprimir(4 en r)"),
        _c("ranges", "in-miss", "r = rango(0, 10, 2)\nimprimir(5 en r)"),
        _c("ranges", "in-str", "r = rango(3)\nimprimir('a' en r)"),
        _c("ranges", "not-in", "r = rango(3)\nimprimir(9 no en r)"),
        _c("ranges", "truthy", "r = rango(3)\nsi r:\n    imprimir('T')\nsino:\n    imprimir('F')"),
        _c("ranges", "falsy", "r = rango(0)\nsi r:\n    imprimir('T')\nsino:\n    imprimir('F')"),
        _c("ranges", "no-range", "r = rango(0)\nimprimir(no r)"),
        _c("ranges", "eq", "imprimir(rango(0, 3) == rango(0, 3))"),
        _c("ranges", "eq-empty", "imprimir(rango(0, 0) == rango(5, 5))"),
        _c("ranges", "ne", "imprimir(rango(0, 3) != rango(0, 4))"),
        _c("ranges", "eq-list", "imprimir(rango(0, 2) == [0, 1])"),
        _c("ranges", "ne-list", "imprimir(rango(0, 2) != [0, 1])"),
        _c("ranges", "lt-closed", "imprimir(rango(0, 3) < rango(0, 4))"),
        _c("ranges", "sum", "imprimir(sum(rango(4)))"),
        _c("ranges", "sum-neg", "imprimir(sum(rango(5, 0, -1)))"),
        _c("ranges", "sum-empty", "imprimir(sum(rango(0)))"),
        _c("ranges", "lista", "imprimir(lista(rango(3)))"),
        _c("ranges", "conjunto", "imprimir(conjunto(rango(3)))"),
        _c("ranges", "ordenar", "imprimir(ordenar(rango(4, 0, -1)))"),
        _c("ranges", "ordenar-asc", "imprimir(ordenar(rango(3)))"),
        _c("ranges", "tipo", "imprimir(tipo(rango(3)))"),
        _c("ranges", "step0", "imprimir(rango(1, 4, 0))"),
        _c("ranges", "step0-caught", "intentar:\n    x = rango(1, 2, 0)\nexcepto ValueError:\n    imprimir('caught')"),
        _c("ranges", "reuse", "r = rango(2)\nimprimir(r)\nimprimir(longitud(r))"),
        _c("ranges", "nested-print", "imprimir([rango(2), rango(1, 3)])"),
    ]
    return out


def area_branch_truthiness() -> list[Case]:
    """TRUTHY_BRANCH_V1: la condicion de `si`/`mientras` es un test de verdad
    CPython, no un test crudo de puntero. Los contenedores vacios son falsy."""
    out = []
    empties = [("emptystr", '""'), ("emptylist", "[]"), ("emptytuple", "()"), ("emptydict", "{}")]
    for name, value in empties:
        out.append(_c("branch_truthiness", f"si-{name}", f"v = {value}\nsi v:\n    imprimir('T')\nsino:\n    imprimir('F')"))
        out.append(_c("branch_truthiness", f"while-{name}", f"v = {value}\nmientras v:\n    imprimir('nope')\nimprimir('fin')"))
    for name, value in (("int0", "0"), ("int1", "1"), ("float0", "0.0"), ("floatnz", "0.5"), ("none", "Nada"), ("str", "'a'"), ("list", "[1]"), ("listnest", "[1, []]")):
        out.append(_c("branch_truthiness", f"si-{name}", f"v = {value}\nsi v:\n    imprimir('T')\nsino:\n    imprimir('F')"))
    out.append(_c("branch_truthiness", "literal-empty-str", 'si "":\n    imprimir("T")\nsino:\n    imprimir("F")'))
    out.append(_c("branch_truthiness", "ternary-empty", "v = []\nimprimir('T' si v sino 'F')"))
    out.append(_c("branch_truthiness", "object-truthy", "clase P:\n    pasar\np = P()\nsi p:\n    imprimir('T')\nsino:\n    imprimir('F')"))
    return out


AREAS = {
    "literals": area_literals,
    "arithmetic": area_arithmetic,
    "associativity": area_associativity,
    "branch_truthiness": area_branch_truthiness,
    "comparisons": area_comparisons,
    "truthiness": area_truthiness,
    "ranges": area_ranges,
    "bigint": area_bigint,
    "builtins": area_builtins,
    "strings": area_strings,
    "formatting": area_formatting,
    "slices": area_slices,
    "collections": area_collections,
    "dicts": area_dicts,
    "sets": area_sets,
    "comprehensions": area_comprehensions,
    "control_flow": area_control_flow,
    "functions": area_functions,
    "exceptions": area_exceptions,
    "classes": area_classes,
    "generators": area_generators,
    "controls_closed": area_generics_extra,
}


def build_corpus(areas: list[str] | None = None) -> list[Case]:
    names = areas if areas else list(AREAS)
    cases: list[Case] = []
    for name in names:
        if name not in AREAS:
            raise SystemExit(f"unknown area: {name} (have: {', '.join(AREAS)})")
        cases.extend(AREAS[name]())
    seen: set[tuple[str, str]] = set()
    unique = []
    for case in cases:
        key = (case.area, case.name)
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


def run_case(case: Case, timeout: float = 10.0) -> dict:
    """Compila, ejecuta y clasifica un caso. Nunca lanza hacia el llamador."""
    from piton.translator import traducir_fuente

    record = {
        "area": case.area,
        "name": case.name,
        "src": case.src,
        "bucket": None,
        "detail": "",
    }
    try:
        translated = traducir_fuente(case.src, "<parity-corpus>")
    except Exception as exc:  # noqa: BLE001 - rechazo honesto
        record["bucket"] = FAIL_CLOSED
        record["detail"] = f"translate: {type(exc).__name__}: {exc}"
        return record
    if platform.system() != "Linux":
        record["bucket"] = FAIL_CLOSED
        record["detail"] = "instrument currently measures the Linux backend only"
        return record
    from piton.linux_x86 import compile_native_linux

    with tempfile.TemporaryDirectory(prefix="piton-corpus-") as directory:
        try:
            executable = compile_native_linux(case.src, Path(directory) / "p.exe")
        except Exception as exc:  # noqa: BLE001
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
    except Exception as exc:  # noqa: BLE001 - un fallo del instrumento no es un gap
        return {
            "area": case.area,
            "name": case.name,
            "src": case.src,
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
        "# Parity corpus report (enumerativo)",
        "",
        f"- backend medido: **{meta['backend']}** (instrumento: `tools/parity_corpus.py`)",
        f"- oracle: CPython {meta['oracle']}",
        f"- casos: **{summary['total']}** | EQUIV **{summary['pct_equiv']}%** | "
        f"comportamiento honesto (EQUIV+RAISE_EQ+FAIL_CLOSED) **{summary['pct_honest']}%** | "
        f"DIVERGENT **{summary['pct_divergent']}%**",
        "",
        "Este reporte NO es un gate. Mide el subconjecto declarado en "
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
            print(f"# {case.area}/{case.name}")
            print(case.src.rstrip("\n"))
        print(f"# total {len(cases)}")
        return 0

    jobs = args.jobs or (os.cpu_count() or 4)
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        records = list(pool.map(_worker, [(case, args.timeout) for case in cases], chunksize=4))

    summary = summarise(records)
    meta = {
        "backend": "linux_x86 (Linux native)",
        "oracle": platform.python_version(),
        "instrument": "tools/parity_corpus.py",
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
    print(f"honesto total: {summary['pct_honest']}% | divergente: {summary['pct_divergent']}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
