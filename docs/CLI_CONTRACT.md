# CLI de Pitón — contrato de superficie (2026-10-05)

Meta: tras `pip install .`, una CLI para descubrir, ejecutar, comprobar,
compilar, inspeccionar y diagnosticar el lenguaje sin conocer el repo.

## Entry points (sin cambios)

| comando | definición |
|---|---|
| `piton` | `piton.cli:main` |
| `pitn`  | `piton.cli:main` (alias) |
| `pi`    | `piton.cli:main` (superficie corta recomendada) |
| `python -m piton` | `piton/__main__.py` → `cli.main()` |

## Comandos canónicos

| comando | contrato |
|---|---|
| `pi run <archivo>` | ejecuta con CPython (default, igual que `ejecutar`) |
| `pi run <archivo> --engine native` | compila a nativo y ejecuta; nunca fallback silencioso a CPython |
| `pi build <archivo>` | compila a target nativo (default = host) |
| `pi build <archivo> --target windows-x86_64` | PE Windows |
| `pi build <archivo> --target linux-x86_64` | ELF Linux |
| `pi check <archivo>` | valida sintaxis + lowering a MIR; no emite ejecutable |
| `pi emit <python\|ast\|tokens\|mir> <archivo>` | representación del compilador a stdout |
| `pi repl` | REPL CPython con banner del engine real |
| `pi test [rutas...]` | passthrough a pytest |
| `pi corpus` | corpus diferencial (flags actuales) |
| `pi evidence [--receipt R] [--format summary\|markdown\|json]` | wrapper del dashboard |
| `pi targets` | targets nativos y estado de toolchain |
| `pi doctor` | diagnóstico del entorno del compilador |
| `pi version` / `pi --version` / `pi -V` | misma fuente `piton.__version__` |

## Aliases históricos (compatibles, sin deprecation warning)

| legacy | equivalente |
|---|---|
| `ejecutar` | `run --engine cpython` |
| `traducir` | `emit python` |
| `verificar` | `check` (validación CPython, mensaje legacy conservado) |
| `tokens` | `emit tokens` |
| `ast` | `emit ast` |
| `compilar` | `build` |

## Targets

| target | formato | backend interno | toolchain |
|---|---|---|---|
| `windows-x86_64` | PE `.exe` | backend x86 | `nasm` + MinGW GCC |
| `linux-x86_64` | ELF estático | backend linux | `gcc` (Linux) / WSL (Windows) |

Aliases aceptados: `x86` → `windows-x86_64`, `linux` → `linux-x86_64`.
No existe backend macOS/ARM/WASM declarado.

## Argumentos del programa

Todo lo que siga a `--` se entrega intacto al programa:

```text
pi run app.piton -- uno "dos tres" -x --flag
```

Sin `--`, `run` no acepta argumentos del programa (argparse, exit 2).
`ejecutar` conserva `argparse.REMAINDER` por compatibilidad.

## Exit codes

| código | clase |
|---|---|
| 0 | éxito (incluye `pi targets`/`pi doctor` con toolchain completo/parcial) |
| 1 | build/toolchain/evidencia/I-O/LOWERING error |
| 2 | uso/argparse, o error de sintaxis Python-side (`ejecutar`, `verificar`) |

No se introduce taxonomía nueva; argparse ya usa 2 para usage errors.

## stdout/stderr

- stdout: resultado solicitado (output del programa, JSON de `emit`, artefacto
  producido, representaciones).
- stderr: diagnóstico (`PITON_NATIVE_BUILD_ERROR`, `PITON_IO_ERROR`,
  `PITON_INTERNAL_ERROR`, hints, flags `--verbose`).

## Reglas de error

- Error esperable del usuario (sintaxis, lowering, target inválido,
  toolchain ausente, archivo inexistente): mensaje corto accionable,
  **sin traceback**, exit code según tabla.
- Error interno inesperado: `PITON_INTERNAL_ERROR` + tipo/mensaje y
  `hint: use --debug`; con `--debug` (antes del subcomando) se propaga el
  traceback.

## Sin éxito falso

- `run --engine native` jamás cae a CPython sin avisar: si no compila,
  falla con `PITON_NATIVE_BUILD_ERROR` + hint `--engine cpython`.
- `build` jamás imprime `PITON_NATIVE_BUILD = PASS` si no existe artefacto
  producido.
- `check` no afirma "native-compatible" si no corrió el pipeline completo
  de lowering a MIR compartido por ambos targets.
- `emit` escribe solo el contenido solicitado a stdout (pipelineable).

## Color / TTY

No se implementa en esta release (`--color` no existe; diagnóstico legible
en logs por construcción).

## Veredicto final

El objetivo NO es "PITÓN implementa todo Python". Es: "PITÓN cerró el
alcance nativo que declara, con Windows y Linux, un corpus diferencial
enumerativo de 1220 casos sin divergencias observadas y evidencia explícita
de lo que queda fuera."
