from __future__ import annotations

import sys
import unittest

from piton.native_differential import compare_native_to_cpython


@unittest.skipUnless(sys.platform == "win32", "Windows PE validation only")
class WindowsValidation(unittest.TestCase):
    """Focused Windows receipt corpus for the current parity blockers."""

    def assert_equivalent(self, source: str) -> None:
        result = compare_native_to_cpython(source)
        self.assertTrue(
            result.equivalent,
            "native="
            + repr(result.native)
            + " oracle="
            + repr(result.oracle),
        )

    def test_t2_raise_from_and_exception_chain(self) -> None:
        self.assert_equivalent(
            "intentar:\n"
            "    intentar:\n"
            "        lanzar TypeError(\"causa original\")\n"
            "    excepto TypeError:\n"
            "        lanzar ValueError(\"nuevo error\") desde TypeError(\"causa original\")\n"
            "excepto ValueError:\n"
            "    imprimir(\"caught\")\n"
        )

    def test_t2_raise_from_none(self) -> None:
        self.assert_equivalent(
            "intentar:\n"
            "    intentar:\n"
            "        lanzar TypeError(\"suppressed\")\n"
            "    excepto TypeError:\n"
            "        lanzar ValueError(\"clean\") desde Nada\n"
            "excepto ValueError:\n"
            "    imprimir(\"caught\")\n"
        )

    def test_t2_base_exception(self) -> None:
        self.assert_equivalent(
            "intentar:\n"
            "    lanzar BaseException(\"raw\")\n"
            "excepto BaseException:\n"
            "    imprimir(\"caught-raw\")\n"
        )

    def test_t5_math_floor_ceil(self) -> None:
        self.assert_equivalent(
            "importar math\n"
            "imprimir(math.floor(2.7))\n"
            "imprimir(math.floor(-2.3))\n"
            "imprimir(math.ceil(2.3))\n"
            "imprimir(math.ceil(-2.7))\n"
        )

    def test_t5_math_trig_log(self) -> None:
        self.assert_equivalent(
            "importar math\n"
            "imprimir(math.sin(0.0))\n"
            "imprimir(math.cos(0.0))\n"
            "imprimir(math.log(1.0))\n"
        )

    def test_t5_sys_argv_length(self) -> None:
        self.assert_equivalent("importar sys\nimprimir(longitud(sys.argv))\n")

    def test_t5_os_name(self) -> None:
        self.assert_equivalent("desde os importar name\nimprimir(name)\n")

    def test_t5_sys_exit(self) -> None:
        self.assert_equivalent(
            "importar sys\n"
            "imprimir(\"before-exit\")\n"
            "sys.exit(3)\n"
        )


if __name__ == "__main__":
    unittest.main()
