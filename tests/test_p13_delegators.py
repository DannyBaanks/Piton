from piton.native_differential import compare_native_to_cpython
import unittest


class TestDelegatorGeneratorsParity(unittest.TestCase):
    def test_yield_from_subgenerator(self):
        source = """
funcion sub():
    producir 1
    producir 2

funcion main():
    producir 0
    producir desde sub()
    producir 3

para x en main():
    imprimir(x)
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_yield_from_list(self):
        source = """
funcion gen_list():
    producir desde [10, 20, 30]

para x en gen_list():
    imprimir(x)
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_yield_from_tuple(self):
        source = """
funcion gen_tuple():
    producir desde (100, 200)

para x en gen_tuple():
    imprimir(x)
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_yield_from_range(self):
        source = """
funcion gen_range():
    producir desde rango(4)

para x en gen_range():
    imprimir(x)
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_yield_from_multiple_sources(self):
        source = """
funcion sub_a():
    producir 1
    producir 2

funcion gen_combo():
    producir desde sub_a()
    producir desde [10, 20]
    producir desde (100, 200)

para item en gen_combo():
    imprimir(item)
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_yield_from_nested_delegation(self):
        source = """
funcion level3():
    producir 30

funcion level2():
    producir 20
    producir desde level3()
    producir 21

funcion level1():
    producir 10
    producir desde level2()
    producir 11

para val en level1():
    imprimir(val)
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_yield_from_in_expression_and_control_flow(self):
        source = """
funcion countdown():
    producir desde [3, 2, 1]
    producir 0

total = 0
para n en countdown():
    total = total + n
imprimir(total)
"""
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)


if __name__ == "__main__":
    unittest.main()
