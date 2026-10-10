#!/usr/bin/env python3
"""parity_corpus_v2 - corpus enumerativo de segunda generacion PITON vs CPython 3.12.4.

INSTRUMENTO DE MEDICION, no un gate. Mide las 12 areas avanzadas del lenguaje que
el corpus original de 1,233 casos (tools/parity_corpus.py) no cubria: complex,
bytes_bytearray, async_coroutines, advanced_decorators, advanced_exceptions,
import_system, context_managers, yield_from, object_model_advanced,
pattern_matching_full, stdlib_tier1 y call_unpacking.

Buckets canonicos (mismo motor que parity_corpus.py):
  EQUIV               rc identico y stdout byte-identico
  RAISE_EQ            ambos lanzan el mismo tipo de excepcion, rc 1
  FAIL_CLOSED         rechazo honesto en translate/build/parse (no ejecuta)
  DIVERGENT_MISMATCH  nativo corrio y su salida NO coincide con el oracle
  DIVERGENT_CRASH     nativo corrio y discrepo de forma grave (rc no 0/1)
  TIMEOUT             nativo u oracle no termina

Ejecucion:
    python3 tools/parity_corpus_v2.py --areas complex --list
    python3 tools/parity_corpus_v2.py --out docs/parity_corpus_v2_report.json
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


def _c(area: str, name: str, src: str) -> tuple[str, str, str]:
    return (area, name, src if src.endswith("\n") else src + "\n")


def _im(src: str) -> str:
    """Caso que imprime una sola expresion."""
    return f"im({src})"


# --------------------------------------------------------------------------
# generadores por area (12 areas avanzadas no cubiertas por parity_corpus.py)
# --------------------------------------------------------------------------

def _im(src: str) -> str:
    """Caso que imprime una sola expresion."""
    return f"imprimir({src})"


def area_complex():
    out = []
    # literales
    for lit in ("1j", "2.5j", "-1j", "0j", "1e3j", "2+3j", "1-2j", "-1-2j", "0.5+1.5j"):
        out.append(("complex", f"lit-{lit}", f"imprimir({lit})"))
    # binops complejo x complejo
    pairs = [("1+2j", "3-1j"), ("2j", "1j"), ("1+2j", "2+3j"), ("-1-2j", "1+1j"), ("0j", "1j")]
    for i, (a, b) in enumerate(pairs):
        for op in ("+", "-", "*", "/"):
            out.append(("complex", f"cc{op}{i}", f"imprimir({a} {op} {b})"))
    # complejo x int/float
    for i, (a, b) in enumerate([("1+2j", "3"), ("2j", "2"), ("1+2j", "1.5"), ("2.5j", "0.5")]):
        for op in ("+", "-", "*", "/"):
            out.append(("complex", f"cx{op}{i}", f"imprimir({a} {op} {b})"))
    # unarios
    for expr in ("-(1+2j)", "+(1+2j)", "no (1+2j)", "no 0j"):
        out.append(("complex", f"un-{expr}", f"imprimir({expr})"))
    # comparaciones
    for i, (a, b) in enumerate([("1+2j", "1+2j"), ("1+2j", "1+3j"), ("1+0j", "1"), ("1j", "2j")]):
        for op in ("==", "!="):
            out.append(("complex", f"cmp{op}{i}", f"imprimir({a} {op} {b})"))
    # abs, .real, .imag, complejo()
    for expr in ("abs(3+4j)", "(2+3j).real", "(2+3j).imag", "complejo(2,3)", "complejo(1.5,2.5)", "complejo()"):
        out.append(("complex", f"misc-{expr}", f"imprimir({expr})"))
    # f-strings, %s, %r, .format()
    for expr in ("f'{1+2j}'", "'%s' % (1+2j)", "'%r' % (1+2j)", "'v={}'.format(1+2j)"):
        out.append(("complex", f"fmt-{expr}", f"imprimir({expr})"))
    # TypeError al ordenar
    out.append(("complex", "ord-err", "imprimir(1j < 2j)"))
    return out


def area_bytes_bytearray():
    out = []
    for lit in ("b'abc'", 'b""', "b'a\\x00b'", "bytearray(b'xyz')", "bytearray(b'')"):
        out.append(("bytes_bytearray", f"lit-{lit}", f"imprimir({lit})"))
    for expr in ("bytes(3)", "bytes([65,66,67])", "bytes(range(4))", "bytearray(2)", "bytearray([1,2,3])", "bytearray(b'abc')"):
        out.append(("bytes_bytearray", f"ctor-{expr}", f"imprimir({expr})"))
    for expr in ("b'abcdef'[1]", "b'abcdef'[-1]", "b'abc'[0]", "bytearray(b'abc')[1]"):
        out.append(("bytes_bytearray", f"idx-{expr}", f"imprimir({expr})"))
    for expr in ("b'abcdef'[1:4]", "b'abcdef'[::2]", "b'abcdef'[::-1]", "b'abcdef'[1:5:2]", "b'abcdef'[:3]", "b'abcdef'[-2:]", "bytearray(b'abcdef')[1:4]"):
        out.append(("bytes_bytearray", f"slice-{expr}", f"imprimir({expr})"))
    for expr in ("b'ab' + b'cd'", "bytearray(b'ab') + bytearray(b'cd')", "b'ab' + bytearray(b'cd')"):
        out.append(("bytes_bytearray", f"concat-{expr}", f"imprimir({expr})"))
    for expr in ("b'ab' * 3", "bytearray(b'ab') * 2", "b'ab' * 0", "b'ab' * -1", "3 * b'ab'"):
        out.append(("bytes_bytearray", f"repeat-{expr}", f"imprimir({expr})"))
    for expr in ("len(b'abc')", "len(bytearray(b'xy'))", "len(b'')"):
        out.append(("bytes_bytearray", f"len-{expr}", f"imprimir({expr})"))
    for expr in ("bool(b'')", "bool(b'a')", "bool(bytearray(b''))"):
        out.append(("bytes_bytearray", f"truth-{expr}", f"imprimir({expr})"))
    for expr in ("b'ab' == b'ab'", "b'ab' == b'ac'", "b'ab' == bytearray(b'ab')", "b'ab' != b'ac'"):
        out.append(("bytes_bytearray", f"eq-{expr}", f"imprimir({expr})"))
    for expr in ("b'bc' in b'abcd'", "98 in b'abcd'", "120 in b'abcd'", "b'zz' not in b'abcd'"):
        out.append(("bytes_bytearray", f"in-{expr}", f"imprimir({expr})"))
    out.append(("bytes_bytearray", "iter", "total = 0\npara x en b'abcdef':\n    total = total + x\nimprimir(total)"))
    for method, args in [("count", "b'hello'"), ("find", "b'world'"), ("rfind", "b'hello'"),
        ("index", "b'o'"), ("rindex", "b'o'"), ("startswith", "b'hello'"),
        ("endswith", "b'hello'"), ("endswith", "b'x'"),
        ("decode", ""), ("decode", "'utf-8'"), ("hex", ""), ("fromhex", "'616263'")]:
        if args:
            out.append(("bytes_bytearray", f"method-{method}", f"imprimir(b'hello world hello'.{method}(b'{args}'))"))
        else:
            out.append(("bytes_bytearray", f"method-{method}", f"imprimir(b'abc'.{method}())"))
    out.append(("bytes_bytearray", "mut-append", "ba = bytearray(b'abc')\nba.append(100)\nimprimir(ba)"))
    out.append(("bytes_bytearray", "mut-extend", "ba = bytearray(b'abc')\nba.extend(b'ef')\nimprimir(ba)"))
    out.append(("bytes_bytearray", "mut-index", "ba = bytearray(b'abc')\nba[0] = 65\nimprimir(ba)"))
    out.append(("bytes_bytearray", "mut-slice", "ba = bytearray(b'abcdef')\nba[1:3] = b'XY'\nimprimir(ba)"))
    out.append(("bytes_bytearray", "mut-negidx", "ba = bytearray(b'abc')\nba[-1] = 90\nimprimir(ba)"))
    for expr in ("b'abc' < b'abd'", "b'abc' <= b'abc'", "b'abc' > b'aaa'"):
        out.append(("bytes_bytearray", f"cmp-{expr}", f"imprimir({expr})"))
    methods = ["count", "find", "rfind", "index", "rindex", "startswith", "endswith"]
    for m in methods:
        out.append(("bytes_bytearray", f"method-{m}-args", f"imprimir(b'hello world hello'.{m}(b'hello'))"))
    out.append(("bytes_bytearray", "method-find-miss", "imprimir(b'hello world hello'.find(b'zzz'))"))
    return out


def area_call_unpacking():
    out = []
    for xs in ("[1, 2]", "(1, 2)", "{'a': 1}", "[1, 2, 3]"):
        out.append(("call_unpacking", f"star-{xs}", "funcion f(*xs): devolver suma(xs)\nimprimir(f(" + xs + "))"))
    out.append(("call_unpacking", "kwarg-dict", "funcion f(**kw): devolver longitud(kw)\nimprimir(f(**{'a': 1, 'b': 2}))"))
    out.append(("call_unpacking", "mixed-1", "funcion f(a, *b): devolver a + suma(b)\nimprimir(f(1, *[2, 3]))"))
    out.append(("call_unpacking", "mixed-2", "funcion f(a, **b): devolver a + longitud(b)\nimprimir(f(1, **{'b': 2}))"))
    out.append(("call_unpacking", "mixed-3", "funcion f(*a, **b): devolver suma(a) + longitud(b)\nimprimir(f(*[1, 2], **{'c': 3}))"))
    out.append(("call_unpacking", "star-var", "xs = [1, 2, 3]\nfuncion f(*xs): devolver suma(xs)\nimprimir(f(*xs))"))
    out.append(("call_unpacking", "kw-var", "kw = {'a': 1, 'b': 2}\nfuncion f(**kw): devolver longitud(kw)\nimprimir(f(**kw))"))
    out.append(("call_unpacking", "star-empty", "funcion f(*xs): devolver suma(xs)\nimprimir(f(*[]))"))
    out.append(("call_unpacking", "kw-empty", "funcion f(**kw): devolver longitud(kw)\nimprimir(f(**{}))"))
    return out


def area_async_coroutines():
    out = []
    out.append(("async_coroutines", "async-def", "asincrono funcion f():\n    devolver 1\nasincrono funcion g():\n    x = f()\n    imprimir(x)"))
    out.append(("async_coroutines", "await", "asincrono funcion f():\n    devolver 1\nasincrono funcion g():\n    x = f()\n    imprimir(x)"))
    out.append(("async_coroutines", "asyncio-run", "asincrono funcion f():\n    devolver 42\nasincrono funcion main():\n    x = f()\n    imprimir(x)\nasincrono.run(main())"))
    out.append(("async_coroutines", "async-for", "asincrono funcion f():\n    para i en rango(3):\n        producir i\nasincrono funcion g():\n    para x en f():\n        imprimir(x)\nasincrono.run(g())"))
    out.append(("async_coroutines", "async-gen", "asincrono funcion f():\n    producir 1\n    producir 2\nasincrono funcion g():\n    para x en f():\n        imprimir(x)\nasincrono.run(g())"))
    out.append(("async_coroutines", "create-task", "asincrono funcion f():\n    devolver 1\nasincrono funcion g():\n    t = asincrono.create_task(f())\n    x = t\n    imprimir(x)\nasincrono.run(g())"))
    out.append(("async_coroutines", "gather", "asincrono funcion f():\n    devolver 1\nasincrono funcion g():\n    x = asincrono.gather(f(), f())\n    imprimir(x)\nasincrono.run(g())"))
    out.append(("async_coroutines", "cancel", "asincrono funcion f():\n    asincrono.sleep(0)\n    devolver 1\nasincrono funcion g():\n    t = asincrono.create_task(f())\n    t.cancel()\n    imprimir('cancelled')\nasincrono.run(g())"))
    out.append(("async_coroutines", "sleep0", "asincrono funcion f():\n    asincrono.sleep(0)\n    devolver 1\nasincrono.run(f())"))
    out.append(("async_coroutines", "sleep1", "asincrono funcion f():\n    asincrono.sleep(1)\n    devolver 1\nasincrono.run(f())"))
    return out


def area_yield_from():
    out = []
    out.append(("yield_from", "sub", "funcion sub():\n    producir 10\n    producir 20\nfuncion g():\n    producir 1\n    producir desde sub()\n    producir 2\nimprimir(lista(g()))"))
    out.append(("yield_from", "list", "funcion g():\n    producir desde [1, 2, 3]\nimprimir(lista(g()))"))
    out.append(("yield_from", "tuple", "funcion g():\n    producir desde (4, 5)\nimprimir(lista(g()))"))
    out.append(("yield_from", "send", "funcion sub():\n    x = producir 1\n    producir x + 1\nfuncion g():\n    g = sub()\n    x = g.send(None)\n    x = g.send(99)\nimprimir(lista(g()))"))
    return out


def area_context_managers():
    out = []
    out.append(("context_managers", "basic", "clase CM:\n    funcion __enter__(self):\n        imprimir('enter')\n        devolver 10\n    funcion __exit__(self, t, v, tb):\n        imprimir('exit')\n        devolver Falso\ncon CM() como x:\n    imprimir('body', x)"))
    out.append(("context_managers", "multiple", "clase A:\n    funcion __enter__(self):\n        devolver 1\n    funcion __exit__(self, t, v, tb):\n        imprimir('exitA')\n        devolver Falso\nclase B:\n    funcion __enter__(self):\n        devolver 2\n    funcion __exit__(self, t, v, tb):\n        imprimir('exitB')\n        devolver Falso\ncon A() como a, B() como b:\n    imprimir('body', a + b)"))
    out.append(("context_managers", "suppress", "clase CM:\n    funcion __enter__(self):\n        devolver 1\n    funcion __exit__(self, t, v, tb):\n        imprimir('suppressed')\n        devolver Verdadero\ncon CM():\n    lanzar ValueError('err')\nimprimir('fin')"))
    out.append(("context_managers", "reraise", "clase CM:\n    funcion __enter__(self):\n        devolver 1\n    funcion __exit__(self, t, v, tb):\n        imprimir('exit')\n        lanzar ValueError('from exit')\ncon CM():\n    lanzar ValueError('err')\nimprimir('fin')"))
    return out


def area_pattern_matching_full():
    out = []
    out.append(("pattern_matching_full", "literal", "x = 1\nsegun x:\n    caso 1:\n        imprimir('uno')\n    caso _:\n        imprimir('otro')"))
    out.append(("pattern_matching_full", "seq", "x = [1, 2]\nsegun x:\n    caso [a, b]:\n        imprimir(a, b)\n    caso _:\n        imprimir('otro')"))
    out.append(("pattern_matching_full", "mapping", "x = {'a': 1}\nsegun x:\n    caso {'a': v}:\n        imprimir(v)\n    caso _:\n        imprimir('otro')"))
    out.append(("pattern_matching_full", "class", "clase P:\n    funcion __init__(self, x): self.x = x\nx = P(1)\nsegun x:\n    caso P(x):\n        imprimir(x)\n    caso _:\n        imprimir('otro')"))
    out.append(("pattern_matching_full", "guard", "x = 15\nsegun x:\n    caso n si n > 10:\n        imprimir('grande', n)\n    caso _:\n        imprimir('otro')"))
    out.append(("pattern_matching_full", "or", "x = 3\nsegun x:\n    caso 1 | 2 | 3:\n        imprimir('123')\n    caso _:\n        imprimir('otro')"))
    return out


def area_object_model_advanced():
    out = []
    out.append(("object_model_advanced", "multi-inherit", "clase A:\n    funcion f(self): devolver 'A'\nclase B:\n    funcion f(self): devolver 'B'\nclase C(A, B):\n    pasar\nimprimir(C().f())"))
    out.append(("object_model_advanced", "super", "clase A:\n    funcion f(self): devolver 'A'\nclase B(A):\n    funcion f(self): devolver 'B' + super().f()\nimprimir(B().f())"))
    out.append(("object_model_advanced", "self-method", "clase P:\n    funcion f(self): devolver 1\nimprimir(P().f())"))
    out.append(("object_model_advanced", "getattr", "clase P:\n    funcion __getattr__(self, n): devolver n\nimprimir(P().x)"))
    out.append(("object_model_advanced", "setattr", "clase P:\n    funcion __setattr__(self, n, v):\n        imprimir('set', n, v)\n        super().__setattr__(n, v)\np = P()\np.x = 1"))
    out.append(("object_model_advanced", "del", "clase P:\n    funcion __del__(self): imprimir('del')\np = P()\nborrar p"))
    out.append(("object_model_advanced", "mro-3", "clase A:\n    funcion f(self): devolver 'A'\nclase B(A):\n    funcion f(self): devolver 'B'\nclase C(B):\n    funcion f(self): devolver 'C'\nimprimir(C().f())"))
    return out


def area_advanced_decorators():
    out = []
    out.append(("advanced_decorators", "property", "clase P:\n    @property\n    funcion x(self): devolver 1\n    @x.setter\n    funcion x(self, v): imprimir('set', v)\n    @x.deleter\n    funcion x(self): imprimir('del')\np = P()\nimprimir(p.x)\np.x = 2\nimprimir(p.x)\nborrar p.x"))
    out.append(("advanced_decorators", "classmethod", "clase C:\n    v = 10\n    @classmethod\n    funcion get_v(cls): devolver cls.v\nimprimir(C.get_v())\nimprimir(C().get_v())"))
    out.append(("advanced_decorators", "staticmethod", "clase M:\n    @staticmethod\n    funcion add(a, b): devolver a + b\nimprimir(M.add(3, 4))\nimprimir(M().add(5, 6))"))
    out.append(("advanced_decorators", "custom", "funcion add_tag(cls):\n    cls.tag = 'TAG'\n    devolver cls\n@add_tag\nclase T:\n    pasar\nimprimir(T.tag)"))
    out.append(("advanced_decorators", "nested", "funcion d1(f):\n    funcion w(): imprimir('d1'); devolver f()\n    devolver w\nfuncion d2(f):\n    funcion w(): imprimir('d2'); devolver f()\n    devolver w\n@d1\n@d2\nfuncion f(): devolver 1\nimprimir(f())"))
    return out


def area_advanced_exceptions():
    out = []
    out.append(("advanced_exceptions", "from", "funcion f():\n    lanzar ValueError('boom')\nintentar:\n    f()\nexcepto ValueError como e:\n    lanzar RuntimeError('wrapper') desde e"))
    out.append(("advanced_exceptions", "except-as", "intentar:\n    lanzar ValueError('boom')\nexcepto ValueError como e:\n    imprimir(e)"))
    out.append(("advanced_exceptions", "custom-hierarchy", "clase A(Exception): pasar\nclase B(A): pasar\nintentar:\n    lanzar B('x')\nexcepto A:\n    imprimir('A')"))
    out.append(("advanced_exceptions", "reraise", "intentar:\n    intentar:\n        lanzar ValueError('x')\n    excepto ValueError:\n        lanzar\nexcepto ValueError:\n    imprimir('outer caught')"))
    out.append(("advanced_exceptions", "custom", "clase E(Exception):\n    pasar\nintentar:\n    lanzar E('x')\nexcepto E:\n    imprimir('custom')"))
    return out


def area_import_system():
    out = []
    out.append(("import_system", "dotted", "importar pkg\nimprimir(pkg.fn())"))
    out.append(("import_system", "from", "desde pkg importar fn\nimprimir(fn())"))
    out.append(("import_system", "star", "desde pkg importar *\nimprimir(fn())"))
    out.append(("import_system", "relative", "desde . importar x\nimprimir(x)"))
    out.append(("import_system", "relative-mod", "desde .mod importar f\nimprimir(f())"))
    out.append(("import_system", "sys-modules", "importar sys\nimprimir(sys.modules['sys'].__name__)"))
    out.append(("import_system", "metadata", "importar sys\nimprimir(sys.__name__)\nimprimir(sys.__package__)\nimprimir(sys.__file__)"))
    out.append(("import_system", "cyclic", "importar sys\nimprimir('cyclic')"))
    return out


def area_stdlib_tier1():
    out = []
    for fn in ("floor", "ceil", "trunc", "fabs", "gcd", "sqrt"):
        out.append(("stdlib_tier1", f"math-{fn}", "importar math\nimprimir(math." + fn + "(42))"))
    out.append(("stdlib_tier1", "math-pi", "importar math\nimprimir(math.pi)"))
    out.append(("stdlib_tier1", "math-e", "importar math\nimprimir(math.e)"))
    out.append(("stdlib_tier1", "all", "imprimir(all([1, 2]))"))
    out.append(("stdlib_tier1", "all-empty", "imprimir(all([]))"))
    out.append(("stdlib_tier1", "any", "imprimir(any([0, 1]))"))
    out.append(("stdlib_tier1", "any-empty", "imprimir(any([]))"))
    out.append(("stdlib_tier1", "bin", "imprimir(bin(5))"))
    out.append(("stdlib_tier1", "chr", "imprimir(chr(65))"))
    out.append(("stdlib_tier1", "ord", "imprimir(ord('A'))"))
    out.append(("stdlib_tier1", "pow", "imprimir(pow(2, 10))"))
    out.append(("stdlib_tier1", "round", "imprimir(round(2.5))"))
    out.append(("stdlib_tier1", "round-prec", "imprimir(round(2.567, 2))"))
    return out


# --------------------------------------------------------------------------
# registro de areas
# --------------------------------------------------------------------------

AREAS = {
    "complex": area_complex,
    "bytes_bytearray": area_bytes_bytearray,
    "call_unpacking": area_call_unpacking,
    "async_coroutines": area_async_coroutines,
    "yield_from": area_yield_from,
    "context_managers": area_context_managers,
    "pattern_matching_full": area_pattern_matching_full,
    "object_model_advanced": area_object_model_advanced,
    "advanced_decorators": area_advanced_decorators,
    "advanced_exceptions": area_advanced_exceptions,
    "import_system": area_import_system,
    "stdlib_tier1": area_stdlib_tier1,
}


# --------------------------------------------------------------------------
# motor de ejecución (copiado de parity_corpus.py, adaptado)
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
        "# Parity corpus report v2 (enumerativo)",
        "",
        f"- backend medido: **{meta['backend']}** (instrumento: `tools/parity_corpus_v2.py`)",
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
        "instrument": "tools/parity_corpus_v2.py",
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
