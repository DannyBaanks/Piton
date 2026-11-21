"""Wave 23 — Tier B stdlib + Tier A fractions/trace/functools parity.

Two constructs are genuinely supported here and get differential coverage:

* PCT_NONE_DYN_V1 — ``%f``/``%e``/``%g`` (and the other numeric conversions)
  with an operand statically typed ``none`` no longer fail at build time.
  CPython compiles the format string and only raises ``TypeError: must be real
  number, not NoneType`` if that branch actually executes.  The operand is
  routed through ``piton_percent_slot_convert`` so the error is raised at
  runtime and routed via ``_emit_exc_check`` (guarded dead branches compile).
  This unblocks Tier A ``trace.py``.

* LIST_HASH_DUNDER_V1 — a ``list`` subclass may override ``__hash__``;
  ``hash(obj)`` dispatches to it through the regular class MRO
  (``HASH_DUNDER_V1``).  This closes the ``functools._HashedSeq`` gap.

The remaining blockers of the three named stdlib files are NOT stubbed here;
they are documented in the final report, not hidden behind a fake green.
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
        f"returncode mismatch\nnative stderr: {result.native.stderr!r}\n"
        f"oracle stderr: {result.oracle.stderr!r}",
    )
    testcase.assertEqual(
        result.native.stdout,
        result.oracle.stdout,
        f"stdout mismatch\nnative stderr: {result.native.stderr!r}\n"
        f"oracle stderr: {result.oracle.stderr!r}",
    )


class PercentNoneDynamic(unittest.TestCase):
    """trace.py: statically-``none`` numeric %-conversion operands."""

    def test_percent_f_none_in_dead_branch_matches_cpython(self):
        # The exact shape from trace.py: an untaken branch that formats None.
        _assert_matches(
            self,
            "funcion fmt(flag):\n"
            "    x = None\n"
            "    si flag:\n"
            '        devolver "%.1f" % x\n'
            '    devolver "0.0"\n'
            "imprimir(fmt(Falso))\n",
        )

    def test_percent_f_e_g_none_executed_raise_caught_typeerror(self):
        # If the branch IS taken, CPython raises TypeError at runtime; the
        # native side must raise the same exception into the same handler.
        _assert_matches(
            self,
            "intentar:\n"
            '    "%.1f" % None\n'
            "excepto TypeError:\n"
            '    imprimir("caught_f")\n'
            "intentar:\n"
            '    "%.2e" % None\n'
            "excepto TypeError:\n"
            '    imprimir("caught_e")\n'
            "intentar:\n"
            '    "%.3g" % None\n'
            "excepto TypeError:\n"
            '    imprimir("caught_g")\n',
        )

    def test_percent_other_numeric_specs_with_none(self):
        # d/x/o/c travel the same runtime conversion helper.
        _assert_matches(
            self,
            "intentar:\n"
            '    "%d" % None\n'
            "excepto TypeError:\n"
            '    imprimir("caught_d")\n'
            "intentar:\n"
            '    "%x" % None\n'
            "excepto TypeError:\n"
            '    imprimir("caught_x")\n',
        )

    def test_percent_f_none_uncaught_matches_cpython_returncode(self):
        _assert_matches(self, '"%.1f" % None\n')

    def test_percent_s_none_still_formats_as_None(self):
        # Edge: %s with None stays the string "None" (unchanged path).
        _assert_matches(self, 'imprimir("%s|%r" % (None, None))\n')

    def test_percent_f_with_string_still_fails_closed(self):
        # Negative / fail-closed: CPython would raise at runtime, but a
        # statically-str operand for %f is outside the supported subset and
        # must keep failing closed with a clear build error.
        with pytest.raises(NativeBuildError, match=r"Linux %f requires int or float, not str"):
            compare_native_to_cpython('imprimir("%.1f" % "x")\n')


class ListSubclassHashOverride(unittest.TestCase):
    """functools.py: ``_HashedSeq(list)`` overriding ``__hash__``."""

    def test_list_subclass_hash_override_matches_cpython(self):
        _assert_matches(
            self,
            "clase H(list):\n"
            "    funcion __hash__(self):\n"
            "        devolver 42\n"
            "imprimir(hash(H()))\n",
        )

    def test_list_subclass_hash_from_instance_attribute(self):
        # Mirrors functools._HashedSeq: __init__ stores a value, __hash__
        # returns it.  A literal is used (hash(int) is a pre-existing
        # PITON/CPython hash-value divergence and is not under test here).
        _assert_matches(
            self,
            "clase HashedSeq(list):\n"
            "    funcion __init__(self, v):\n"
            "        self.hashvalue = v\n"
            "    funcion __hash__(self):\n"
            "        devolver self.hashvalue\n"
            "h = HashedSeq(17)\n"
            "imprimir(hash(h))\n",
        )

    def test_dict_subclass_hash_override_also_dispatches(self):
        _assert_matches(
            self,
            "clase HD(dict):\n"
            "    funcion __hash__(self):\n"
            "        devolver 7\n"
            "imprimir(hash(HD()))\n",
        )

    def test_list_subclass_repr_override_still_fails_closed(self):
        # Negative / fail-closed: only __hash__ was added to the list-subclass
        # allowed set; other dunder overrides keep their clear build error.
        with pytest.raises(
            NativeBuildError,
            match=r"native list-subclass overriding '__repr__' is not supported",
        ):
            compare_native_to_cpython(
                "clase L(list):\n"
                "    funcion __repr__(self):\n"
                '        devolver "x"\n'
            )

    def test_list_subclass_setitem_override_still_fails_closed(self):
        with pytest.raises(
            NativeBuildError,
            match=r"native list-subclass overriding '__setitem__' is not supported",
        ):
            compare_native_to_cpython(
                "clase L(list):\n"
                "    funcion __setitem__(self, k, v):\n"
                "        super().__setitem__(k, v)\n"
            )


if __name__ == "__main__":
    unittest.main()
