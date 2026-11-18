"""Falsificación adversarial de ``decimal`` contra CPython 3.12.3.

Expectativas verificadas el 2026-11-13 con ``python3 --version``
(``Python 3.12.3``) y los comandos ``python3 -c`` indicados en cada caso.
No se copió ninguna salida de PITON.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "LC_ALL": "C.UTF-8",
            "LANG": "C.UTF-8",
        }
    )
    return env


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        env=_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )


def _assert_oracle_and_piton(
    tmp_path: Path,
    stem: str,
    oracle_source: str,
    piton_source: str,
    expected: str,
) -> None:
    oracle = _run([sys.executable, "-c", oracle_source])
    assert oracle.returncode == 0, (
        f"el propio oráculo falló para {stem}: rc={oracle.returncode}\n"
        f"stdout={oracle.stdout!r}\nstderr={oracle.stderr!r}"
    )
    assert oracle.stdout == expected, (
        f"cambió la expectativa CPython de {stem}: {oracle.stdout!r} != {expected!r}"
    )

    piton_file = tmp_path / f"{stem}.piton"
    piton_file.write_text(piton_source, encoding="utf-8")
    piton = _run([sys.executable, "-m", "piton", "ejecutar", str(piton_file)])
    assert piton.returncode == 0, (
        f"PITON no ejecutó {stem}: rc={piton.returncode}\n"
        f"stdout={piton.stdout!r}\nstderr={piton.stderr!r}"
    )
    assert piton.stdout == oracle.stdout, (
        f"PITON diverge de CPython 3.12.3 en {stem}\n"
        f"PITON={piton.stdout!r}\nCPython={oracle.stdout!r}\n"
        f"stderr PITON={piton.stderr!r}"
    )


def test_decimal_product_and_half_even_rounding(tmp_path: Path) -> None:
    """CPython 3.12.3: 0.70*1.05 conserva escala 0.7350 y ``round(..., 2)``
    da 0.74 (ROUND_HALF_EVEN).  Verificado con ``python3 -c``.
    """
    oracle_source = """\
from decimal import Decimal
product = Decimal("0.70") * Decimal("1.05")
print(product)
print(round(product, 2))
"""
    piton_source = """\
desde decimal importar Decimal
producto = Decimal("0.70") * Decimal("1.05")
imprimir(producto)
imprimir(round(producto, 2))
"""
    expected = "0.7350\n0.74\n"
    _assert_oracle_and_piton(
        tmp_path, "product_round", oracle_source, piton_source, expected
    )


def test_decimal_remainder_and_exact_decimal_sum(tmp_path: Path) -> None:
    """CPython 3.12.3: ``Decimal('1.00') % Decimal('.10')`` es 0.00 y la suma
    exacta de diez Decimal('0.1') es 1.0.
    """
    oracle_source = """\
from decimal import Decimal
print(Decimal("1.00") % Decimal(".10"))
print(sum([Decimal("0.1")] * 10))
"""
    piton_source = """\
desde decimal importar Decimal
imprimir(Decimal("1.00") % Decimal(".10"))
imprimir(sum([Decimal("0.1")] * 10))
"""
    expected = "0.00\n1.0\n"
    _assert_oracle_and_piton(
        tmp_path, "remainder_sum", oracle_source, piton_source, expected
    )


def test_decimal_context_precision_36_is_exact(tmp_path: Path) -> None:
    """CPython 3.12.3 con ``getcontext().prec=36``: 1/7 produce exactamente
    seis bloques de seis dígitos ``142857``.
    """
    oracle_source = """\
from decimal import Decimal, getcontext
context = getcontext()
context.prec = 36
print(Decimal(1) / Decimal(7))
"""
    piton_source = """\
desde decimal importar Decimal, getcontext
contexto = getcontext()
contexto.prec = 36
imprimir(Decimal(1) / Decimal(7))
"""
    expected = "0.142857142857142857142857142857142857\n"
    _assert_oracle_and_piton(
        tmp_path, "precision_36", oracle_source, piton_source, expected
    )


def test_decimal_comparisons_with_integers(tmp_path: Path) -> None:
    """CPython 3.12.3: Decimal('2.5')>2 es True, ==2 es False, y
    Decimal('2')==2 es True.
    """
    oracle_source = """\
from decimal import Decimal
print(Decimal("2.5") > 2)
print(Decimal("2.5") == 2)
print(Decimal("2") == 2)
"""
    piton_source = """\
desde decimal importar Decimal
imprimir(Decimal("2.5") > 2)
imprimir(Decimal("2.5") == 2)
imprimir(Decimal("2") == 2)
"""
    expected = "True\nFalse\nTrue\n"
    _assert_oracle_and_piton(
        tmp_path, "int_comparisons", oracle_source, piton_source, expected
    )


def test_decimal_arithmetic_does_not_go_through_binary_float(tmp_path: Path) -> None:
    """La aritmética Decimal debe ser decimal, no IEEE float.

    En CPython 3.12.3, Decimal('0.1')+Decimal('0.2') es exactamente 0.3 y
    Decimal('0.1')*Decimal('0.2') es exactamente 0.02.  Una ruta que pasara
    por float fallaría ambas igualdades (float da 0.30000000000000004 y
    0.020000000000000004).  La desigualdad 0.1+0.2 != 0.3 corresponde al
    oráculo *float*, no a Decimal.
    """
    oracle_source = """\
from decimal import Decimal
total = Decimal("0.1") + Decimal("0.2")
product = Decimal("0.1") * Decimal("0.2")
print(total)
print(total == Decimal("0.3"))
print(product)
print(product == Decimal("0.02"))
print(total != Decimal("0.3"))
"""
    piton_source = """\
desde decimal importar Decimal
total = Decimal("0.1") + Decimal("0.2")
producto = Decimal("0.1") * Decimal("0.2")
imprimir(total)
imprimir(total == Decimal("0.3"))
imprimir(producto)
imprimir(producto == Decimal("0.02"))
imprimir(total != Decimal("0.3"))
"""
    expected = "0.3\nTrue\n0.02\nTrue\nFalse\n"
    _assert_oracle_and_piton(
        tmp_path, "not_binary_float", oracle_source, piton_source, expected
    )
