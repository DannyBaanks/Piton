"""Differential native coverage for the bundled pure PITON re module."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from piton.native_differential import compare_native_to_cpython  # noqa: E402


def exigir_paridad(codigo: str) -> None:
    resultado = compare_native_to_cpython(codigo)
    assert resultado.native.returncode == resultado.oracle.returncode, (
        f"returncode mismatch for {codigo!r}\n"
        f"native stderr: {resultado.native.stderr!r}\n"
        f"oracle stderr: {resultado.oracle.stderr!r}"
    )
    assert resultado.native.stdout == resultado.oracle.stdout, (
        f"stdout mismatch for {codigo!r}\n"
        f"native stdout: {resultado.native.stdout!r}\n"
        f"oracle stdout: {resultado.oracle.stdout!r}\n"
        f"native stderr: {resultado.native.stderr!r}"
    )


def test_findall_and_sub_corpus_examples():
    exigir_paridad(
        "importar re\n"
        "imprimir(re.findall(r'\\bf[a-z]*', 'which foot or hand fell fastest'))\n"
        "imprimir(re.sub(r'(\\b[a-z]+) \\1', r'\\1', 'cat in the the hat'))\n"
    )


def test_search_match_and_capture_methods():
    exigir_paridad(
        "importar re\n"
        "found = re.search(r'([a-z]+)-(\\d+)', 'xx-abc-42!')\n"
        "imprimir(found.group(), found.group(1), found.group(2))\n"
        "imprimir(found.groups(), found.span(), found.start(2), found.end(2))\n"
        "imprimir(re.match(r'(ab)+', 'abab!').group(0))\n"
        "imprimir(re.match(r'abc', 'xabc') es Nada)\n"
        "imprimir(re.search(r'z+', 'abc') es Nada)\n"
    )


def test_greedy_backtracking_classes_and_quantifiers():
    exigir_paridad(
        "importar re\n"
        "imprimir(re.findall(r'([a-z]+)(\\d?)', 'ab12 cd3'))\n"
        "imprimir(re.findall(r'[a-c]+', 'abxccc'))\n"
        "imprimir(re.findall(r'[^0-9]+', 'a12bc3'))\n"
        "imprimir(re.match(r'a.*b', 'a1b2b').group())\n"
    )


def test_anchors_character_classes_and_flags():
    exigir_paridad(
        "importar re\n"
        "imprimir(re.findall(r'^\\w+\\s\\d+$', 'name 42'))\n"
        "imprimir(re.findall(r'^foo$', 'x\\nfoo\\ny', re.M))\n"
        "imprimir(re.findall(r'a.b', 'a\\nb'))\n"
        "imprimir(re.findall(r'a.b', 'a\\nb', re.S))\n"
        "imprimir(re.findall(r'abc', 'ABC abc', re.I))\n"
    )


def test_sub_backreferences_and_count():
    exigir_paridad(
        "importar re\n"
        "imprimir(re.sub(r'(\\w+)-(\\d+)', r'\\2:\\1', 'ab-12 cd-3'))\n"
        "imprimir(re.sub(r'(\\w+) \\1', r'\\g<1>', 'the the cat cat', count=1))\n"
        "imprimir(re.sub(r'x', 'y', 'x-x-x', count=2))\n"
    )


def test_empty_matches_advance_like_cpython():
    exigir_paridad(
        "importar re\n"
        "imprimir(re.findall(r'x*', 'ab'))\n"
        "imprimir(re.sub(r'x*', '-', 'ab'))\n"
        "imprimir(re.findall(r'()', 'ab'))\n"
    )


def test_adv_re_v1_inventory():
    casos = [
        "findall_and_sub_corpus_examples",
        "search_match_and_capture_methods",
        "greedy_backtracking_classes_and_quantifiers",
        "anchors_character_classes_and_flags",
        "sub_backreferences_and_count",
        "empty_matches_advance_like_cpython",
    ]
    assert len(casos) == 6
