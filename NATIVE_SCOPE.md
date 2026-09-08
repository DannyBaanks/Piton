# NATIVE_SCOPE.md — Alcance del backend nativo v1 (Fase 5)

**Versión:** 1.0.0
**Fase objetivo:** 5 (2.2 → 2.5) — Primer x86-64 real
**Oracle:** CPython 3.12.4
**Fecha:** 2026-09-07
**Estado:** BORRADOR PARA DECISIÓN — se congela en Fase 4

> Documento histórico del MVP planeado antes del backend. El contrato
> ejecutable vigente es `NATIVE_SUBSET_1_0.md`; la matriz viva está en
> `NATIVE_COMPATIBILITY.md`. Las exclusiones originales de este borrador no
> describen la implementación actual.

---

## 1. Objetivo de la Fase 5

Producir el **primer ejecutable Windows x86-64** que:
1. Arranca y corre **sin `python.exe`, `python3`, `libpython`, ni VM de bytecode Pitón**.
2. Cubre un **subconjunto útil y demostrable** de Pitón.
3. Pasa la **prueba reina** (§3) en máquina limpia.
4. Mantiene **evidencia diferencial** contra el oracle CPython para el subconjunto reclamado.

**No objetivo:** paridad completa, optimización, self-hosting.

---

## 2. Subconjunto MÍNIMO VIABLE (MVP nativo)

El siguiente subconjunto es **suficiente para demostrar** que Pitón puede emitir x86-64 real y correr sin Python, y **necesario** para que la prueba reina tenga sentido.

| Categoría | Incluido en MVP | Excluido (fase posterior) |
|-----------|-----------------|---------------------------|
| **Literales** | `int` (small, tagged), `bool`, `Nada` | `float`, `str`, `bytes`, contenedores |
| **Aritmética** | `+`, `-`, `*`, `//`, `%` (enteros) | `**`, `/`, operaciones float, big ints |
| **Comparación** | `==`, `!=`, `<`, `<=`, `>`, `>=` (int) | `is`, `in`, comparaciones encadenadas |
| **Control** | `si`/`sino`, `mientras`, `para` (solo `rango(n)` simple) | `para` genérico, `segun`/`caso`, `asincrono` |
| **Funciones** | `funcion` sin args, sin defaults, `devolver` | Parámetros, defaults, `*args`, `**kwargs`, closures, lambdas, generadores, decorators |
| **Variables** | Locales (stack slots), asignación simple | `global`, `no_local`, shadowing complejo |
| **I/O básico** | `imprimir` (solo `str` literal o variable) via runtime | `entrada`, `abrir`, `sys.stdout`, formatting |
| **Tipos** | Solo `int`/`bool`/`Nada` inmediatos | `str`, `list`, `dict`, `tuple`, `set`, clases, objetos |
| **Excepciones** | NO (salida vía código de retorno) | `intentar`/`excepto`/`lanzar` — Fase 7 |
| **Imports** | NO (programa single-file) | Import hook, módulos — Fase 9 |

---

## 3. Prueba Reina (debe pasar para declarar `PITON_NATIVE_X86 = DEMONSTRATED`)

```bash
# 1. Compilar
piton compilar hola.piton --backend=x86 --output=hola.exe

# 2. Mover a VM limpia (Windows 10/11, sin Python instalado)
#    scp hola.exe vm-limpia:/tmp/

# 3. Ejecutar en VM
> hola.exe
Hola, mundo

# 4. Inspeccionar imports del PE
> dumpbin /imports hola.exe
#    DEBE mostrar SOLO: KERNEL32.dll, ucrtbase.dll, etc.
#    NO DEBE mostrar: python312.dll, python3.dll, libpython*.dll

# 5. Verificar contra oracle CPython
#    python3 hola.py  → mismo stdout, mismo exit code

# 6. Desensamblar y conservar evidencia
> objdump -d hola.exe > hola.asm
#    Guardar hola.asm, hola.exe, hash SHA-256 de ambos
```

**Archivo `hola.piton` de referencia:**
```piton
funcion main():
    imprimir("Hola, mundo")

main()
```

