"""ADVERSARIAL decimal + round/sum on objects — paridad byte a byte con CPython 3.12."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from native_linux import compile_native_linux, run_native_linux  # noqa: E402
from tools.parity_corpus_v4 import python_to_piton  # noqa: E402

TIMEOUT = 60


def _env() -> dict[str, str]:
    env = dict(os.environ)
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


def _run_cpython(codigo_python: str) -> tuple[int, bytes, bytes]:
    proc = subprocess.run(
        [sys.executable, "-c", codigo_python],
        capture_output=True,
        timeout=TIMEOUT,
        env=_env(),
    )
    return proc.returncode, proc.stdout, proc.stderr


def _run_native(piton_src: str) -> tuple[int, bytes, bytes]:
    with tempfile.TemporaryDirectory() as d:
        exe = Path(d) / "prog"
        compile_native_linux(piton_src, exe)
        proc = run_native_linux(exe, capture_output=True, timeout=TIMEOUT, env=_env())
    return proc.returncode, proc.stdout, proc.stderr


def _run_ejecutar(piton_src: str) -> tuple[int, bytes, bytes]:
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "prog.piton"
        src.write_text(piton_src, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-m", "piton", "ejecutar", str(src)],
            cwd=REPO,
            capture_output=True,
            timeout=TIMEOUT,
            env=_env(),
        )
    return proc.returncode, proc.stdout, proc.stderr


_CASES = [
    (
        "decimal_add_mul_repr",
        """from decimal import Decimal
print(Decimal("0.1") + Decimal("0.2"))
print(Decimal("0.70") * Decimal("1.05"))
print(repr(Decimal("0.70") * Decimal("1.05")))
print(Decimal("-0.00") + Decimal("0"))
print(Decimal(0))
print(Decimal("1") + 2, 1 + Decimal("2"))
""",
    ),
    (
        "decimal_div_floordiv_mod_sci",
        """from decimal import Decimal
print(Decimal("10") / Decimal("3"))
print(Decimal("1") / Decimal("3"))
print(Decimal("2") / Decimal("3"))
print(Decimal("10") / Decimal("4"))
print(Decimal("1") / Decimal("10000000"))
print(Decimal("12000.0") * Decimal("1"))
print(Decimal("-10") // Decimal("3"))
print(Decimal("-10") % Decimal("3"))
""",
    ),
    (
        "decimal_quantize_rounding_and_prec",
        """from decimal import Decimal, getcontext, ROUND_HALF_UP, ROUND_FLOOR
print(Decimal("7.325").quantize(Decimal(".01"), rounding=ROUND_HALF_UP))
print(Decimal("7.325").quantize(Decimal("1."), rounding=ROUND_HALF_UP))
print(Decimal("7.325").quantize(Decimal(".01"), ROUND_FLOOR))
getcontext().prec = 6
print(Decimal("1") / Decimal("7"))
""",
    ),
    (
        "decimal_round_builtin_and_dunder",
        """from decimal import Decimal
print(round(Decimal("2.675"), 2))
print(round(Decimal("2.675")))
print(round(Decimal("2.5")))
print(round(Decimal("3.5")))
print(round(Decimal("2.675"), 2) + Decimal("0.01"))
""",
    ),
    (
        "decimal_sum_and_comparisons",
        """from decimal import Decimal
s = sum([Decimal("0.1"), Decimal("0.2"), Decimal("0.3")])
print(s)
print(sum([Decimal("0.1")] * 10) == Decimal("1.0"))
print(sum([Decimal("0.1"), Decimal("0.2")], Decimal("1.0")))
print(Decimal("1.0") < Decimal("2.0"), Decimal("1.0") != Decimal("2.0"))
""",
    ),
]


@pytest.mark.parametrize("name,code", _CASES, ids=[c[0] for c in _CASES])
def test_decimal_native_and_interp_parity(name: str, code: str) -> None:
    rc_c, out_c, err_c = _run_cpython(code)
    assert rc_c == 0, f"CPython oracle {name} failed: {err_c!r}"
    piton_src = python_to_piton(code)

    rc_e, out_e, err_e = _run_ejecutar(piton_src)
    assert (rc_e, out_e, err_e) == (rc_c, out_c, err_c), (
        f"[ejecutar:{name}] mismatch:\n"
        f"  piton: rc={rc_e} out={out_e!r} err={err_e!r}\n"
        f"  cpy  : rc={rc_c} out={out_c!r} err={err_c!r}"
    )

    rc_n, out_n, err_n = _run_native(piton_src)
    assert (rc_n, out_n, err_n) == (rc_c, out_c, err_c), (
        f"[native:{name}] mismatch:\n"
        f"  piton: rc={rc_n} out={out_n!r} err={err_n!r}\n"
        f"  cpy  : rc={rc_c} out={out_c!r} err={err_c!r}"
    )
