"""Parity checks for runtime-typed isinstance parameters and path arguments."""
from __future__ import annotations

import unittest

import pytest

from piton.native_differential import compare_native_to_cpython
from piton.x86 import NativeBuildError


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


class IsinstanceMixedParams(unittest.TestCase):
    def test_runtime_isinstance_dispatch_for_str_and_bytes(self):
        _assert_matches(
            self,
            "funcion tipo (valor):\n"
            "    si es_instancia (valor, str):\n"
            "        devolver ('str')\n"
            "    sino_si es_instancia (valor, bytes):\n"
            "        devolver ('bytes')\n"
            "    sino:\n"
            "        devolver ('other')\n"
            "imprimir (tipo ('ruta' ))\n"
            "imprimir (tipo (b'ruta' ))\n",
        )

    def test_posixpath_join_normpath_and_splitext_keep_path_kind(self):
        _assert_matches(
            self,
            "importar posixpath\n"
            "imprimir (posixpath .join ('alpha', 'beta' ))\n"
            "imprimir (posixpath .join (b'alpha', b'beta' ))\n"
            "imprimir (posixpath .join ('' ))\n"
            "imprimir (posixpath .join (b'' ))\n"
            "imprimir (posixpath .normpath ('../a/../../b' ))\n"
            "imprimir (posixpath .normpath (b'../a/../../b' ))\n"
            "imprimir (posixpath .splitext ('.hidden' ))\n"
            "imprimir (posixpath .splitext (b'...name.ext' ))\n"
            "imprimir (posixpath .splitext ('....a' ))\n"
            "imprimir (posixpath .splitext (b'....a' ))\n"
            "importar os\n"
            "imprimir (os .path .splitext ('a.tar.gz' ))\n"
            "imprimir (os .path .splitext (b'a.tar.gz' ))\n"
            "imprimir (os .path .join ('' ))\n"
            "imprimir (os .path .join (b'' ))\n",
        )

    def test_posixpath_join_mixed_kinds_raises_typeerror(self):
        result = compare_native_to_cpython(
            "importar posixpath\n"
            "posixpath .join ('alpha', b'beta')\n"
        )
        self.assertEqual(result.native.returncode, result.oracle.returncode)
        self.assertEqual(result.native.stdout, result.oracle.stdout)
        self.assertEqual(result.native.returncode, 1)
        self.assertIn(b"TypeError:", result.native.stderr)
        self.assertIn(b"TypeError:", result.oracle.stderr)

    def test_split_helpers_preserve_str_and_bytes_path_kinds(self):
        _assert_matches(
            self,
            "importar posixpath\n"
            "imprimir (posixpath .basename ('alpha' ))\n"
            "imprimir (posixpath .basename (b'alpha' ))\n"
            "imprimir (posixpath .dirname ('alpha' ))\n"
            "imprimir (posixpath .dirname (b'alpha' ))\n"
            "imprimir (posixpath .isabs ('/alpha/beta' ))\n"
            "imprimir (posixpath .isabs (b'/alpha/beta' ))\n"
            "imprimir (posixpath .split ('/alpha/beta' ))\n"
            "imprimir (posixpath .split (b'/alpha/beta' ))\n"
            "imprimir (posixpath .splitdrive ('/alpha/beta' ))\n"
            "imprimir (posixpath .splitdrive (b'/alpha/beta' ))\n"
            "importar os\n"
            "imprimir (os .path .basename (b'/alpha/beta' ))\n"
            "imprimir (os .path .dirname (b'/alpha/beta' ))\n"
            "imprimir (os .path .isabs (b'/alpha/beta' ))\n"
            "imprimir (os .path .split (b'/alpha/beta' ))\n"
            "imprimir (os .path .splitdrive (b'/alpha/beta' ))\n",
        )

    def test_closure_is_not_accepted_as_a_path(self):
        result = compare_native_to_cpython(
            "importar posixpath\n"
            "funcion callback():\n"
            "    devolver ('x')\n"
            "posixpath .splitext (callback)\n"
        )
        self.assertEqual(result.native.returncode, result.oracle.returncode)
        self.assertEqual(result.native.stdout, result.oracle.stdout)
        self.assertEqual(result.native.returncode, 1)
        self.assertIn(b"TypeError:", result.native.stderr)
        self.assertIn(b"TypeError:", result.oracle.stderr)

    def test_literal_int_and_str_type_conflict_stays_fail_closed(self):
        with pytest.raises(
            NativeBuildError,
            match=r"parametro 'valor' de 'tipo' recibe tipos distintos",
        ):
            compare_native_to_cpython(
                "funcion tipo (valor):\n"
                "    si es_instancia (valor, str):\n"
                "        devolver (valor)\n"
                "imprimir (tipo (3 ))\n"
                "imprimir (tipo ('ruta' ))\n"
            )


if __name__ == "__main__":
    unittest.main()
