"""Falsificación adversarial de ``random`` contra CPython 3.12.3.

Las expectativas se verificaron el 2026-11-13 en esta máquina con
``python3 --version`` (``Python 3.12.3``) y con los comandos ``python3 -c``
citados debajo.  Ninguna expectativa fue copiada de PITON.
"""
from __future__ import annotations

import os
import subprocess
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
    oracle = _run(["python3", "-c", oracle_source])
    assert oracle.returncode == 0, (
        f"el propio oráculo falló para {stem}: rc={oracle.returncode}\n"
        f"stdout={oracle.stdout!r}\nstderr={oracle.stderr!r}"
    )
    assert oracle.stdout == expected, (
        f"cambió la expectativa CPython de {stem}: {oracle.stdout!r} != {expected!r}"
    )

    piton_file = tmp_path / f"{stem}.piton"
    piton_file.write_text(piton_source, encoding="utf-8")
    piton = _run(["python3", "-m", "piton", "ejecutar", str(piton_file)])
    assert piton.returncode == 0, (
        f"PITON no ejecutó {stem}: rc={piton.returncode}\n"
        f"stdout={piton.stdout!r}\nstderr={piton.stderr!r}"
    )
    assert piton.stdout == oracle.stdout, (
        f"PITON diverge de CPython 3.12.3 en {stem}\n"
        f"PITON={piton.stdout!r}\nCPython={oracle.stdout!r}\n"
        f"stderr PITON={piton.stderr!r}"
    )


def test_random_seed_vector_is_exact(tmp_path: Path) -> None:
    """CPython 3.12.3: ``python3 -c 'import random; random.seed(0); ...'``."""
    oracle_source = """\
import random
random.seed(0)
print([random.random() for _ in range(3)])
"""
    piton_source = """\
importar random
random.seed(0)
imprimir([random.random() para _ en rango(3)])
"""
    expected = "[0.8444218515250481, 0.7579544029403025, 0.420571580830845]\n"
    _assert_oracle_and_piton(
        tmp_path, "seed_vector", oracle_source, piton_source, expected
    )


def test_random_scalar_primitives_use_fresh_seeded_state(tmp_path: Path) -> None:
    """CPython 3.12.3: randint=7, bits32=3626764237, bits64=7106521602475165645,
    randrange=49; cada operación se midió después de ``seed(0)`` independiente.
    """
    oracle_source = """\
import random
random.seed(0); print(random.randint(1, 10))
random.seed(0); print(random.getrandbits(32))
random.seed(0); print(random.getrandbits(64))
random.seed(0); print(random.randrange(100))
"""
    piton_source = """\
importar random
random.seed(0); imprimir(random.randint(1, 10))
random.seed(0); imprimir(random.getrandbits(32))
random.seed(0); imprimir(random.getrandbits(64))
random.seed(0); imprimir(random.randrange(100))
"""
    expected = "7\n3626764237\n7106521602475165645\n49\n"
    _assert_oracle_and_piton(
        tmp_path, "scalar_primitives", oracle_source, piton_source, expected
    )


def test_random_sample_and_randbytes_are_exact(tmp_path: Path) -> None:
    """CPython 3.12.3: sample(range(100), 10) y randbytes(16) verificados con
    ``python3 -c``; ambos parten de ``seed(0)`` en una instancia nueva.
    """
    oracle_source = """\
import random
random.seed(0); print(random.sample(range(100), 10))
random.seed(0); print(random.randbytes(16).hex())
"""
    piton_source = """\
importar random
random.seed(0); imprimir(random.sample(rango(100), 10))
random.seed(0); imprimir(random.randbytes(16).hex())
"""
    expected = (
        "[49, 97, 53, 5, 33, 65, 62, 51, 38, 61]\n"
        "cd072cd8be6f9f62ac4c09c28206e7e3\n"
    )
    _assert_oracle_and_piton(
        tmp_path, "sample_randbytes", oracle_source, piton_source, expected
    )


def test_random_uniform_choice_and_shuffle_are_exact(tmp_path: Path) -> None:
    """CPython 3.12.3: uniform=1.8444218515250481, choice='delta', y
    shuffle(range(10))=[7, 8, 1, 5, 3, 4, 2, 0, 9, 6].
    """
    oracle_source = """\
import random
random.seed(0); print(repr(random.uniform(1.0, 2.0)))
random.seed(0); print(random.choice(["alfa", "beta", "gamma", "delta"]))
random.seed(0)
values = list(range(10))
random.shuffle(values)
print(values)
"""
    piton_source = """\
importar random
random.seed(0); imprimir(repr(random.uniform(1.0, 2.0)))
random.seed(0); imprimir(random.choice(["alfa", "beta", "gamma", "delta"]))
random.seed(0)
valores = list(rango(10))
random.shuffle(valores)
imprimir(valores)
"""
    expected = "1.8444218515250481\ndelta\n[7, 8, 1, 5, 3, 4, 2, 0, 9, 6]\n"
    _assert_oracle_and_piton(
        tmp_path, "uniform_choice_shuffle", oracle_source, piton_source, expected
    )


def test_two_random_instances_do_not_share_a_global_counter(tmp_path: Path) -> None:
    """Dos ``Random(12345)`` independientes deben conservar estados separados.

    CPython 3.12.3 imprime dos veces 0.41661987254534116 aun al intercalar
    una lectura de cada instancia.
    """
    oracle_source = """\
import random
first = random.Random(12345)
second = random.Random(12345)
a = first.random()
b = second.random()
print(a == b)
print(a)
print(b)
"""
    piton_source = """\
importar random
primero = random.Random(12345)
segundo = random.Random(12345)
a = primero.random()
b = segundo.random()
imprimir(a == b)
imprimir(a)
imprimir(b)
"""
    expected = "True\n0.41661987254534116\n0.41661987254534116\n"
    _assert_oracle_and_piton(
        tmp_path, "independent_instances", oracle_source, piton_source, expected
    )
