"""Focused differential tests for Wave 24 linux-dyn targets in piton/linux_x86.py.

Cubre:
- set_attr sobre objeto dinamico y None (NONE_CONFLICT_SLOT_V1).
- ramas dinamicas con resultados de tipo mixto (int vs str vs none)
  -> resultado boxeado como PitonSlot con kind real (MIXED_RESULT_TYPES_V1).
- traceback: celda pasada a parametro que espera str en closures.
- enum: subclase de lista/diccionario pasada a setattr (objeto de heap mutable).
Cada caso compara stdout + codigo de salida contra CPython 3.12, con casos
positivos (igualdad) y negativos (mismo error/tipo de fallo).
"""
from __future__ import annotations

import unittest

from piton.native_differential import compare_native_to_cpython
from piton.x86 import NativeBuildError


def _assert_matches(source: str) -> None:
    result = compare_native_to_cpython(source)
    unittest.TestCase().assertEqual(
        result.native.returncode, result.oracle.returncode,
        f"returncode mismatch\nnative stderr: {result.native.stderr!r}",
    )
    unittest.TestCase().assertEqual(
        result.native.stdout, result.oracle.stdout,
        f"stdout mismatch\nnative stderr: {result.native.stderr!r}",
    )


def _assert_exception_name(source: str, exc_name: str) -> None:
    """Lanza exactamente `exc_name` (tipo de excepcion de CPython) en nativo y
    oracle, comparando rc y el nombre de la excepcion en stderr."""
    result = compare_native_to_cpython(source)
    unittest.TestCase().assertEqual(
        result.native.returncode, result.oracle.returncode,
        f"returncode mismatch\nnative stderr: {result.native.stderr!r}"
        f"\noracle   stderr: {result.oracle.stderr!r}",
    )
    unittest.TestCase().assertEqual(
        result.native.returncode, 1,
        f"se esperaba rc 1 (excepcion no capturada), nativo devolvio "
        f"{result.native.returncode}\nnative stderr: {result.native.stderr!r}",
    )
    native_exc = result.native.stderr.decode().strip().splitlines()[-1].split(":", 1)[0]
    oracle_exc = result.oracle.stderr.decode().strip().splitlines()[-1].split(":", 1)[0]
    unittest.TestCase().assertEqual(
        native_exc, exc_name,
        f"nativo lanza {native_exc!r}, se esperaba {exc_name!r}\n"
        f"native stderr: {result.native.stderr!r}",
    )
    unittest.TestCase().assertEqual(
        oracle_exc, exc_name,
        f"CPython lanza {oracle_exc!r}, se esperaba {exc_name!r}\n"
        f"oracle stderr: {result.oracle.stderr!r}",
    )


def _assert_build_fails(source: str) -> None:
    """El fuente no debe compilar (fail-closed honesto) en el backend nativo."""
    with unittest.TestCase().assertRaises(NativeBuildError):
        compare_native_to_cpython(source)


