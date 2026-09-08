# Native Subset 1.0

Estado: `PASS` cuando un recibo `piton-native-subset-evidence-v1` válido acompaña al dashboard.

Este milestone demuestra un subconjunto x86-64 útil. No declara paridad completa
con Python ni convierte los gates amplios de Windows, Linux o runtime en `PASS`.

## Comando canónico

```powershell
py -m piton compilar examples\01_hola.piton --backend=x86 --output build\native-subset-1\hola.exe --evidencia build\native-subset-1\receipt.json
```

## Gates automatizados

El recibo exige simultáneamente:

| Gate | Criterio |
|---|---|
| `X86_CODE_EMITTED` | El PE declara máquina AMD64 (`0x8664`) |
| `EXECUTABLE_LINKED` | Existe el PE enlazado |
| `PE_IMPORT_TABLE_READ` | `objdump` pudo leer imports reales |
| `PE_IMPORTS_PYTHON` | Ningún DLL importado contiene `python` |
| `PYTHON_MARKER_ABSENT` | El PE no contiene el marcador binario `python` |
| `WINDOWS_EMPTY_ENV_EXECUTION` | El PE termina en cero con un environment vacío |
| `NATIVE_DIFFERENTIAL_SUBSET` | Exit code, stdout y stderr coinciden con el oracle |
| `NATIVE_CORPUS` | La suite nativa de `tests/test_phase5.py` termina completa en PASS |
| `ORACLE_PINNED` | El oracle es exactamente CPython 3.12.4 |

El recibo conserva SHA-256 de fuente, corpus y PE, observaciones en bytes,
imports y versiones de NASM, GCC y objdump. Antes de aceptarlo, el dashboard
vuelve a calcular los hashes, reinspecciona y ejecuta el PE, repite el oracle y
repite el corpus nativo.

## Alcance demostrado

El alcance vivo está enumerado en `NATIVE_COMPATIBILITY.md` y protegido por el
corpus de `tests/test_phase5.py`. Una operación fuera de ese alcance debe fallar
con `NativeBuildError`, nunca aproximarse silenciosamente.

## Límites explícitos

- `WINDOWS_CLEAN_MACHINE_EXECUTION` permanece `NOT_DEMONSTRATED`: este host no
  tiene Windows Sandbox y la consulta de optional features requiere elevación.
- `CLEAN_MACHINE_EXECUTION=PASS` procede del ELF Linux ejecutado como `/init`
  único bajo QEMU; no se reutiliza como evidencia Windows.
- El compilador sigue escrito y ejecutado con CPython. Los PE/ELF generados no
  cargan CPython. Self-hosting no forma parte de este milestone.
- `FULL_PARITY` permanece `NOT_DEMONSTRATED`.
