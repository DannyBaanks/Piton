from __future__ import annotations

import unittest

from piton.native_differential import compare_native_to_cpython
from piton.x86 import NativeBuildError


def _assert_matches(testcase: unittest.TestCase, source: str) -> None:
    diff = compare_native_to_cpython(source)
    testcase.assertEqual(
        diff.native.returncode,
        diff.oracle.returncode,
        f"returncode mismatch\nnative stderr: {diff.native.stderr!r}\noracle stderr: {diff.oracle.stderr!r}",
    )
    testcase.assertEqual(
        diff.native.stdout,
        diff.oracle.stdout,
        f"stdout mismatch\nnative stderr: {diff.native.stderr!r}\noracle stderr: {diff.oracle.stderr!r}",
    )


def _assert_build_fails(testcase: unittest.TestCase, source: str) -> None:
    with testcase.assertRaises(NativeBuildError):
        compare_native_to_cpython(source)


class TestComplex(unittest.TestCase):
    """COMPLEX_V1 — números complejos en el backend Linux x86-64.

    Regla del gate: byte-idéntico contra el oráculo CPython 3.12.4, incluyendo
    el repr (que tiene sus propias reglas de signo y de ".0") y la división
    compleja (que CPython resuelve con el algoritmo de Smith, no con la fórmula
    naive). Lo no soportado falla cerrado en build, nunca semántica aproximada.
    """

    def test_literals_and_repr(self):
        _assert_matches(self, """
imprimir(1j)
imprimir(2.5j)
imprimir(1e3j)
imprimir(-1j)
imprimir(0j)
imprimir(2+3j)
imprimir(1-2j)
imprimir(-1-2j)
imprimir(complejo(2, 3))
imprimir(complejo(1.5, 2.5))
""")

    def test_repr_signed_zero_rules(self):
        # CPython distingue +0.0 de -0.0 y usa el bit de signo de la parte
        # imaginaria para el separador.
        _assert_matches(self, """
imprimir(complejo(2, 0))
imprimir(complejo(0, 0))
imprimir(complejo(0.0, -0.0))
imprimir(complejo(-0.0, 0.0))
imprimir(complejo(-0.0, -0.0))
imprimir(complejo(2, -0.0))
imprimir(complejo(0.0, -1.0))
""")

    def test_repr_exponent_forms(self):
        _assert_matches(self, """
imprimir(complejo(1e20, -1e20))
imprimir(complejo(1e-20, -1e-20))
imprimir(complejo(1.5e300, 2e300))
imprimir(1e16j)
""")

    def test_arithmetic(self):
        _assert_matches(self, """
imprimir((1+2j)+(3-1j))
imprimir((1+2j)-(3-1j))
imprimir((1+2j)*(3-1j))
imprimir(1j*1j)
imprimir(1j*1j*1j)
imprimir(2j+1)
imprimir(1.5+2j)
imprimir((1+2j)*1.5)
imprimir((0+1j)+(0-1j))
imprimir(2j-2j)
imprimir(complejo(2,3)+complejo(-1,-1))
""")

    def test_division_bit_exact(self):
        # La naive (ac+bd)/(c²+d²) difiere de CPython en el último bit; el
        # gate exige el algoritmo de Smith byte-idéntico.
        _assert_matches(self, """
imprimir(1j/2)
imprimir((1+2j)/2)
imprimir((1+2j)/(3-1j))
imprimir((1+2j)/(-3-4j))
imprimir((7+7j)/(1-1j))
""")

    def test_comparisons(self):
        _assert_matches(self, """
imprimir((1+2j)==(1+2j))
imprimir((1+2j)!=(1+3j))
imprimir((1+0j)==1)
imprimir((1+2j)==complejo(1.0,2.0))
imprimir(1j==2j)
imprimir(1j!=2j)
""")

    def test_ordering_is_type_error(self):
        # CPython: TypeError en build... no: en ejecución, exit 1. Ambos lados
        # deben coincidir en returncode (1) y stdout (vacío).
        _assert_matches(self, "imprimir(1j < 2j)")
        _assert_matches(self, "imprimir(1j <= 2j)")
        _assert_matches(self, "imprimir(1j > 2j)")
        _assert_matches(self, "imprimir(1j >= 2j)")

    def test_truthiness(self):
        _assert_matches(self, """
imprimir(bool(0j))
imprimir(bool(1+0j))
imprimir(bool(0+0j))
imprimir(no 1+2j)
si 0j:
    imprimir('t')
sino:
    imprimir('f')
si 1j:
    imprimir('t')
sino:
    imprimir('f')
""")

    def test_abs(self):
        _assert_matches(self, """
imprimir(abs(3+4j))
imprimir(abs(1j))
imprimir(abs(0j))
imprimir(abs(3+4j) == 5.0)
imprimir(abs(-3-4j))
""")

    def test_real_imag_attributes(self):
        _assert_matches(self, """
z = 2+3j
imprimir(z.real)
imprimir(z.imag)
imprimir((2+3j).real)
imprimir((2.5j).imag)
imprimir((-3-4j).real)
imprimir((2+3j).real + (1+1j).imag)
""")

    def test_constructor(self):
        _assert_matches(self, """
imprimir(complejo())
imprimir(complejo(2))
imprimir(complejo(2,3))
imprimir(complejo(1.5,2))
imprimir(complejo(3.5))
z = complejo(1,2)
imprimir(complejo(z))
""")

    def test_str_and_formatting(self):
        _assert_matches(self, """
imprimir(texto(1+2j))
imprimir('%s' % (1+2j))
imprimir('%r' % (1+2j))
imprimir('v={}'.format(1+2j))
imprimir(f"{1+2j}")
""")

    def test_type_and_isinstance(self):
        _assert_matches(self, """
imprimir(tipo(1+2j))
imprimir(es_instancia(1+2j, complejo))
imprimir(es_instancia(1+2j, decimal))
imprimir(es_instancia(1.5, complejo))
imprimir(es_instancia(complejo(0,0), complejo))
""")

    def test_collections_and_membership(self):
        _assert_matches(self, """
imprimir([1+2j, 2+2j])
imprimir([1+2j, 2+2j][0])
imprimir((1+2j) en [1+2j])
imprimir((1+2j) no en [1+2j])
imprimir(1j en {1j: 'x'})
imprimir(1j en {1j})
para z en [1j, 2j]:
    imprimir(z)
d = {1j: 'a'}
imprimir(d[1j])
""")

    def test_variables_and_return(self):
        _assert_matches(self, """
z = complejo(1,2)
imprimir(z)
z2 = z*z
imprimir(z2)
imprimir(z.real + z.imag)

funcion crear(a):
    devolver complejo(a, a)

imprimir(crear(2))
imprimir(crear(2).imag)
""")

    def test_fail_closed_cases(self):
        # complejo cruzando el ABI de llamada (parámetros tipados int)
        _assert_build_fails(self, """
funcion f(x):
    imprimir(x)
f(1+2j)
""")
        # piso, módulo y potencia: fuera del subconjunto (CPythonTypeError o
        # cmath); el gate falla cerrado en vez de aproximar.
        _assert_build_fails(self, "imprimir(1j // 2)")
        _assert_build_fails(self, "imprimir(1j % 2)")
        _assert_build_fails(self, "imprimir(1j ** 2)")
        # constructor desde string
        _assert_build_fails(self, "imprimir(complejo('1+2j'))")


if __name__ == "__main__":
    unittest.main()
