"""W22 file_none_conflict_attrs: None-conflict locales cuyo otro valor es un
handle de fichero (`fp = open(...)`). Lecturas, escrituras, cierres y
atributos (`closed`, `name`, `mode`) despachan en runtime por el kind de la
caja: `PK_NONE` lanza el `AttributeError` de CPython; un fichero despacha a
los m\u00e9todos y atributos reales del fichero. Paridad con CPython 3.12
(stdout + c\u00f3digo de salida, o la misma excepci\u00f3n); los casos no
soportados siguen fallando cerrados.

Repro exactos: /var/tmp/piton_w22_orch/repro/file_none_conflict_attrs.py
"""
from __future__ import annotations

import unittest

from piton.native_differential import compare_native_to_cpython
from piton.x86 import NativeBuildError


def _assert_matches(testcase: unittest.TestCase, source: str) -> None:
    result = compare_native_to_cpython(source)
    testcase.assertEqual(
        result.native.returncode,
        result.oracle.returncode,
        f"returncode mismatch\nnative stderr: {result.native.stderr!r}\n"
        f"oracle stderr: {result.oracle.stderr!r}",
    )
    testcase.assertEqual(
        result.native.stdout,
        result.oracle.stdout,
        f"stdout mismatch\nnative stderr: {result.native.stderr!r}\n"
        f"oracle stderr: {result.oracle.stderr!r}",
    )


def _assert_fails_closed(testcase: unittest.TestCase, source: str) -> None:
    with testcase.assertRaises(NativeBuildError):
        compare_native_to_cpython(source)


