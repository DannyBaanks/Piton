# BASELINE — PITON × AUTOPITON Parity Attack Cycle V1

## 1. Proveniencia de Git y Entorno (`[DEMONSTRATED]`)

- **Repositorio:** `/home/danny/piton-autopiton`
- **Rama activa:** `feat/autopiton-polish` (`main` intacta, sin merge ni push)
- **Commit HEAD inicial:** `d6a7ab54b40aea4ce1d63820924d846d9b788373`
- **Oráculo CPython:** `Python 3.12.3` (`/usr/bin/python3`)
- **Compilador C nativo:** `gcc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0`
- **Zig:** `0.16.0`
- **Sandbox:** `bubblewrap 0.9.0` (`/usr/bin/bwrap`)
- **Sistema Operativo:** `Linux 7.0.0-42-generic x86_64 (Ubuntu 24.04)`

## 2. Estado Inicial de Corpus Específicos (Pre-Fase A)

| Corpus | Run ID de Referencia | Total | `EQUIV` | `INVALID_REFERENCE` | Otros |
|---|---|---|---|---|---|
| Sistemático | `run_20261101_144510_systematic` | 74 | 74 (100.0%) | 0 | 0 |
| Bounded (`seed=11`, 30 casos) | `run_20261101_145040_bounded_seed11` | 30 | 30 (100.0%) | 0 | 0 |

> **Nota de alcance:** Estas cifras corresponden exclusivamente a los corpus indicados y no representan un porcentaje global de compatibilidad con Python.

## 3. Reconciliación por Replay de los 58 Casos No-`EQUIV` Históricos en `index.json` (`[DEMONSTRATED]`)

Antes de modificar una sola línea de código en Fase A, se ejecutó `replay` real en sandbox sobre los **58** casos con clasificación histórica distinta de `EQUIV` registrados en `autopiton/cases/index.json` (de un total inicial de 260 casos):

| Clasificación en Replay (Pre-Fase A) | Cantidad | Explicación |
|---|---:|---|
| `EQUIV` | **50** | Resueltos por las correcciones previas de plegado constante en defaults, inferencia de tipos `bool`/`float`/`str` y orden de bloques CFG |
| `INVALID_REFERENCE` | **3** | `case-912241919394a987`, `case-e40e23b17c444ddb`, `case-e49b5c7a9e738d48`: programas inválidos donde CPython lanza `TypeError`/`RuntimeError` y Piton rechaza en `MIR` |
| `UNSUPPORTED` | **3** | `case-228d90a490532bbb` (`{**a, **b}`), `case-52ec2e43a5d86c64` (`zip` 3-ario), `case-efd722252f2ec8ff` (`super().f(1, 2, 3, 4)`) |
| `INVALID_ACCEPTED` | **2** | `case-4d25af23ed64826d` y `case-707d1042403c5de2` (`class A: pass; A(0)`) |
| **Total reconciliado** | **58** | **5 contraejemplos genuinos pendientes al iniciar Fase A** |

## 4. Reproducción Pre-Fix de los Cinco Contraejemplos (`[DEMONSTRATED]`)

| Caso | ID | Clasificación Pre-Fix | Etapa Piton | Resultado CPython | Resultado Piton Pre-Fix |
|---|---|---|---|---|---|
| **A1** | `case-228d90a490532bbb` | `UNSUPPORTED` | `MIR` (rechazado) | `stdout="{'x': 2, 'y': 3}\n"`, `exit=0` | `MIRLoweringError: dict literal merge ({**a, **b}) is not supported in the native subset` |
| **A2** | `case-52ec2e43a5d86c64` | `UNSUPPORTED` | `MIR` (rechazado) | `stdout="[(1, 3, 5), (2, 4, 6)]\n"`, `exit=0` | `MIRLoweringError: native zip requires exactly 2 arguments` |
| **A3** | `case-efd722252f2ec8ff` | `UNSUPPORTED` | `BUILD` (rechazado) | `stdout="4\n"`, `exit=0` | `NativeBuildError: too many arguments to function 'A__f'` |
| **A4** | `case-4d25af23ed64826d` | `INVALID_ACCEPTED` | `EXECUTE` (`exit=0`) | `TypeError: A() takes no arguments` (`exit=1`) | `exit=0`, `stdout=''`, `stderr=''` |
| **A5** | `case-707d1042403c5de2` | `INVALID_ACCEPTED` | `EXECUTE` (`exit=0`) | `TypeError: A() takes no arguments` (`exit=1`) | `exit=0`, `stdout=''`, `stderr=''` |