class TestLinuxDynObjects(unittest.TestCase):
    def _assert_matches(self, source: str) -> None:
        result = compare_native_to_cpython(source)
        self.assertEqual(
            result.native.returncode, result.oracle.returncode,
            f"returncode mismatch\nnative stderr: {result.native.stderr!r}",
        )
        self.assertEqual(
            result.native.stdout, result.oracle.stdout,
            f"stdout mismatch\nnative stderr: {result.native.stderr!r}",
        )

    # ── set_attr sobre objeto dinamico y None ──────────────────────────

    def test_set_attr_on_dynamic_object_and_none(self):
        # obj es None en una rama y un objeto de usuario en la otra.
        # set_attr sobre None debe lanzar AttributeError como CPython.
        src = (
            "clase Controller:\n"
            "    funcion __init__(este):\n"
            "        este.name = \"default\"\n"
            "funcion register(c, flag):\n"
            "    obj = None\n"
            "    si flag:\n"
            "        obj = c\n"
            "    si obj no es None:\n"
            "        setattr(obj, \"name\", \"custom\")\n"
            "        imprimir(obj.name)\n"
            "    sino:\n"
            "        imprimir(\"none\")\n"
            "register(Controller(), Verdadero)\n"
            "register(Controller(), Falso)\n"
        )
        self._assert_matches(src)

    def test_set_attr_uncaught_none_raises_attribute_error(self):
        # set_attr directo sobre receptor None sin intentar/excepto:
        # mismo AttributeError que CPython (tipo real, no TypeError silencioso).
        _assert_exception_name(
            "setattr(None, \"x\", 1)\n", "AttributeError"
        )

    def test_setattr_on_none_attribute_error_message(self):
        # Mensaje exacto de CPython 3.12 para setattr sobre None.
        src = 'setattr(None, "foo", "bar")\n'
        result = compare_native_to_cpython(src)
        self.assertEqual(result.native.returncode, result.oracle.returncode)
        native_last = result.native.stderr.decode().strip().splitlines()[-1]
        oracle_last = result.oracle.stderr.decode().strip().splitlines()[-1]
        self.assertEqual(native_last, oracle_last)
        self.assertIn("'NoneType' object has no attribute 'foo'", native_last)

    # ── ramas dinamicas con resultados de tipo mixto ───────────────────

    def test_mixed_dynamic_return_types_int_none(self):
        # Rama int vs rama none: resultado boxeado como PitonSlot.
        src = (
            "funcion check(flag, x):\n"
            "    si flag:\n"
            "        devolver x\n"
            "    devolver None\n"
            "a = check(Verdadero, 10)\n"
            "b = check(Falso, 20)\n"
            "imprimir(a, b)\n"
        )
        self._assert_matches(src)

    def test_mixed_dynamic_return_types_str_int(self):
        # Rama str vs rama int: resultado boxeado como PitonSlot.
        # CPython devuelve "abc" / 123, piton debe hacer lo mismo.
        src = (
            "funcion check(flag):\n"
            "    si flag:\n"
            "        devolver \"abc\"\n"
            "    devolver 123\n"
            "imprimir(check(Verdadero))\n"
            "imprimir(check(Falso))\n"
        )
        self._assert_matches(src)

    def test_mixed_dynamic_return_types_str_int_none(self):
        # Tres ramas: str, int, none. Resultado boxeado.
        src = (
            "funcion check(n):\n"
            "    si n == 1:\n"
            "        devolver \"uno\"\n"
            "    si n == 2:\n"
            "        devolver 2\n"
            "    devolver None\n"
            "imprimir(check(1), check(2), check(3))\n"
        )
        self._assert_matches(src)

    def test_mixed_return_float_int_type_and_print(self):
        # float vs int: el float NO debe imprimirse como bits crudos y
        # type(v).__name__ debe ser el tipo real (float / int).
        src = (
            "funcion check(flag):\n"
            "    si flag:\n"
            "        devolver 3.5\n"
            "    devolver 2\n"
            "a = check(Verdadero)\n"
            "b = check(Falso)\n"
            "imprimir(tipo(a).__nombre__, a)\n"
            "imprimir(tipo(b).__nombre__, b)\n"
        ).replace("__nombre__", "__name__")
        self._assert_matches(src)

    def test_mixed_return_str_bool_none_type_and_print(self):
        # str vs bool vs none: cada uno conserva su kind real en runtime,
        # tanto al imprimir como en type(v).__name__.
        src = (
            "funcion check(n):\n"
            "    si n == 1:\n"
            "        devolver \"uno\"\n"
            "    si n == 2:\n"
            "        devolver Verdadero\n"
            "    devolver None\n"
            "imprimir(tipo(check(1)).__nombre__, check(1))\n"
            "imprimir(tipo(check(2)).__nombre__, check(2))\n"
            "imprimir(tipo(check(3)).__nombre__, check(3))\n"
        ).replace("__nombre__", "__name__")
        self._assert_matches(src)

    def test_mixed_return_int_none_type_and_print(self):
        # int vs none: type(None).__name__ es 'NoneType', no 'int'.
        src = (
            "funcion check(flag, x):\n"
            "    si flag:\n"
            "        devolver x\n"
            "    devolver None\n"
            "a = check(Verdadero, 10)\n"
            "b = check(Falso, 20)\n"
            "imprimir(tipo(a).__nombre__, a)\n"
            "imprimir(tipo(b).__nombre__, b)\n"
        ).replace("__nombre__", "__name__")
        self._assert_matches(src)

    def test_mixed_dynamic_get_attr_object(self):
        # get_attr sobre variable none-conflict donde el atributo tiene
        # distinto tipo segun la instancia (misma clase, attrs distintos).
        src = (
            "clase C:\n"
            "    pasar\n"
            "funcion make(flag):\n"
            "    o = None\n"
            "    si flag:\n"
            "        o = C()\n"
            "        o.val = 10\n"
            "    sino:\n"
            "        o = C()\n"
            "        o.val = \"veinte\"\n"
            "    devolver o.val\n"
            "imprimir(make(Verdadero))\n"
            "imprimir(make(Falso))\n"
        )
        self._assert_matches(src)

    # ── traceback: celda a parametro str en closures ───────────────────

    def test_traceback_cell_param_str(self):
        src = (
            "funcion outer(s):\n"
            "    funcion inner():\n"
            "        devolver longitud(s)\n"
        )
        src += "    devolver inner()\n"
        src += "imprimir(outer(\"hello\"))\n"
        self._assert_matches(src)

    def test_traceback_cell_param_str_multiple(self):
        src = (
            "funcion outer(s):\n"
            "    funcion inner():\n"
            "        devolver texto(s)\n"
            "    return inner()\n"
            "imprimir(outer(\"hola\"))\n"
        ).replace("return inner()", "    devolver inner()")
        self._assert_matches(src)

    # ── enum: list/dict subclass con setattr ───────────────────────────

    def test_enum_subclass_setattr_list(self):
        src = (
            "clase MyList(lista):\n"
            "    pasar\n"
            "obj = MyList([1, 2, 3])\n"
            "setattr(obj, \"attr\", \"val\")\n"
            "imprimir(getattr(obj, \"attr\"))\n"
        )
        self._assert_matches(src)

    def test_enum_subclass_setattr_dict(self):
        src = (
            "clase MyDict(diccionario):\n"
            "    pasar\n"
            "obj = MyDict()\n"
            "setattr(obj, \"attr\", \"val\")\n"
            "imprimir(getattr(obj, \"attr\"))\n"
        )
        self._assert_matches(src)

    # ── negativos: fail-closed honesto ─────────────────────────────────

    # ── negativos: fail-closed honesto ─────────────────────────────────

    def test_negative_setattr_on_none_raises(self):
        # setattr sobre receptor None: AttributeError como CPython,
        # nunca silencioso ni codigo 0 erroneo.
        src = (
            "clase C:\n"
            "    funcion __init__(este):\n"
            "        este.x = 1\n"
        )
        src += (
            "funcion f(obj):\n"
            "    setattr(None, \"x\", 1)\n"
            "    devolver obj.x\n"
        )
        src += "imprimir(f(C()))\n"
        self._assert_matches(src)


if __name__ == "__main__":
    unittest.main()
