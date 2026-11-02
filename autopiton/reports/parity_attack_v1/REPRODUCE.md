# REPRODUCE — Guía de Reproducción Exacta (Fase D5)

Todos los comandos deben ejecutarse desde la raíz del repositorio `/home/danny/piton-autopiton` en la rama `feat/autopiton-polish` con `bubblewrap` (`bwrap`) disponible.

## 1. Verificar Entorno y Sandbox

```bash
cd /home/danny/piton-autopiton
python3 -m autopiton.cli doctor
```

## 2. Reproducir los Cinco Contraejemplos Históricos (Fase A)

```bash
python3 -m autopiton.cli replay case-228d90a490532bbb
python3 -m autopiton.cli replay case-52ec2e43a5d86c64
python3 -m autopiton.cli replay case-efd722252f2ec8ff
python3 -m autopiton.cli replay case-4d25af23ed64826d
python3 -m autopiton.cli replay case-707d1042403c5de2
```
Resultado esperado: `classification=EQUIV` en los 5 casos.

## 3. Reproducir las Campañas Diferenciales Bounded y Sistemáticas (Fase B)

```bash
python3 -m autopiton.cli compare --canary
python3 -m autopiton.cli compare --systematic
python3 -m autopiton.cli compare --bounded -c 30 --seed 11
python3 -m autopiton.cli compare --bounded -c 40 --seed 23
python3 -m autopiton.cli compare --bounded -c 30 --seed 99
python3 -m autopiton.cli compare --bounded -c 30 --seed 77
```
Resultado esperado:
- `--canary`: `7 EQUIV`, `3 INVALID_REFERENCE` (`0` divergencias).
- `--systematic`: `74/74 EQUIV`.
- `--bounded` (`seed=11`, `seed=23`, `seed=99`, `seed=77`): `100% EQUIV` (`30/30`, `40/40`, `30/30`, `30/30`).

## 4. Reproducir Evaluación de Stdlib y Controles de Mutación (Fases C y D2)

```bash
python3 -m autopiton.cli synthesize --module bisect -c 10 --seed 7
python3 -m autopiton.cli synthesize --module bisect -c 10 --seed 7 --control
python3 -m autopiton.cli synthesize --module heapq -c 10 --seed 7
python3 -m autopiton.cli synthesize --module heapq -c 10 --seed 7 --control
```

## 5. Ejecutar Suite de Regresiones (Fase D4)

```bash
PYTHONPATH=. pytest autopiton/tests/ tests/test_p15_decorators.py -q
PYTHONPATH=. pytest tests/test_grammar_parity.py -k "test_non_const_default_closure or DictMergeV1 or BuildFailGatesV1 or RecursiveStdlibGatesV2" -q
```

## 6. Verificar Integridad de `SHA256SUMS.txt`

```bash
cd /home/danny/piton-autopiton/autopiton/reports/parity_attack_v1
sha256sum -c SHA256SUMS.txt
```
