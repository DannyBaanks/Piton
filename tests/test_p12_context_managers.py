from piton.native_differential import compare_native_to_cpython
import unittest


class TestContextManagersParity(unittest.TestCase):
    def test_basic_with_as(self):
        source = """
clase Manager:
    funcion __init__(self, val):
        self.val = val
    funcion __enter__(self):
        imprimir("enter")
        devolver self.val * 2
    funcion __exit__(self, exc_type, exc_val, exc_tb):
        imprimir("exit")
        devolver Falso

con Manager(21) como x:
    imprimir("inside body")
    imprimir(x)
imprimir("after with")
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_basic_with_no_as(self):
        source = """
clase SimpleManager:
    funcion __enter__(self):
        imprimir("enter no as")
        devolver 100
    funcion __exit__(self, exc_type, exc_val, exc_tb):
        imprimir("exit no as")
        devolver Falso

con SimpleManager():
    imprimir("inside no as")
imprimir("done")
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_with_enter_returns_self(self):
        source = """
clase SelfManager:
    funcion __init__(self, tag):
        self.tag = tag
    funcion __enter__(self):
        imprimir("entering", self.tag)
        devolver self
    funcion __exit__(self, t, v, tb):
        imprimir("exiting", self.tag)
        devolver Falso
    funcion greet(self):
        imprimir("hello from", self.tag)

con SelfManager(42) como m:
    m.greet()
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_with_exception_propagation_and_catch(self):
        source = """
clase PropagatingManager:
    funcion __enter__(self):
        imprimir("enter prop")
        devolver self
    funcion __exit__(self, exc_type, exc_val, exc_tb):
        imprimir("exit prop", exc_val)
        devolver Falso

intentar:
    con PropagatingManager() como p:
        imprimir("before raise")
        lanzar ValueError("bad input")
        imprimir("unreachable")
excepto ValueError:
    imprimir("caught ValueError")
imprimir("continued after try")
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_with_exception_suppression(self):
        source = """
clase SuppressingManager:
    funcion __enter__(self):
        imprimir("enter supp")
        devolver self
    funcion __exit__(self, exc_type, exc_val, exc_tb):
        imprimir("suppressing", exc_val)
        devolver Verdadero

con SuppressingManager() como s:
    imprimir("raising inside suppressed with")
    lanzar ValueError("ignored key")
    imprimir("after raise inside")
imprimir("resumed execution after suppressed with")
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_multiple_context_managers_success(self):
        source = """
clase ManagerA:
    funcion __enter__(self):
        imprimir("enter A")
        devolver "A"
    funcion __exit__(self, t, v, tb):
        imprimir("exit A")
        devolver Falso

clase ManagerB:
    funcion __enter__(self):
        imprimir("enter B")
        devolver "B"
    funcion __exit__(self, t, v, tb):
        imprimir("exit B")
        devolver Falso

con ManagerA() como a, ManagerB() como b:
    imprimir("in multiple body:", a, b)
imprimir("after multiple with")
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_three_context_managers_order(self):
        source = """
clase LogManager:
    funcion __init__(self, tag):
        self.tag = tag
    funcion __enter__(self):
        imprimir("enter", self.tag)
        devolver self.tag
    funcion __exit__(self, t, v, tb):
        imprimir("exit", self.tag)
        devolver Falso

con LogManager(1) como a, LogManager(2) como b, LogManager(3) como c:
    imprimir("body:", a, b, c)
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_multiple_with_inner_suppression(self):
        source = """
clase OuterManager:
    funcion __enter__(self):
        imprimir("outer enter")
        devolver self
    funcion __exit__(self, t, v, tb):
        imprimir("outer exit", t)
        devolver Falso

clase InnerSuppressor:
    funcion __enter__(self):
        imprimir("inner enter")
        devolver self
    funcion __exit__(self, t, v, tb):
        imprimir("inner suppress", v)
        devolver Verdadero

con OuterManager() como out_m, InnerSuppressor() como in_m:
    imprimir("raising in inner")
    lanzar ValueError("inner error")
imprimir("all done")
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_multiple_with_propagation_both_exits(self):
        source = """
clase NonSuppressor:
    funcion __init__(self, tag):
        self.tag = tag
    funcion __enter__(self):
        imprimir("enter", self.tag)
        devolver self
    funcion __exit__(self, t, v, tb):
        imprimir("exit", self.tag, v)
        devolver Falso

intentar:
    con NonSuppressor(1) como m1, NonSuppressor(2) como m2:
        imprimir("body error")
        lanzar TypeError("type boom")
excepto TypeError:
    imprimir("caught in outer except")
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_with_in_loop(self):
        source = """
clase CounterCM:
    funcion __init__(self, n):
        self.n = n
    funcion __enter__(self):
        imprimir("enter loop", self.n)
        devolver self.n * 10
    funcion __exit__(self, t, v, tb):
        imprimir("exit loop", self.n)
        devolver Falso

para i en [1, 2, 3]:
    con CounterCM(i) como val:
        imprimir("body val:", val)
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_async_with_basic(self):
        source = """
importar asyncio

clase AsyncCM:
    funcion __init__(self, code):
        self.code = code
    asincrono funcion __aenter__(self):
        imprimir("aenter", self.code)
        devolver self.code
    asincrono funcion __aexit__(self, t, v, tb):
        imprimir("aexit", self.code)
        devolver Falso

asincrono funcion main():
    asincrono con AsyncCM(77) como val:
        imprimir("inside async body:", val)
    devolver 99

imprimir(asyncio.run(main()))
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_async_with_multiple(self):
        source = """
importar asyncio

clase AsyncLogger:
    funcion __init__(self, tag):
        self.tag = tag
    asincrono funcion __aenter__(self):
        imprimir("aenter", self.tag)
        devolver self.tag
    asincrono funcion __aexit__(self, t, v, tb):
        imprimir("aexit", self.tag)
        devolver Falso

asincrono funcion run_both():
    asincrono con AsyncLogger(10) como x, AsyncLogger(20) como y:
        imprimir("in async multiple:", x, y)
    devolver 1

imprimir(asyncio.run(run_both()))
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_nested_with_custom_context_managers(self):
        source = """
clase Resource:
    funcion __init__(self, res_id):
        self.res_id = res_id
    funcion __enter__(self):
        imprimir("allocating", self.res_id)
        devolver self.res_id
    funcion __exit__(self, t, v, tb):
        imprimir("freeing", self.res_id)
        devolver Falso

con Resource(1) como r1:
    imprimir("acquired", r1)
    con Resource(2) como r2:
        imprimir("acquired", r2)
        imprimir("working with", r1, r2)
    imprimir("back in outer with", r1)
imprimir("all freed")
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)


if __name__ == "__main__":
    unittest.main()