**Python generado equivalente:**
```python
def main():
    print("Hola, mundo")

main()
```

---

## 4. Gates de Fase 5 (de ROADMAP.md)

| Gate | Criterio de PASS |
|------|------------------|
| `PITON_X86_HELLO` | `hola.exe` imprime "Hola, mundo\n" y exit 0 |
| `PITON_X86_BRANCH` | `si`/`sino` compila y ramifica correctamente |
| `PITON_X86_LOOP` | `mientras`/`para rango` compila e itera correctamente |
| `PITON_X86_FUNCTION` | `funcion`/`devolver` compila, llama y retorna correctamente |
| `PYTHON_RUNTIME_DEPENDENCY` | 0 — ejecutable no carga python*.dll |
| `PE_IMPORTS_PYTHON` | 0 — tabla de imports PE limpia |
| `DIFFERENTIAL_NATIVE_SUBSET` | Corpus MVP produce mismos observables que CPython |

---

## 5. Decisiones de arquitectura (previas a Fase 5, definidas en Fase 4)

| Decisión | Opción recomendada | Justificación |
|----------|-------------------|---------------|
| **ABI** | Windows x64 (Microsoft) primero | Entorno principal de desarrollo |
| **ABI secundario** | SysV AMD64 (Linux/macOS) | Compatibilidad cross-platform |
| **Stack alignment** | 16 bytes | Requisito Windows x64 / SysV |
| **Calling convention** | Propia, documentada | Control total; no copiar CPython |
| **Registros** | `RAX` retorno, `RCX/RDX/R8/R9` args (Win) / `RDI/RSI/RDX/RCX/R8/R9` (SysV) | Estándar de cada ABI |
| **Value representation** | **Tagged pointers (NaN-boxing NO en v1)** | Small ints inmediatos (tag 0), bool/Nada inmediatos, heap objects puntero taggeado |
| **Heap allocation** | Bump allocator simple + `free` manual | Sin GC en MVP; Fase 6 añade refcount/GC |
| **Error handling** | Return codes + out-param para error | Sin exceptions en MVP; Fase 7 |
| **Runtime interface** | C header `piton_runtime.h` con `PitonValue`, `piton_print`, `piton_alloc` | Límite claro generated↔runtime |

**Value model MVP (congelado en Fase 4):**
```
bits 0-1: tag
  00 = small int (value << 2)
  01 = heap pointer (aligned, tag stripped)
  10 = bool (0=false, 1=true en payload)
  11 = Nada (singleton)
```

---

## 6. Pipeline de compilación nativa

```
.piton
  → frontend Pitón (lexer, parser, analysis)        [existente v1.0]
  → HIR (intención: Func, Call, BinOp, If, Loop)    [Fase 3]
  → MIR (ops: Const, Load, Store, Add, Cmp, Jmp, Call, Ret) [Fase 3]
  → x86-64 codegen (Windows COFF → PE)              [Fase 5]
  → linker (lld / link.exe) → .exe
```

**Herramientas permitidas:** NASM, MASM, LLVM `llc`, `lld`, `link.exe`, o emisión directa de bytes. **Meta:** código máquina real, no pureza de "escrito a mano".

---

## 7. Runtime mínimo para MVP (Fase 4-5)

```c
// piton_runtime.h — interfaz C que el código generado llama
#ifndef PITON_RUNTIME_H
#define PITON_RUNTIME_H

#include <stdint.h>
#include <stddef.h>

typedef uint64_t PitonValue;  // tagged value

// Tags
#define PITON_TAG_INT   0x0
#define PITON_TAG_PTR   0x1
#define PITON_TAG_BOOL  0x2
#define PITON_TAG_NONE  0x3

// Constructores
static inline PitonValue piton_int(int64_t x) { return (x << 2) | PITON_TAG_INT; }
static inline int64_t piton_int_value(PitonValue v) { return v >> 2; }
static inline PitonValue piton_bool(int b) { return (uint64_t)(b ? 1 : 0) << 2 | PITON_TAG_BOOL; }
static inline int piton_bool_value(PitonValue v) { return (v >> 2) & 1; }
static inline PitonValue piton_none() { return (uint64_t)0 << 2 | PITON_TAG_NONE; }
static inline int piton_is_none(PitonValue v) { return (v & 0x3) == PITON_TAG_NONE; }

// Heap allocation (bump allocator simple para MVP)
void* piton_alloc(size_t size);
void piton_free(void* ptr);

// I/O
void piton_print_int(int64_t x);
void piton_print_str(const char* s, size_t len);
void piton_print_newline(void);

#endif
```

