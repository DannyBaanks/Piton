"""Parity checks for super() with dynamically evaluated/imported base classes (W23 super_dynamic_base_mro).

These tests cover classes that inherit from builtin types like GenericAlias, type,
_compression.BaseStream, inspect._Traceback, etc. that are resolved through
module/alias bindings rather than being statically known.

Parity = byte-identical stdout and identical exit code against CPython 3.12.
Constructs that the native backend cannot match must fail closed with
NativeBuildError at build time; they must never build and print something else.
"""
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


class SuperDynamicBaseMroParity(unittest.TestCase):
    def test_generic_alias_subclass_super_new(self):
        """Test subclass of GenericAlias with super().__new__."""
        _assert_matches(
            self,
            "desde types importar GenericAlias\n"
            "clase _CallableGenericAlias(GenericAlias):\n"
            "    funcion __new__(cls, origin, args):\n"
            "        super().__new__(cls, origin, args)\n"
            "    funcion __repr__(self):\n"
            "        super().__repr__()\n"
            "    funcion __getitem__(self, item):\n"
            "        super().__getitem__(item)\n",
        )

    def test_type_subclass_super_new(self):
        """Test subclass of type with super().__new__."""
        _assert_matches(
            self,
            "clase MiMeta(type):\n"
            "    funcion __new__(cls, name, bases, namespace):\n"
            "        super().__new__(cls, name, bases, namespace)\n",
        )

    def test_compression_base_stream_subclass_super_tell(self):
        """Test subclass of _compression.BaseStream with super().tell()."""
        _assert_matches(
            self,
            "importar _compression\n"
            "clase MiStream(_compression.BaseStream):\n"
            "    funcion tell(self):\n"
            "        super().tell()\n",
        )

    def test_inspect_traceback_subclass_super_init(self):
        """Test subclass of inspect._Traceback with super().__init__()."""
        _assert_matches(
            self,
            "importar inspect\n"
            "clase MiTraceback(inspect._Traceback):\n"
            "    funcion __init__(self):\n"
            "        super().__init__()\n",
        )

    def test_io_buffered_io_base_subclass(self):
        """Test subclass of io.BufferedIOBase."""
        _assert_matches(
            self,
            "importar io\n"
            "clase MiBuffered(io.BufferedIOBase):\n"
            "    funcion __init__(self):\n"
            "        super().__init__()\n",
        )


class SuperDynamicBaseMroFailClosed(unittest.TestCase):
    def test_unmodeled_method_on_generic_alias_fails_closed(self):
        """Unmodeled methods on GenericAlias should fail closed."""
        with pytest.raises(NativeBuildError, match="has no modeled native implementation"):
            compare_native_to_cpython(
                "desde types importar GenericAlias\n"
                "clase MiClase(GenericAlias):\n"
                "    funcion metodo_desconocido(self):\n"
                "        super().metodo_desconocido()\n"
            )

    def test_unmodeled_method_on_compression_base_stream_fails_closed(self):
        """Unmodeled methods on _compression.BaseStream should fail closed."""
        with pytest.raises(NativeBuildError, match="has no modeled native implementation"):
            compare_native_to_cpython(
                "importar _compression\n"
                "clase MiClase(_compression.BaseStream):\n"
                "    funcion metodo_desconocido(self):\n"
                "        super().metodo_desconocido()\n"
            )

    def test_unmodeled_method_on_inspect_traceback_fails_closed(self):
        """Unmodeled methods on inspect._Traceback should fail closed."""
        with pytest.raises(NativeBuildError, match="has no modeled native implementation"):
            compare_native_to_cpython(
                "importar inspect\n"
                "clase MiClase(inspect._Traceback):\n"
                "    funcion metodo_desconocido(self):\n"
                "        super().metodo_desconocido()\n"
            )


if __name__ == "__main__":
    unittest.main()