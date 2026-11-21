"""Differential parity tests for wave 23 shelve and weakref C codegen and linker fixes.

Validates:
1. Method alias arity mismatch adaptation (C function wrappers for __len__, __getitem__,
   __setitem__, keys, etc. aliased to methods with different arities or *args).
2. Unbound _weakref.ref.__eq__ and __ne__ runtime symbols and equality comparison parity.
3. Fail-closed behavior for unsupported operations and invalid invocation arity.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

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


class ShelveWeakrefCgenLinkerParity(unittest.TestCase):
    def test_weakref_unbound_ref_equality_same_target(self):
        _assert_matches(
            self,
            "importar _weakref\n"
            "clase Target:\n"
            "    funcion __init__(self, val):\n"
            "        self.val = val\n"
            "t1 = Target(42)\n"
            "r1 = _weakref.ref(t1)\n"
            "r2 = _weakref.ref(t1)\n"
            "print(_weakref.ref.__eq__(r1, r2))\n"
            "print(_weakref.ref.__ne__(r1, r2))\n",
        )

    def test_weakref_unbound_ref_equality_different_target(self):
        _assert_matches(
            self,
            "importar _weakref\n"
            "clase Target:\n"
            "    funcion __init__(self, val):\n"
            "        self.val = val\n"
            "t1 = Target(42)\n"
            "t2 = Target(43)\n"
            "r1 = _weakref.ref(t1)\n"
            "r2 = _weakref.ref(t2)\n"
            "print(_weakref.ref.__eq__(r1, r2))\n"
            "print(_weakref.ref.__ne__(r1, r2))\n",
        )

    def test_weakref_unbound_ref_equality_not_implemented_types(self):
        _assert_matches(
            self,
            "importar _weakref\n"
            "clase Target:\n"
            "    funcion __init__(self, val):\n"
            "        self.val = val\n"
            "t = Target(99)\n"
            "r = _weakref.ref(t)\n"
            "print(_weakref.ref.__eq__(r, 123))\n"
            "print(_weakref.ref.__ne__(r, 123))\n"
            "print(_weakref.ref.__eq__(r, Nada))\n"
            "print(_weakref.ref.__ne__(r, Nada))\n",
        )

    def test_weakref_unbound_ref_equality_arity_mismatch_fails_closed(self):
        _assert_matches(
            self,
            "importar _weakref\n"
            "clase Target:\n"
            "    funcion __init__(self, val):\n"
            "        self.val = val\n"
            "t = Target(1)\n"
            "r = _weakref.ref(t)\n"
            "intentar:\n"
            "    _weakref.ref.__eq__(r)\n"
            "excepto TypeError:\n"
            "    print('FEW_ARGS_EXC')\n"
            "intentar:\n"
            "    _weakref.ref.__eq__(r, r, r)\n"
            "excepto TypeError:\n"
            "    print('MANY_ARGS_EXC')\n",
        )

    def test_method_alias_arity_adaptation_varargs(self):
        _assert_matches(
            self,
            "clase ClosedLike:\n"
            "    funcion handler(self, *args):\n"
            "        devolver 100\n"
            "    __len__ = handler\n"
            "    __getitem__ = handler\n"
            "    keys = handler\n"
            "c = ClosedLike()\n"
            "print(len(c))\n"
            "print(c['key'])\n"
            "print(c.keys())\n",
        )

    def test_method_alias_setitem_packs_varargs(self):
        _assert_matches(
            self,
            "clase Store:\n"
            "    funcion handler(self, *args):\n"
            "        self.recorded = args\n"
            "    __setitem__ = handler\n"
            "s = Store()\n"
            "s['key'] = 'val'\n"
            "print(s.recorded)\n",
        )

    def test_method_alias_with_defaults(self):
        _assert_matches(
            self,
            "clase Config:\n"
            "    funcion get_value(self, default=42):\n"
            "        devolver default\n"
            "    __len__ = get_value\n"
            "cfg = Config()\n"
            "print(len(cfg))\n"
            "print(cfg.get_value(99))\n",
        )

    def test_method_alias_getitem_and_len_multiple_aliases(self):
        _assert_matches(
            self,
            "clase MultiAlias:\n"
            "    funcion handler(self, *args):\n"
            "        devolver len(args)\n"
            "    __len__ = handler\n"
            "    __iter__ = handler\n"
            "    keys = handler\n"
            "m = MultiAlias()\n"
            "print(len(m))\n"
            "print(m.keys())\n",
        )


if __name__ == "__main__":
    unittest.main()
