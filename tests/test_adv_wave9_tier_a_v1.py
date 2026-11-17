"""Adversarial parity checks for Tier A native build gaps fixed in wave 9."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from piton.native_differential import compare_native_to_cpython
from piton.linux_x86 import LinuxCEmitter, _gcc_compile
from piton.lower import lower_cst_to_hir
from piton.mir import lower_hir_to_mir
from piton.parser import parse


def _assert_exact_parity(source: str) -> None:
    result = compare_native_to_cpython(source)
    assert result.native == result.oracle, (
        f"native={result.native!r}\nCPython={result.oracle!r}"
    )


def test_dynamic_slot_bitwise_operators_match_int_and_bool_semantics() -> None:
    _assert_exact_parity(
        "funcion bitwise(values):\n"
        "    para value en values:\n"
        "        imprimir(value << 2, value >> 1, value & 3, value | 8, value ^ 1)\n"
        "        imprimir(value & Verdadero, value | Falso, value ^ Verdadero)\n"
        "bitwise([-9, 0, 5, Verdadero, Falso])\n"
    )


def test_open_accepts_string_paths_carried_by_dynamic_slots(tmp_path: Path) -> None:
    target = tmp_path / "tier-a-slot-path.txt"
    target.write_text("dynamic path read\nsecond line\n", encoding="utf-8")
    path_literal = json.dumps(str(target))
    bytes_path_literal = repr(os.fsencode(target))

    _assert_exact_parity(
        "funcion read_paths(paths):\n"
        "    para path en paths:\n"
        "        file = abrir(path)\n"
        "        imprimir(file.read())\n"
        f"read_paths([{path_literal}, {bytes_path_literal}])\n"
    )


def test_getopt_error_str_repr_and_exception_fields() -> None:
    _assert_exact_parity(
        "importar getopt\n"
        "error = getopt.GetoptError('invalid option', '-x')\n"
        "imprimir(error)\n"
        "imprimir(repr(error))\n"
        "imprimir(error.args)\n"
        "imprimir(error.msg, error.opt)\n"
        "defaulted = getopt.GetoptError('missing option')\n"
        "imprimir(defaulted.args, repr(defaulted))\n"
    )


def test_tier_a_getopt_error_alias_emits_bundled_string_hooks(tmp_path: Path) -> None:
    source = (
        "importar getopt\n"
        "intentar:\n"
        "    lanzar getopt.error('bad option', '-x')\n"
        "excepto getopt.error como message:\n"
        "    imprimir(message)\n"
    )
    mir = lower_hir_to_mir(lower_cst_to_hir(parse(source)))
    c_source = LinuxCEmitter().emit(mir)
    c_file = tmp_path / "program.c"
    c_file.write_text(c_source, encoding="utf-8")
    _gcc_compile(c_file, tmp_path / "program")