**Implementación:** C (compilado con MSVC/Clang/GCC) o Rust/Zig si acelera corrección. **Objetivo:** eliminar CPython, no demostrar self-hosting.

---

## 8. Queda EXPLÍCITAMENTE FUERA del MVP nativo (Fase 6+)

| Feature | Fase |
|---------|------|
| Strings heap (`str`) | 6 |
| Big integers (arbitrary precision) | 6 |
| Listas (`list`) / tuplas / dicts / sets | 6 |
| Reference counting / GC | 6 |
| Excepciones (`try`/`except`/`raise`) | 7 |
| Closures / cells / lambdas | 7 |
| Generadores (`yield`) | 7 |
| Clases / MRO / descriptors | 8 |
| Imports nativos / paquetes | 9 |
| Async / await | 10 |
| `eval`/`exec`/`compile` dinámico | 11 |
| Stdlib nativa (os, json, etc.) | 12 |
| Optimizaciones (SSA, inlining, PIC) | 13 |

---

## 9. Criterio de "lista para Fase 5"

Antes de escribir el primer `MOV`/`ADD` x86:

- [ ] `SPEC.md` congelado y firmado
- [ ] `SEMANTICS.md` congelado y firmado
- [ ] `ORACLE.md` escrito (CPython 3.12.4 exacto, flags, plataforma)
- [ ] `NATIVE_SCOPE.md` **este documento** congelado y firmado
- [ ] `PITON_VALUE_MODEL = FROZEN` gate PASS
- [ ] `WINDOWS_X64_ABI = PASS` gate PASS (calling convention probada con asm inline)
- [ ] `STACK_ALIGNMENT = PASS` gate PASS
- [ ] `CALL_CONVENTION_TESTS = PASS` gate PASS
- [ ] Corpus diferencial MVP versionado (`tests/corpus/native_mvp/`)
- [ ] HIR/MIR definidos y `HIR_TO_MIR = PASS`, `MIR_DETERMINISTIC = PASS`
- [ ] `MIR_TO_PYTHON_ORACLE_EQUIVALENCE = PASS` (backend Python desde MIR)

---

## 10. Decisión pendiente (para hoy/semana)

| Pregunta | Opciones | Recomendación |
|----------|----------|---------------|
| **¿Incluir `float` en MVP?** | Sí / No | **No** — complica value model y ABI; Fase 6 |
| **¿Incluir `str` literal en MVP?** | Sí / No | **No** — requiere heap + GC/refcount; Fase 6. MVP solo `imprimir(int)` |
| **¿Backend codegen: LLVM vs emisión directa?** | LLVM / Directo | **LLVM (`inkwell`/`llvm-sys` o `clang` + `.ll`)** — acelera corrección, ABI probado |
| **¿Runtime en C, Rust o Zig?** | C / Rust / Zig | **Rust** (memoria segura, buen FFI, `inkwell` para LLVM) o **C** (más simple, MSVC nativo) |
| **¿Linker: `lld` o `link.exe`?** | lld / link.exe | **`lld` (LLVM linker)** — cross-platform, no requiere Visual Studio instalado |

---

## 11. Firmas de congelación

```
SPEC.md v1.0:     _________________________  Fecha: __________
SEMANTICS.md v1.0: _________________________  Fecha: __________
NATIVE_SCOPE.md v1.0: _______________________  Fecha: __________
ORACLE.md v1.0:    _________________________  Fecha: __________
```

**Una vez firmados, no se modifican sin rebaseline explícito y versión `2.0`.**
