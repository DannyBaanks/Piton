# PITÓN — Protocolo de Handoff desde Sesión Nueva (FRESH_SESSION_HANDOFF)

> Para un agente con CERO contexto. Sigue estos pasos en orden. No improvises
> el orden de trabajo: el roadmap manda, los tests juzgan, los artefactos son
> evidencia.

## Paso 0 — Contexto del proyecto

PITÓN es un compilador que traduce una superficie en español de México a
semántica CPython 3.12 y produce ejecutables nativos x86-64 (Windows PE y
Linux ELF) que corren **sin** CPython. El compilador está escrito en Python;
el backend nativo tiene un runtime en C.

## Paso 1 — Checkout y ubicación

```powershell
cd "C:\Development\ISyCo Git\PITON"
git checkout main
git pull
```

## Paso 2 — Archivos que leer primero

| Archivo | Por qué |
|---|---|
| `README.md` | orientación |
| `docs/PITON_CPYTHON_3_12_MASTER_ROADMAP.md` | **autoridad de trabajo** |
| `docs/PARITY_DEFINITION.md` | qué significa "paridad" y sus no-goals |
| `docs/CPYTHON_PARITY_DEPENDENCY_DAG.md` | orden de dependencias |
| `docs/FEATURE_STATUS_MATRIX.md` | estado real por feature/plataforma |
| `piton/final_dashboard.py` | gates declarados |
| `ROADMAP.md` | historial de fases |

## Paso 3 — Línea base (debe dar lo que el dashboard declara)

```powershell
py -m pytest tests/test_phase5.py tests/test_phase14.py tests/test_phase10_linux.py -q
```

Esperado: **117 passed** en este estado verificado. Si no, DETENTE: algo no está
en el estado documentado. No empieces un gate nuevo con la línea base rota.

## Paso 4 — Leer el dashboard

```powershell
py -m pytest tests/test_phase14.py -q
py -c "from piton.final_dashboard import build_dashboard, render_summary; print(render_summary(build_dashboard()))"
```

Los gates declarados derivan de `FINAL_GATES`. No promuevas un gate amplio por
muchos tests estrechos verdes (regla fail-closed).

## Paso 5 — Elegir el siguiente gate desbloqueado

1. En el roadmap maestro, abre la sección "PRIMEROS 10 GATES RECOMENDADOS".
2. Verifica en el DAG que sus dependencias estén `PASS`.
3. Un gate con dependencia no cerrada está `BLOCKED`.

## Paso 6 — Capturar el fallo pre-implementación

Antes de tocar código, corre el corpus del gate y **conserva** el fallo esperado
(es evidencia de que el test detecta el hueco).

## Paso 7 — Implementar

Lee el bloque completo del gate en el roadmap: superficie de implementación,
`DO_NOT_TOUCH`, semántica a replicar, tests positivos/negativos/diferenciales,
edge cases, plataformas.

## Paso 8 — Verificar en orden

```powershell
py -m pytest <test_focalizado> -q      # tests del gate
py -m pytest <test_diferencial> -q     # corpus diferencial
py -m pytest <test_negativo> -q        # casos negativos
py -m pytest tests/ -q                 # suite de regresión completa
```

## Paso 9 — Actualizar evidencia y claims

- Registra el recibo (ver `PITON_CPYTHON_3_12_MASTER_ROADMAP.md` §"Recibos").
- Actualiza `final_dashboard.py`, `FEATURE_STATUS_MATRIX.md`,
  `STDLIB_PARITY_MATRIX.md` (si aplica), `README.md`.
- No marques un gate `PASS` sin nueva verificación.

## Paso 10 — Commit

- Revisa `git diff` (solo cambios del gate, sin cambios no relacionados).
- Mensaje que nombre el GATE_ID.
- Comando de línea base documentado en el mensaje.
- Push solo si se te instruye.

## Definición de DONE de una sesión

Una sesión NO termina por escribir código. Termina cuando:

- el gate está explícitamente nombrado;
- el fallo pre-implementación quedó capturado;
- la implementación está completa;
- tests enfocados + diferenciales + negativos + regresión pasan;
- docs + dashboard + evidencia actualizados;
- `git diff` revisado;
- veredicto final del gate escrito.

## Trampas conocidas (léelas antes de tocar código)

- **`python` está interceptado** en esta máquina (shim devuelve 2718). Usa `py`.
- **Windows vs Linux** en un feature cross-platform: un `PASS` de un solo lado
  no es un `PASS` del feature.
- **No normalizar oculto**: toda normalización de stderr/line endings debe estar
  documentada en el corpus. Compara semántica estructurada, no texto exacto,
  salvo que el gate declare texto.
- **`FULL_PARITY` está retirado** como término. Usa los gates de
  `PARITY_DEFINITION.md`.
- **Stop conditions arquitectónicas**: si el gate exige cambio de frame model,
  GC, object-layout, ABI o calling convention ⇒ `ARCHITECTURE_REVIEW_REQUIRED`,
  detente antes de implementar.