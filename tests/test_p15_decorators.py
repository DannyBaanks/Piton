# tests/test_p15_decorators.py
"""Phase 42: Advanced Decorator Parity vs CPython 3.12.

Tests:
- Parameterized decorators (decorator factories with arguments)
- Stacked / chained decorators (multiple @dec applied in order)
- Decorators on nested functions and closures
- Class decorators (transforming classes, adding attributes, wrapping)
- Method decorators (@staticmethod / @metodo_estatico, @classmethod / @metodo_clase)
- Bound method access on static/class methods
- Differential parity with exact stdout / returncode match against CPython 3.12.
"""

from __future__ import annotations
import unittest
from piton.native_differential import compare_native_to_cpython


class TestAdvancedDecorators(unittest.TestCase):
    def test_parameterized_decorator_repeat(self):
        source = """
funcion repeat(n):
    funcion decorator(fn):
        funcion wrapper(x):
            res = 0
            para i en rango(n):
                res = res + fn(x)
            devolver res
        devolver wrapper
    devolver decorator

@repeat(3)
funcion add_five(x):
    devolver x + 5

imprimir(add_five(10))
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_parameterized_decorator_prefix(self):
        source = """
funcion tag(t):
    funcion dec(fn):
        funcion wrapper(s):
            devolver "<" + t + ">" + fn(s) + "</" + t + ">"
        devolver wrapper
    devolver dec

@tag("b")
funcion greet(name):
    devolver "Hello " + name

imprimir(greet("World"))
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_stacked_decorators_execution_order(self):
        source = """
funcion dec_a(fn):
    funcion wrapper(x):
        devolver "[" + fn(x) + "]"
    devolver wrapper

funcion dec_b(fn):
    funcion wrapper(x):
        devolver "(" + fn(x) + ")"
    devolver wrapper

@dec_a
@dec_b
funcion format_val(x):
    devolver texto(x)

imprimir(format_val(42))
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_stacked_parameterized_and_plain_decorators(self):
        source = """
funcion add_n(n):
    funcion dec(fn):
        funcion wrapper(x):
            devolver fn(x) + n
        devolver wrapper
    devolver dec

funcion double(fn):
    funcion wrapper(x):
        devolver fn(x) * 2
    devolver wrapper

@add_n(10)
@double
funcion compute(x):
    devolver x + 1

imprimir(compute(3))
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_decorator_on_nested_function(self):
        source = """
funcion make_counter_multiplier(factor):
    funcion logged(fn):
        funcion wrapper(val):
            devolver fn(val) * factor
        devolver wrapper

    @logged
    funcion base_step(x):
        devolver x + 1

    devolver base_step

f = make_counter_multiplier(5)
imprimir(f(2))
imprimir(f(10))
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_class_decorator_attribute_injection(self):
        source = """
funcion add_metadata(cls):
    cls.version = 2
    cls.author = "PITON"
    devolver cls

@add_metadata
clase Service:
    name = "Auth"
    funcion get_info(self):
        devolver self.name

s = Service()
imprimir(s.get_info())
imprimir(Service.version)
imprimir(Service.author)
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_class_decorator_wrapper(self):
        source = """
funcion singleton(cls):
    instance = cls()
    funcion get_instance():
        devolver instance
    devolver get_instance

@singleton
clase Config:
    mode = 1
    funcion get_mode(self):
        devolver self.mode

c1 = Config()
c2 = Config()
imprimir(c1.get_mode())
imprimir(c2.get_mode())
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_staticmethod_and_metodo_estatico(self):
        source = """
clase MathLib:
    @staticmethod
    funcion add(a, b):
        devolver a + b

    @metodo_estatico
    funcion mul(a, b):
        devolver a * b

imprimir(MathLib.add(5, 7))
imprimir(MathLib.mul(6, 7))

m = MathLib()
imprimir(m.add(10, 20))
imprimir(m.mul(3, 4))
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_classmethod_and_metodo_clase(self):
        source = """
clase Counter:
    count = 100

    @classmethod
    funcion get_count(cls):
        devolver cls.count

    @metodo_clase
    funcion get_double(cls):
        devolver cls.count * 2

imprimir(Counter.get_count())
imprimir(Counter.get_double())

c = Counter()
imprimir(c.get_count())
imprimir(c.get_double())
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_method_tearoff_and_bound_methods(self):
        source = """
clase Tools:
    factor = 3

    @staticmethod
    funcion add(x, y):
        devolver x + y

    @classmethod
    funcion scale(cls, x):
        devolver x * cls.factor

t = Tools()
add_fn = t.add
scale_fn = t.scale

imprimir(add_fn(10, 5))
imprimir(scale_fn(4))
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)