class FileNoneConflictAttrsParity(unittest.TestCase):
    # --- Repro exactos ---------------------------------------------------

    def test_file_none_then_open_read(self):
        # fp lee /etc/hosts cuando flag es Verdadero; cuando es Falso sigue
        # siendo None y no se toca.
        _assert_matches(
            self,
            "funcion leer_si(flag):\n"
            "    fp = None\n"
            "    si flag:\n"
            "        fp = open(\"/etc/hosts\", \"r\")\n"
            "    si fp != None:\n"
            "        datos = fp.read()\n"
            "        fp.close()\n"
            "        devolver len(datos) > 0\n"
            "    devolver Falso\n"
            "imprimir(leer_si(Verdadero), leer_si(Falso))\n",
        )

    def test_file_close_none_attribute_error(self):
        # fp None => fp.close() lanza AttributeError, capturado por excepto.
        _assert_matches(
            self,
            "funcion cerrar_none(flag):\n"
            "    fp = None\n"
            "    si flag:\n"
            "        fp = open(\"/etc/hosts\", \"r\")\n"
            "    intentar:\n"
            "        fp.close()\n"
            "        devolver \"ok\"\n"
            "    excepto AttributeError:\n"
            "        devolver \"attr_err\"\n"
            "imprimir(cerrar_none(Falso))\n",
        )

    # --- m\u00e9todos de fichero ------------------------------------------

    def test_file_readline_close_parity(self):
        _assert_matches(
            self,
            "funcion leer_linea(flag):\n"
            "    fp = None\n"
            "    si flag:\n"
            "        fp = open(\"/etc/hosts\", \"r\")\n"
            "    si fp != None:\n"
            "        linea = fp.readline()\n"
            "        fp.close()\n"
            "        devolver len(linea) > 0\n"
            "    devolver Falso\n"
            "imprimir(leer_linea(Verdadero), leer_linea(Falso))\n",
        )

    def test_file_readlines_parity(self):
        _assert_matches(
            self,
            "funcion leer_todas(flag):\n"
            "    fp = None\n"
            "    si flag:\n"
            "        fp = open(\"/etc/hosts\", \"r\")\n"
            "    si fp != None:\n"
            "        lineas = fp.readlines()\n"
            "        fp.close()\n"
            "        devolver len(lineas) > 0\n"
            "    devolver Falso\n"
            "imprimir(leer_todas(Verdadero), leer_todas(Falso))\n",
        )

    def test_file_write_roundtrip_parity(self):
        _assert_matches(
            self,
            "funcion escribir_leer():\n"
            "    fp = None\n"
            "    fp = open(\"/tmp/piton_w22_file_write_roundtrip\", \"w\")\n"
            "    fp.write(\"hola piton\")\n"
            "    fp.close()\n"
            "    fp = open(\"/tmp/piton_w22_file_write_roundtrip\", \"r\")\n"
            "    datos = fp.read()\n"
            "    fp.close()\n"
            "    devolver datos\n"
            "imprimir(escribir_leer())\n",
        )

    # --- atributos de fichero -------------------------------------------

    def test_file_closed_before_and_after_close(self):
        _assert_matches(
            self,
            "funcion info(flag):\n"
            "    fp = None\n"
            "    si flag:\n"
            "        fp = open(\"/etc/hosts\", \"r\")\n"
            "    si fp != None:\n"
            "        antes = fp.closed\n"
            "        fp.close()\n"
            "        despues = fp.closed\n"
            "        devolver despues y no antes\n"
            "    devolver Falso\n"
            "imprimir(info(Verdadero), info(Falso))\n",
        )

    def test_file_name_and_mode(self):
        _assert_matches(
            self,
            "funcion info(flag):\n"
            "    fp = None\n"
            "    si flag:\n"
            "        fp = open(\"/etc/hosts\", \"r\")\n"
            "    si fp != None:\n"
            "        n = len(fp.name) > 0\n"
            "        m = fp.mode == \"r\"\n"
            "        fp.close()\n"
            "        devolver n y m\n"
            "    devolver Falso\n"
            "imprimir(info(Verdadero), info(Falso))\n",
        )

    # --- AttributeError cuando fp es None --------------------------------

    def test_file_read_attribute_error_caught_when_none(self):
        # fp None => fp.read() lanza AttributeError, capturado; fp fichero =>
        # read devuelve contenido y devuelve "ok".
        _assert_matches(
            self,
            "funcion leer_o_ae(flag):\n"
            "    fp = None\n"
            "    si flag:\n"
            "        fp = open(\"/etc/hosts\", \"r\")\n"
            "    intentar:\n"
            "        fp.read()\n"
            "        devolver \"ok\"\n"
            "    excepto AttributeError:\n"
            "        devolver \"ae\"\n"
            "imprimir(leer_o_ae(Falso), leer_o_ae(Verdadero))\n",
        )

    def test_uncaught_attribute_error_on_none_path(self):
        # fp None y sin except: `fp.read()` como sentencia (resultado
        # descartado, para no tocar el bug T1 de retornar strings). None
        # lanza AttributeError sin capturar; mismo c\u00f3digo de salida que
        # CPython.
        _assert_matches(
            self,
            "funcion f(flag):\n"
            "    fp = None\n"
            "    si flag:\n"
            "        fp = open(\"/etc/hosts\", \"r\")\n"
            "    fp.read()\n"
            "    devolver Verdadero\n"
            "imprimir(f(Verdadero))\n"
            "imprimir(f(Falso))\n",
        )

    # --- casos que deben seguir fallando cerrados ------------------------

    def test_file_and_user_class_on_one_none_conflict_variable_fails_closed(self):
        # Un mismo local no-conflicto con `file` y una clase de usuario no
        # admite despacho dinamico (dos candidatos PK_OBJECT). `m` se usa como
        # receptor de get_attr para forzar el despacho.
        _assert_fails_closed(
            self,
            "clase C:\n"
            "    funcion __init__(self):\n"
            "        self.v = 1\n"
            "funcion f(x):\n"
            "    m = None\n"
            "    si x:\n"
            "        m = open(\"/etc/hosts\", \"r\")\n"
            "    sino:\n"
            "        m = C()\n"
            "    devolver m.closed\n"
            "imprimir(f(Verdadero))\n",
        )

    def test_module_level_none_conflict_file_fails_closed(self):
        # Un local de m\u00f3dulo None-conflicto con `file` usado como receptor
        # sigue fallando cerrado (el despacho dinamico solo se habilita dentro
        # de funciones).
        _assert_fails_closed(
            self,
            "m = None\n"
            "m = open(\"/etc/hosts\", \"r\")\n"
            "imprimir(m.closed)\n",
        )


if __name__ == "__main__":
    unittest.main()
