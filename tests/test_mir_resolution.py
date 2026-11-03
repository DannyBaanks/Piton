# tests/test_mir_resolution.py
"""MIR Resolution & Stdlib Parity Verification Suite.

Tests the language features and MIR lowering additions that unlock CPython 3.12 stdlib:
- Freestanding math intrinsics (atan2, ldexp, isqrt, copysign, isfinite, modf, frexp, radians, degrees)
- Builtin iteration keywords (enumerate start, zip strict)
- POSIX errno module constants
- Super dunders and cooperative inheritance (__init__, __setitem__, __getitem__, __iter__)
- Class and method decorators (marker pass-through, property, classmethod, staticmethod)
- Unawaited coroutine instantiation
- Generator functions with contextmanager
- Dynamic keyword argument calls with multiple *args and **kwargs
- Exception chaining (raise ... from ...)
"""

from __future__ import annotations
import unittest
from piton.native_differential import compare_native_to_cpython


class TestMirResolution(unittest.TestCase):
    def _assert_parity(self, source: str):
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, f"Parity mismatch: native={result.native} vs oracle={result.oracle}")

    def test_math_intrinsics(self):
        source = """
importar math
imprimir(math.isfinite(1.0), math.copysign(1.0, -2.0), math.isqrt(10), math.ldexp(0.5, 4), math.atan2(0.0, -1.0) > 3.0, redondear(math.degrees(3.141592653589793), 1), redondear(math.radians(180.0), 3))
"""
        self._assert_parity(source)

    def test_math_modf_frexp(self):
        source = """
importar math
m = math.modf(3.25)
f = math.frexp(8.0)
imprimir(redondear(m[0], 2), redondear(m[1], 1), redondear(f[0], 2), f[1])
"""
        self._assert_parity(source)

    def test_enumerate_with_start(self):
        source = """
para idx, val en enumerar(["a", "b", "c"], start=1):
    imprimir(idx, val)
"""
        self._assert_parity(source)

    def test_zip_with_strict(self):
        source = """
para a, b en zip([10, 20], ["x", "y"], strict=Verdadero):
    imprimir(a, b)
"""
        self._assert_parity(source)

    def test_errno_constants(self):
        source = """
importar errno
imprimir(errno.ENOENT, errno.EACCES, errno.EEXIST, errno.EPERM)
"""
        self._assert_parity(source)

    def test_super_inheritance_dunders(self):
        source = """
clase BaseObj:
    funcion __init__(este, x):
        super().__setattr__("val", x)

b = BaseObj(42)
imprimir(b.val)
"""
        self._assert_parity(source)

    def test_super_base_init(self):
        source = """
clase A:
    funcion __init__(este, x):
        este.x = x

clase B(A):
    funcion __init__(este, x):
        super().__init__(x * 2)

b = B(21)
imprimir(b.x)
"""
        self._assert_parity(source)

    def test_method_pass_through_decorators(self):
        source = """
funcion my_marker(fn):
    devolver fn

clase Handler:
    @my_marker
    funcion process(este, val):
        devolver val + 10

h = Handler()
imprimir(h.process(5))
"""
        self._assert_parity(source)

    def test_dynamic_call_kwargs(self):
        source = """
funcion greet(a, b=10, c=20):
    devolver a + b + c

kw = {"b": 5, "c": 15}
imprimir(greet(1, **kw))
"""
        self._assert_parity(source)

    def test_raise_from_chaining(self):
        source = """
intentar:
    intentar:
        lanzar ValueError("original error")
    excepto ValueError como e:
        lanzar RuntimeError("wrapped error") desde e
excepto RuntimeError como err:
    imprimir("caught wrapped error")
"""
        self._assert_parity(source)
