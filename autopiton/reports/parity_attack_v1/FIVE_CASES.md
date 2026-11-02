# FIVE_CASES — Resolución y Generalización de los Cinco Contraejemplos Históricos

## 1. EstadoAntes vs Después en los Cinco Casos Originales (`[DEMONSTRATED]`)

| Caso | Case ID | Código Fuente | Pre-Fix | Post-Fix | Run ID Replay Post-Fix | SHA-256 Artefacto ELF |
|---|---|---|---|---|---|---|
| **A1** | `case-228d90a490532bbb` | `a = {'x': 1}; b = {'x': 2, 'y': 3}; print({**a, **b})` | `UNSUPPORTED` (`MIR`) | **`EQUIV`** (`{'x': 2, 'y': 3}\n`) | `run_20261101_151614_replay_seed7` | `7810cc0f848dc35c8fa50d7e3dc276debd516b5d86904970804f3fc739099aa5` |
| **A2** | `case-52ec2e43a5d86c64` | `print(list(zip([1, 2], [3, 4], [5, 6])))` | `UNSUPPORTED` (`MIR`) | **`EQUIV`** (`[(1, 3, 5), (2, 4, 6)]\n`) | `run_20261101_151615_replay_seed2` | `07e808a38d0fa642faab51835760c324a0817db1f07aa6173bf2db0c0c40d2e2` |
| **A3** | `case-efd722252f2ec8ff` | `class A: def f(self, *args): return len(args)` / `class B(A): def g(self): return super().f(1, 2, 3, 4)` | `UNSUPPORTED` (`BUILD`) | **`EQUIV`** (`4\n`) | `run_20261101_151615_replay_seed3` | `9efdf59f5c009128ff45aaed9965978d730350c0154ef2d321c97b3e6261790c` |
| **A4** | `case-4d25af23ed64826d` | `class A: pass\nA(0)` | `INVALID_ACCEPTED` | **`EQUIV`** (`TypeError: A() takes no arguments`) | `run_20261101_151616_replay_seed5` | `f606236da91097d7f9f5cf8640d5c036bb6db5f5df9c600b044f79ba210817de` |
| **A5** | `case-707d1042403c5de2` | `class A:\n    pass\nA(0)` | `INVALID_ACCEPTED` | **`EQUIV`** (`TypeError: A() takes no arguments`) | `run_20261101_151617_replay_seed5` | `f606236da91097d7f9f5cf8640d5c036bb6db5f5df9c600b044f79ba210817de` |

---

## 2. Pruebas de Generalización y Controles Negativos (Gate D1 & D3 — `10/10 EQUIV`) (`[DEMONSTRATED]`)

| Variante | Objetivo Semántico | Resultado CPython | Resultado Piton | Veredicto |
|---|---|---|---|---|
| `A1_mixed_order` | `{**d1, 'a': 10, **d2, 'c': 30, 'd': 40}` (intercalado clave/valor + `**`, orden izq→der y last-wins) | `{'a': 20, 'b': 2, 'c': 30, 'd': 40}\n` | `{'a': 20, 'b': 2, 'c': 30, 'd': 40}\n` | **`EQUIV`** |
| `A1_three_dicts_empty` | `{**{}, **a, **{}, **b, **c}` (3+ dicts y dicts vacíos) | `{'k': 9, 'z': 4, 'm': 5}\n` | `{'k': 9, 'z': 4, 'm': 5}\n` | **`EQUIV`** |
| `A1_neg_non_mapping` | `x = [1, 2]; print({**x})` (operando inválido no-mapping sin `try`) | `TypeError` (`exit=1`) | `TypeError: 'list' object is not a mapping` (`exit=1`) | **`EQUIV`** |
| `A1_neg_caught_typeerror` | `try: print({**x})\nexcept TypeError: print('caught TypeError')` | `caught TypeError\n` | `caught TypeError\n` | **`EQUIV`** |
| `A2_zero_one_two_three_four` | `zip()` con 0, 1, 2, 3 y 4 iterables y longitudes desiguales | `[]\n[(10,), (20,)]\n[(1, 3), (2, 4)]\n[(1, 4, 7), (2, 5, 8)]\n[(1, 3, 5, 7), (2, 4, 6, 8)]\n` | Idéntico | **`EQUIV`** |
| `A2_empty_and_generators` | `zip([1, 2], [], [3, 4])` y `zip(gen(3), [10, 20], range(5, 8))` | `[]\n[(0, 10, 5), (1, 20, 6)]\n` | Idéntico | **`EQUIV`** |
| `A3_direct_and_super_varargs` | Herencia con prefix args + `*args`, llamada directa `b.f(...)`, `super().f(...)` y 0 varargs | `14\n109\n5\n` | `14\n109\n5\n` | **`EQUIV`** |
| `A3_neg_too_few_args` | `super().f()` cuando falta argumento posicional requerido (`x`) | `TypeError` (`exit=1`) | `TypeError: A.f() missing 1 required positional argument: 'x'` (`exit=1`) | **`EQUIV`** |
| `A4_inherited_no_init_vs_with_init` | Herencia de `__init__` explícito vs jerarquía sin `__init__` llamada con argumento en `try/except TypeError` | `8\ncaught D(99)\n` | `8\ncaught D(99)\n` | **`EQUIV`** |
| `A5_explicit_init_too_many_args` | `class E: def __init__(self, a, b=2): ...` llamado como `E(1, 2, 3)` y `E()` | `caught E(1,2,3)\ncaught E()\n` | `caught E(1,2,3)\ncaught E()\n` | **`EQUIV`** |
