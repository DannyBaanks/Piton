# Guía de Pitón nativo

```powershell
py -m piton compilar examples\01_hola.piton --backend=x86 --output build\native-subset-1\hola.exe --evidencia build\native-subset-1\receipt.json
```

## Regla de oro

Un backend solo cuenta como nativo si el artefacto ejecutado no carga Python.
Nunca conviertas el PASS de este subconjunto en una afirmación de paridad completa.

## Compilar y generar evidencia Windows

```powershell
py -m piton compilar examples\01_hola.piton --backend=x86 --output build\native-subset-1\hola.exe --evidencia build\native-subset-1\receipt.json
```

Salida real verificada el 2026-09-08:

```text
PITON_NATIVE_BUILD = PASS (C:\Development\ISyCo Git\PITON\build\native-subset-1\hola.exe)
PITON_NATIVE_SUBSET_1_0 = PASS (C:\Development\ISyCo Git\PITON\build\native-subset-1\receipt.json)
```

## Ejecutar el PE

```powershell
& "build\native-subset-1\hola.exe"
```

Salida real:

```text
Hola, mundo
```

## Leer el milestone en el dashboard

```powershell
py -m piton.final_dashboard --format summary --native-receipt build\native-subset-1\receipt.json
```

Salida real completa (el proceso devuelve `1` porque `FULL_PARITY` sigue abierto):

```text
NATIVE_SUBSET_1_0 = PASS
FULL_PARITY = NOT_READY
```

`PASS` y `NOT READY` juntos son correctos: el primero es el subconjunto; el
segundo conserva abiertos runtime, objetos, excepciones, async y stdlib amplios.

## Verificación Linux y máquina limpia

```powershell
py -m unittest tests.test_phase10_linux -v
```

Salida real verificada el 2026-09-08:

```text
test_static_elf_boots_as_only_userspace_in_qemu ... ok
test_static_elf_runs_inside_empty_chroot ... ok
test_static_elf_x86_64_runs_with_empty_environment ... ok
----------------------------------------------------------------------
Ran 3 tests in 48.580s

OK
```

## Verificar toda la suite

```powershell
py -m unittest discover -s tests -q
```

Salida real verificada el 2026-09-08:

```text
----------------------------------------------------------------------
Ran 139 tests in 127.726s

OK
5
5
5
```

Los tres `5` finales son salida heredada de pruebas del traductor; no son fallos.

## Estados

| Estado | Significado | Acción |
|---|---|---|
| PASS | Evidencia reproducible disponible | Conservar el test verde |
| PARTIAL | Existe un subconjunto nativo | Consultar `NATIVE_COMPATIBILITY.md` |
| NOT_DEMONSTRATED | No hay evidencia suficiente | No anunciar compatibilidad |
| INTENTIONALLY_UNSUPPORTED | Fuera del contrato | No implementarlo sin cambiar el contrato |

## Trampas

- `python` puede estar interceptado; usa `py` para ejecutar las pruebas.
- Docker no es necesario para el gate limpio. La prueba usa QEMU/TCG.
- El NASM de WSL no es ejecutable en esta máquina; el backend Linux genera C11 freestanding y GCC produce el ELF x86-64.
- `CLEAN_MACHINE_EXECUTION=PASS` no convierte automáticamente la paridad completa en PASS. Solo demuestra independencia del artefacto probado.
- El recibo Windows demuestra PE AMD64, imports sin Python y ejecución con environment vacío; no sustituye una VM Windows limpia. Ese gate sigue `NOT_DEMONSTRATED`.
- El dashboard rechaza un recibo si el SHA-256 del PE ya no coincide. Regenera PE y recibo juntos.
- `--evidencia` solo aplica al backend `x86`; el gate limpio Linux tiene su propia prueba QEMU.
