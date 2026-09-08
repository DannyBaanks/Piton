# Guía de Pitón nativo

```powershell
py -m unittest tests.test_phase10_linux -v
```

## Regla de oro

Un backend solo cuenta como nativo si el artefacto ejecutado no carga Python.
Un test bootstrap que usa CPython como oracle no demuestra esa independencia.

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
