"""W25 native introspection parity for source-backed metadata."""
from __future__ import annotations

import unittest

from piton.native_differential import compare_native_to_cpython


def _assert_matches(testcase: unittest.TestCase, source: str):
    try:
        result = compare_native_to_cpython(source)
    except Exception as exc:
        testcase.fail(f"comparison raised {type(exc).__name__}: {exc!r}")
    testcase.assertEqual(
        result.native.returncode,
        result.oracle.returncode,
        f"returncode mismatch\nnative stderr: {result.native.stderr!r}\noracle stderr: {result.oracle.stderr!r}",
    )
    testcase.assertEqual(
        result.native.stdout,
        result.oracle.stdout,
        f"stdout mismatch\nnative stdout: {result.native.stdout!r}\n"
        f"oracle stdout: {result.oracle.stdout!r}\n"
        f"native stderr: {result.native.stderr!r}\noracle stderr: {result.oracle.stderr!r}",
    )
    testcase.assertEqual(result.native.stderr, result.oracle.stderr, "stderr mismatch")
    return result


class DocstringParity(unittest.TestCase):
    def test_module_function_class_and_method_docstrings(self) -> None:
        _assert_matches(
            self,
            '"""module documentation"""\n'
            "clase C:\n"
            '    """class documentation"""\n'
            "    value = 3\n"
            "    funcion method(self):\n"
            '        """method documentation"""\n'
            "        devolver self.value\n"
            "funcion documented(value):\n"
            '    """function documentation"""\n'
            "    devolver value\n"
            "funcion undocumented():\n"
            "    devolver None\n"
            "imprimir(__doc__)\n"
            "imprimir(C.__doc__)\n"
            "imprimir(C(). __doc__)\n"
            "imprimir(C.method.__doc__)\n"
            "imprimir(C().method.__doc__)\n"
            "imprimir(documented.__doc__)\n"
            "imprimir(undocumented.__doc__)\n",
        )

    def test_leading_string_is_the_only_class_documentation(self) -> None:
        _assert_matches(
            self,
            "clase WithDoc:\n"
            '    "leading literal"\n'
            "    value = 1\n"
            "clase WithoutDoc:\n"
            "    value = 2\n"
            "imprimir(WithDoc.__doc__)\n"
            "imprimir(WithoutDoc.__doc__)\n",
        )


class DirParity(unittest.TestCase):
    def test_user_class_and_instance_include_sorted_source_attributes(self) -> None:
        _assert_matches(
            self,
            "clase C:\n"
            '    """class docs"""\n'
            "    value = 3\n"
            "    funcion method(self):\n"
            "        devolver self.value\n"
            "obj = C()\n"
            "imprimir(dir(C))\n"
            "imprimir(dir(obj))\n",
        )

    def test_module_scope_dir_and_function_dir(self) -> None:
        _assert_matches(
            self,
            "answer = 42\n"
            "funcion documented(value):\n"
            '    """function docs"""\n'
            "    devolver value\n"
            "imprimir(dir())\n"
            "imprimir(dir(documented))\n",
        )


class HelpBehavior(unittest.TestCase):
    def test_help_uses_captured_docs_and_returns_none(self) -> None:
        result = compare_native_to_cpython(
            "clase C:\n"
            '    """class help documentation"""\n'
            "    funcion method(self):\n"
            '        """method help documentation"""\n'
            "        devolver None\n"
            "funcion target():\n"
            '    """function help documentation"""\n'
            "    devolver None\n"
            "help(C)\n"
            "help(C.method)\n"
            "help(target)\n"
            "imprimir('done', help())\n",
        )
        self.assertEqual(
            result.native.returncode,
            result.oracle.returncode,
            f"returncode mismatch\nnative stderr: {result.native.stderr!r}\noracle stderr: {result.oracle.stderr!r}",
        )
        self.assertEqual(result.native.stderr, result.oracle.stderr)
        native = result.native.stdout.decode("utf-8")
        oracle = result.oracle.stdout.decode("utf-8")
        for doc in (
            "class help documentation",
            "method help documentation",
            "function help documentation",
        ):
            self.assertIn(doc, native)
            self.assertIn(doc, oracle)
        self.assertTrue(native.startswith("Help on class C:\n"))
        self.assertTrue(oracle.startswith("Help on class C in module __main__:\n"))
        self.assertNotEqual(
            native,
            oracle,
            "help() intentionally provides concise native documentation; full pydoc parity is a known gap",
        )
        self.assertTrue(native.endswith("done None\n"))
        self.assertTrue(oracle.endswith("done None\n"))


if __name__ == "__main__":
    unittest.main()
