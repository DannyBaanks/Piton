"""INTROSPECTION_V1: Introspección y reflexión nativa en PITON.

Implementa y valida:
  - isinstance / es_instancia (tipos primitivos, bool-is-int, MRO, tuplas de tipos)
  - issubclass / es_subclase (jerarquía de clases y tipos base, tuplas de tipos)
  - hasattr / tiene_atr (campos de instancia en heap, atributos de clase, métodos MRO)
  - getattr / obtener_atr (lectura directa, fallback con default, y AttributeError capturable)
  - setattr / fijar_atr / establecer_atr (mutación dinámica de atributos en heap)
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


class TestIntrospectionV1(unittest.TestCase):
    def test_isinstance_primitives(self):
        source = """
imprimir(isinstance(5, int))
imprimir(isinstance(5, str))
imprimir(isinstance('hola', str))
imprimir(isinstance('hola', int))
imprimir(isinstance(3.14, float))
imprimir(isinstance(Verdadero, bool))
imprimir(isinstance([1, 2], list))
imprimir(isinstance((1, 2), tuple))
imprimir(isinstance({'a': 1}, dict))
imprimir(isinstance({1, 2}, set))
imprimir(isinstance(5, object))
"""
        _assert_matches(self, source)

    def test_isinstance_bool_subclass_of_int(self):
        source = """
imprimir(isinstance(Verdadero, int))
imprimir(isinstance(Falso, int))
imprimir(isinstance(1, bool))
"""
        _assert_matches(self, source)

    def test_isinstance_tuple_of_types(self):
        source = """
imprimir(isinstance(5, (str, int)))
imprimir(isinstance('a', (int, float, str)))
imprimir(isinstance(3.14, (int, str)))
imprimir(isinstance(5, (str, (float, int))))
"""
        _assert_matches(self, source)

    def test_isinstance_custom_classes(self):
        source = """
clase A:
    pasar

clase B(A):
    pasar

clase C:
    pasar

b = B()
imprimir(isinstance(b, B))
imprimir(isinstance(b, A))
imprimir(isinstance(b, C))
imprimir(isinstance(b, object))
imprimir(isinstance(b, (C, A)))
"""
        _assert_matches(self, source)

    def test_issubclass_basic(self):
        source = """
clase A:
    pasar

clase B(A):
    pasar

clase C:
    pasar

imprimir(issubclass(B, A))
imprimir(issubclass(B, B))
imprimir(issubclass(A, B))
imprimir(issubclass(B, object))
imprimir(issubclass(bool, int))
imprimir(issubclass(int, int))
imprimir(issubclass(B, (C, A)))
"""
        _assert_matches(self, source)

    def test_hasattr_fields_and_methods(self):
        source = """
clase Persona:
    funcion __init__(self, nombre):
        self.nombre = nombre
    funcion saludar(self):
        devolver 'hola'

p = Persona('Danny')
imprimir(hasattr(p, 'nombre'))
imprimir(hasattr(p, 'saludar'))
imprimir(hasattr(p, 'edad'))
"""
        _assert_matches(self, source)

    def test_getattr_and_setattr(self):
        source = """
clase Caja:
    funcion __init__(self):
        self.valor = 10

c = Caja()
imprimir(getattr(c, 'valor'))
imprimir(getattr(c, 'extra', 99))
setattr(c, 'nuevo', 42)
imprimir(getattr(c, 'nuevo'))
imprimir(c.nuevo)
"""
        _assert_matches(self, source)

    def test_getattr_missing_raises_attribute_error(self):
        source = """
clase Caja:
    pasar

c = Caja()
intentar:
    imprimir(getattr(c, 'no_existe'))
excepto AttributeError:
    imprimir('caught AttributeError')
"""
        _assert_matches(self, source)

    def test_spanish_aliases(self):
        source = """
clase Base:
    pasar

clase Derivada(Base):
    funcion __init__(self):
        self.x = 100

d = Derivada()
imprimir(es_instancia(d, Base))
imprimir(es_instancia(5, entero))
imprimir(es_subclase(Derivada, Base))
imprimir(tiene_atr(d, 'x'))
imprimir(obtener_atr(d, 'x'))
fijar_atr(d, 'y', 200)
imprimir(obtener_atr(d, 'y'))
establecer_atr(d, 'z', 300)
imprimir(obtener_atr(d, 'z'))
"""
        _assert_matches(self, source)


if __name__ == "__main__":
    unittest.main()
