# ORACLE.md — Oracle congelado para Pitón

**Versión:** 1.0.0
**Fase:** 1 (1.0 → 1.2) — CONGELAR LA SEMÁNTICA
**Fecha:** 2026-09-07
**Estado:** CONGELADO — cambios requieren rebaseline explícito

---

## 1. Oracle de referencia

| Atributo | Valor |
|----------|-------|
| **Implementación** | CPython |
| **Versión exacta** | 3.12.4 |
| **Compilación** | Release oficial python.org (Windows) / `python3.12` distro (Linux) |
| **Plataforma principal** | Windows 10/11 x64 (entorno de desarrollo) |
| **Plataformas secundarias** | Ubuntu 22.04+ x64, macOS 13+ (ARM64/x64) — NOT_DEMONSTRATED |
| **Arquitectura** | x86-64 |

---

## 2. Flags de ejecución del oracle

```bash
# Windows
python.exe -X utf8 -B -I -S

# Linux/macOS
python3.12 -X utf8 -B -I -S
```

| Flag | Significado | Por qué |
|------|-------------|---------|
| `-X utf8` | UTF-8 mode (PEP 540) | Coherencia encoding cross-platform |
| `-B` | No escribir `.pyc` | Reproducibilidad, no cache |
| `-I` | Isolated mode (no `PYTHONPATH`, no site) | Entorno limpio |
| `-S` | No `import site` | Evita side-effects de `site.py` |

**Variables de entorno fijas:**
```
PYTHONIOENCODING=utf-8
PYTHONUTF8=1
PYTHONDONTWRITEBYTECODE=1
PYTHONHASHSEED=0  # determinismo de hash
```

---

## 3. Versiones de toolchain (para evidencia reproducible)

| Herramienta | Versión | Notas |
|-------------|---------|-------|
| CPython | 3.12.4 | Oracle |
| MSVC (Windows) | 19.40.x (VS 2022 17.9+) | `cl.exe` |
| Clang (Linux/macOS) | 17+ | `clang` |
| LLVM | 17+ | `llc`, `lld` si se usa |
| NASM | 2.16+ | Si se usa asm directo |
| Rust | 1.75+ | Si runtime en Rust |
| Git | 2.45+ | Para hashes de fuente |

---

## 4. Corpus base (baseline v1.0)

El corpus diferencial base está en:
- `examples/*.piton` (9 archivos + `programa_completo.piton`)
- `examples/equivalentes/*.py` (equivalentes Python escritos a mano)
- `tests/fixtures/*.piton` (casos edge)

**Total fixtures v1.0:** 12 programas de prueba
**Gates de evidencia:** 36 (ver `tests/evidence.py`)

---

## 5. Formato de resultados del oracle

Para cada fixture, el oracle produce `oracle_results/<fixture>.json`:

```json
{
  "fixture": "programa_completo",
  "oracle": "CPython 3.12.4",
  "flags": ["-X", "utf8", "-B", "-I", "-S"],
  "env": {"PYTHONIOENCODING": "utf-8", "PYTHONHASHSEED": "0"},
  "result": {
    "exit_code": 0,
    "stdout": "qué onda Danny\n¿y tú quién eres alv?\nargumentos: ['uno', 'dos']\n",
    "stderr": "",
    "exception": null,
    "fs_changes": {},
    "imports_loaded": ["sys"]
  },
  "timestamp": "2026-09-07T19:00:00Z",
  "source_hash": "sha256:...",
  "toolchain": {"python": "3.12.4", "platform": "Windows-10-10.0.19045"}
}
```

---

## 6. Procedimiento de rebaseline (cambio de oracle)

**Solo mediante decisión explícita documentada:**

1. **Issue/PR** titulado `REBASELINE: CPython X.Y.Z → A.B.C`
2. Actualizar `ORACLE.md` con nueva versión, flags, toolchain
3. Re-ejecutar **TODO** el corpus diferencial contra nuevo oracle
4. Actualizar `tests/evidence.py` si hay gates nuevos/rotos
5. Commit con mensaje `rebaseline: oracle CPython 3.12.4 → 3.13.0`
6. Tag `oracle-rebaseline-YYYYMMDD`

**No se permite:** rebaseline silencioso, cambiar oracle sin re-ejecutar corpus completo.

---

## 7. Compatibilidad hacia adelante

| Nueva versión CPython | Acción |
|----------------------|--------|
| 3.12.x (patch) | Rebaseline automático en CI; no rompe gates |
| 3.13 (minor) | Rebaseline explícito + revisión de `SPEC.md` por features nuevas |
| 3.14+ (major) | Rebaseline explícito + posible `SPEC.md` v2.0 |

---

## 8. Qué NO es el oracle

| No es | Aclaración |
|-------|------------|
| Spec de Python | El spec es la [Python Language Reference](https://docs.python.org/3/reference/). El oracle es una **instancia concreta** para medir. |
| Único runtime válido | Pitón puede tener backends propios (x86, WASM, etc.). El oracle solo define **qué es "correcto"** para el corpus diferencial. |
| Inmutable para siempre | Se rebaselinea explícitamente (§6). |

---

## 9. Evidencia de congelación

```
ORACLE.md v1.0 firmado: _________________________  Fecha: __________
CPython 3.12.4 hash:    sha256:_________________________
Corpus baseline hash:   sha256:_________________________ (de examples/)
Evidence gates:         36/36 PASS
```