import os
import subprocess
import sys
import tempfile
from pathlib import Path

from native_linux import compile_native_linux, run_native_linux

ROOT = Path(__file__).resolve().parent.parent


def _run_cpython(py_code: str) -> str:
    proc = subprocess.run(
        [sys.executable, "-c", py_code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"CPython failed ({proc.returncode}): {proc.stderr}"
    return proc.stdout


def _run_ejecutar(piton_code: str) -> str:
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "prog.piton"
        src.write_text(piton_code, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-m", "piton", "ejecutar", str(src)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    assert proc.returncode == 0, (
        f"piton ejecutar failed ({proc.returncode})\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return proc.stdout


def _run_native(piton_code: str, use_cli_build: bool = False) -> str:
    with tempfile.TemporaryDirectory() as td:
        exe = Path(td) / "prog_bin"
        if use_cli_build:
            src = Path(td) / "prog.piton"
            src.write_text(piton_code, encoding="utf-8")
            build = subprocess.run(
                [sys.executable, "-m", "piton", "build", "--target", "linux", str(src), "-o", str(exe)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert build.returncode == 0, (
                f"piton build failed ({build.returncode})\n"
                f"stdout:\n{build.stdout}\nstderr:\n{build.stderr}"
            )
        else:
            compile_native_linux(piton_code, str(exe))
        proc = run_native_linux(
            exe,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    assert proc.returncode == 0, (
        f"native binary failed ({proc.returncode})\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return proc.stdout


def test_cpython_exact_seed0_range100_native_cli_and_ejecutar():
    expected = "[49, 97, 53, 5, 33, 65, 62, 51, 38, 61]"
    py_out = _run_cpython(
        "import random\n"
        "random.seed(0)\n"
        "print(random.sample(range(100), 10))\n"
    ).strip()
    assert py_out == expected

    piton_src = (
        "importar random\n"
        "random.seed(0)\n"
        "imprimir(random.sample(rango(100), 10))\n"
    )
    assert _run_ejecutar(piton_src).strip() == expected
    assert _run_native(piton_src, use_cli_build=False).strip() == expected
    assert _run_native(piton_src, use_cli_build=True).strip() == expected


def test_random_class_instances_seeding_parity():
    py_src = """
import random
r1 = random.Random(0)
r2 = random.Random(42)
r3 = random.Random(42)
print("r1_0:", r1.sample(range(100), 10))
print("r2_a:", r2.sample(range(50), 7))
print("r3_a:", r3.sample(range(50), 7))
r1.seed((1 << 45) | 987654321)
print("big:", r1.sample(range(200), 8))
r1.seed(-987654321)
print("neg:", r1.sample(range(200), 8))
r1.seed(0)
print("reseed:", r1.sample(range(100), 10))
"""
    piton_src = """
importar random
r1 = random.Random(0)
r2 = random.Random(42)
r3 = random.Random(42)
imprimir("r1_0:", r1.sample(rango(100), 10))
imprimir("r2_a:", r2.sample(rango(50), 7))
imprimir("r3_a:", r3.sample(rango(50), 7))
r1.seed((1 << 45) | 987654321)
imprimir("big:", r1.sample(rango(200), 8))
r1.seed(-987654321)
imprimir("neg:", r1.sample(rango(200), 8))
r1.seed(0)
imprimir("reseed:", r1.sample(rango(100), 10))
"""
    expected = _run_cpython(py_src)
    assert _run_ejecutar(piton_src) == expected
    assert _run_native(piton_src) == expected


def test_default_seed_and_system_random_smoke():
    piton_src = """
importar random
r = random.Random()
res1 = r.sample(rango(30), 6)
si longitud(res1) != 6:
    lanzar AssertionError("bad len 1")
si longitud(conjunto(res1)) != 6:
    lanzar AssertionError("dup 1")
para x en res1:
    si x < 0 o x >= 30:
        lanzar AssertionError("oob")
sr = random.SystemRandom()
res2 = sr.sample(["a", "b", "c", "d", "e"], 3)
si longitud(res2) != 3:
    lanzar AssertionError("bad len 2")
si longitud(conjunto(res2)) != 3:
    lanzar AssertionError("dup 2")
imprimir("OK")
"""
    assert _run_ejecutar(piton_src).strip() == "OK"
    assert _run_native(piton_src).strip() == "OK"


def test_setsize_crossover_and_large_range_parity():
    py_src = """
import random
cases = [
    (11, 21, 5),
    (11, 22, 5),
    (13, 85, 6),
    (13, 86, 6),
    (17, 277, 18),
    (17, 278, 18),
    (99, 10000000, 60),
]
for seed_val, n, k in cases:
    random.seed(seed_val)
    print(f"{seed_val}:{n}:{k}=", random.sample(range(n), k))
"""
    piton_src = """
importar random
cases = [(11, 21, 5), (11, 22, 5), (13, 85, 6), (13, 86, 6), (17, 277, 18), (17, 278, 18), (99, 10000000, 60)]
para item en cases:
    seed_val = item[0]
    n = item[1]
    k = item[2]
    random.seed(seed_val)
    imprimir(f"{seed_val}:{n}:{k}=", random.sample(rango(n), k))
"""
    expected = _run_cpython(py_src)
    assert _run_ejecutar(piton_src) == expected
    assert _run_native(piton_src) == expected


def test_sequence_types_from_import_and_non_mutation_parity():
    py_src = """
from random import sample, seed
seed(7)
print("range_step:", sample(range(2, 20, 3), 4))
print("range_neg:", sample(range(10, 0, -2), 3))
orig = ["a", "b", "c", "d", "e"]
print("list_str:", sample(orig, 3))
print("orig:", orig)
print("list_float:", sample([1.5, 2.5, 3.5, 4.5], 2))
print("list_mixed:", sample([1, "a", None, True], 4))
print("tuple:", sample(("x", "y", "z"), 2))
print("str:", sample("abcdef", 4))
print("k0_list:", sample([1, 2, 3], 0))
print("k0_empty:", sample([], 0))
print("k_eq_n:", sample([10, 20, 30], 3))
"""
    piton_src = """
desde random importar sample, seed
seed(7)
imprimir("range_step:", sample(rango(2, 20, 3), 4))
imprimir("range_neg:", sample(rango(10, 0, -2), 3))
orig = ["a", "b", "c", "d", "e"]
imprimir("list_str:", sample(orig, 3))
imprimir("orig:", orig)
imprimir("list_float:", sample([1.5, 2.5, 3.5, 4.5], 2))
imprimir("list_mixed:", sample([1, "a", Nada, Verdadero], 4))
imprimir("tuple:", sample(("x", "y", "z"), 2))
imprimir("str:", sample("abcdef", 4))
imprimir("k0_list:", sample([1, 2, 3], 0))
imprimir("k0_empty:", sample([], 0))
imprimir("k_eq_n:", sample([10, 20, 30], 3))
"""
    expected = _run_cpython(py_src)
    assert _run_ejecutar(piton_src) == expected
    assert _run_native(piton_src) == expected


def test_counts_parameter_parity():
    py_src = """
import random
random.seed(0)
print("kw:", random.sample(["red", "blue"], 5, counts=[4, 2]))
random.seed(0)
print("zero:", random.sample(["a", "b", "c", "d"], 4, counts=[0, 3, 0, 2]))
random.seed(0)
print("tuple:", random.sample(("x", "y", "z"), 3, counts=(2, 0, 1)))
random.seed(0)
print("range_cnt:", random.sample(["a", "b", "c"], 4, counts=range(1, 4)))
random.seed(0)
print("str_pop:", random.sample("abc", 4, counts=[2, 1, 2]))
random.seed(0)
print("range_pop:", random.sample(range(10, 13), 4, counts=[1, 2, 3]))
random.seed(0)
print("k0_cnt:", random.sample(["a", "b"], 0, counts=[1, 2]))
r = random.Random(0)
print("method:", r.sample(["red", "blue"], 5, counts=[4, 2]))
"""
    piton_src_kw_and_pos = """
importar random
random.seed(0)
imprimir("kw:", random.sample(["red", "blue"], 5, counts=[4, 2]))
random.seed(0)
imprimir("zero:", random.sample(["a", "b", "c", "d"], 4, counts=[0, 3, 0, 2]))
random.seed(0)
imprimir("tuple:", random.sample(("x", "y", "z"), 3, counts=(2, 0, 1)))
random.seed(0)
imprimir("range_cnt:", random.sample(["a", "b", "c"], 4, counts=rango(1, 4)))
random.seed(0)
imprimir("str_pop:", random.sample("abc", 4, counts=[2, 1, 2]))
random.seed(0)
imprimir("range_pop:", random.sample(rango(10, 13), 4, counts=[1, 2, 3]))
random.seed(0)
imprimir("k0_cnt:", random.sample(["a", "b"], 0, counts=[1, 2]))
r = random.Random(0)
imprimir("method:", r.sample(["red", "blue"], 5, counts=[4, 2]))
"""
    expected = _run_cpython(py_src)
    assert _run_ejecutar(piton_src_kw_and_pos) == expected
    assert _run_native(piton_src_kw_and_pos.replace('r.sample(["red", "blue"], 5, counts=[4, 2])', 'r.sample(["red", "blue"], 5, [4, 2])')) == expected

    piton_pos_mod = (
        "importar random\n"
        "random.seed(0)\n"
        'imprimir(random.sample(["red", "blue"], 5, [4, 2]))\n'
    )
    assert _run_native(piton_pos_mod).strip() == "['red', 'blue', 'red', 'red', 'red']"


def test_error_parity_native_and_ejecutar():
    py_src = """
import random

def check(label, fn):
    try:
        fn()
        print(label, "NO_ERROR")
    except Exception as e:
        print(label, type(e).__name__, str(e))

check("k_neg", lambda: random.sample([1, 2, 3], -1))
check("k_gt_n", lambda: random.sample([1, 2, 3], 4))
check("k_gt_total", lambda: random.sample(["a", "b"], 4, counts=[1, 2]))
check("counts_wrong_len", lambda: random.sample(["a", "b"], 1, counts=[1, 2, 3]))
check("counts_zero_total", lambda: random.sample(["a", "b"], 1, counts=[0, 0]))
check("counts_neg_total", lambda: random.sample(["a", "b"], 1, counts=[1, -2]))
check("counts_float", lambda: random.sample(["a", "b"], 1, counts=[1.5, 2]))
check("counts_str", lambda: random.sample(["a", "b"], 1, counts=["x", "y"]))
check("empty_counts_k0", lambda: random.sample([], 0, counts=[]))
check("pop_set", lambda: random.sample({1, 2}, 1))
check("pop_dict", lambda: random.sample({"a": 1}, 1))
check("pop_int", lambda: random.sample(42, 1))
check("pop_none", lambda: random.sample(None, 1))
"""
    piton_src = """
importar random

funcion check(label, mode):
    intentar:
        si mode == 0:
            random.sample([1, 2, 3], -1)
        sino_si mode == 1:
            random.sample([1, 2, 3], 4)
        sino_si mode == 2:
            random.sample(["a", "b"], 4, counts=[1, 2])
        sino_si mode == 3:
            random.sample(["a", "b"], 1, counts=[1, 2, 3])
        sino_si mode == 4:
            random.sample(["a", "b"], 1, counts=[0, 0])
        sino_si mode == 5:
            random.sample(["a", "b"], 1, counts=[1, -2])
        sino_si mode == 6:
            random.sample(["a", "b"], 1, counts=[1.5, 2])
        sino_si mode == 7:
            random.sample(["a", "b"], 1, counts=["x", "y"])
        sino_si mode == 8:
            random.sample([], 0, counts=[])
        sino_si mode == 9:
            random.sample({1, 2}, 1)
        sino_si mode == 10:
            random.sample({"a": 1}, 1)
        sino_si mode == 11:
            random.sample(42, 1)
        sino:
            random.sample(Nada, 1)
        imprimir(label, "NO_ERROR")
    excepto Exception como e:
        imprimir(label, tipo(e).__name__, str(e))

check("k_neg", 0)
check("k_gt_n", 1)
check("k_gt_total", 2)
check("counts_wrong_len", 3)
check("counts_zero_total", 4)
check("counts_neg_total", 5)
check("counts_float", 6)
check("counts_str", 7)
check("empty_counts_k0", 8)
check("pop_set", 9)
check("pop_dict", 10)
check("pop_int", 11)
check("pop_none", 12)
"""
    expected = _run_cpython(py_src)
    assert _run_ejecutar(piton_src) == expected
    assert _run_native(piton_src) == expected
