#!/usr/bin/env python3
"""parity_corpus_sec2 - corpus secundario stdlib/iteración.

Ejecuta diferencial: CPython 3.12 (oracle) vs PITON linux_x86 (nativo).
Criterio HONESTO: EQUIV solo si (a) ambos lanzan excepción O (b) stdout coincide
BYTE A BYTE y el código de salida coincide.

Uso:
  python3 tools/parity_corpus_sec2.py --json /tmp/sec_stdlib.json
"""
from __future__ import annotations

import argparse
import glob
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Estado bucket
EQUIV = "EQUIV"
RAISING_EQ = "RAISE_EQ"
DIVERGENT_MISMATCH = "DIVERGENT_MISMATCH"
DIVERGENT_CRASH = "DIVERGENT_CRASH"
TIMEOUT = "TIMEOUT"
COMPILE_ERROR = "COMPILE_ERROR"
RUNNER_ERROR = "RUNNER_ERROR"

BUCKETS = [EQUIV, RAISING_EQ, DIVERGENT_MISMATCH, DIVERGENT_CRASH, TIMEOUT, COMPILE_ERROR, RUNNER_ERROR]


def _exception_name(blob: bytes) -> str | None:
    """Extract exception name from stderr."""
    text = blob.decode("utf-8", "replace")
    import re
    match = re.search(r"^([A-Za-z_][A-Za-z_0-9]*(?:Error|Exception|Warning))", text, re.M)
    if match:
        return match.group(1)
    if "Traceback" in text:
        return "Exception"
    return None


def _short(blob: bytes, limit: int = 160) -> str:
    return blob.decode("utf-8", "replace").strip().replace("\n", " | ")[:limit]


def run_script_cpython(path_str: str, timeout: float = 30.0) -> subprocess.CompletedProcess:
    """Ejecuta script con CPython (el oracle)."""
    src = Path(path_str).read_text(encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-c", src],
        capture_output=True,
        timeout=timeout,
        cwd=Path(path_str).parent,
        stdin=subprocess.DEVNULL,
    )


# Mapa de adaptacion Python->PITON (keywords en espanol)
ADAPT_MAP = {
    "if": "si", "elif": "sino_si", "else": "sino", "while": "mientras",
    "for": "para", "in": "en", "def": "funcion", "class": "clase",
    "try": "intentar", "except": "excepto", "finally": "finalmente",
    "with": "con", "as": "como", "import": "importar", "from": "desde",
    "return": "devolver", "yield": "producir", "raise": "lanzar",
    "pass": "pasar", "break": "romper", "continue": "continuar",
    "assert": "afirmar", "del": "borrar", "global": "global",
    "and": "y", "or": "o", "not": "no", "is": "es",
    "True": "Verdadero", "False": "Falso", "None": "Nada",
    "async": "asincrono", "await": "esperar",
}


def python_to_piton(src: str) -> str:
    """Traduce Python a PITON (keywords en espanol, sin comentarios).
    
    Usa el tokenizador de Python para identificar keywords reales y evitar
    falsos positivos como 'in' dentro de 'print'.
    """
    import tokenize
    import io
    
    # Eliminar comentarios primero
    lines = src.splitlines()
    cleaned_lines = []
    for line in lines:
        # Eliminar comentario (simple, no manejamos strings con #)
        if '#' in line:
            # Encontrar posicion del comentario fuera de strings
            in_string = None
            for i, c in enumerate(line):
                if in_string:
                    if c == in_string and (i == 0 or line[i-1] != '\\'):
                        in_string = None
                elif c in ('"', "'"):
                    in_string = c
                elif c == '#':
                    line = line[:i]
                    break
        cleaned_lines.append(line)
    
    src_no_comments = '\n'.join(cleaned_lines)
    
    # Tokenizar y adaptar
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(src_no_comments).readline))
        result = []
        for tok in tokens:
            tok_type, tok_str, start, end, line = tok
            if tok_type == tokenize.NAME and tok_str in ADAPT_MAP:
                tok_str = ADAPT_MAP[tok_str]
            result.append((tok_type, tok_str))
        return tokenize.untokenize(result)
    except tokenize.TokenError as e:
        return python_to_piton_fallback(src_no_comments)


def python_to_piton_fallback(src: str) -> str:
    """Fallback: sustitucion palabra por palabra con regex preciso."""
    import re
    
    lines = src.splitlines()
    result_lines = []
    
    # Regex que captura palabras completas con word boundaries
    keyword_pattern = re.compile(r'\b(' + '|'.join(re.escape(k) for k in ADAPT_MAP.keys()) + r')\b')
    
    for line in lines:
        result_lines.append(keyword_pattern.sub(lambda m: ADAPT_MAP[m.group(0)], line))
    
    return '\n'.join(result_lines)


def strip_comments(src: str) -> str:
    """Elimina comentarios # de un script Python.
    
    PITON no acepta comentarios, así que los removemos antes de compilar.
    Se preserva la estructura de líneas para mantener el conteo de líneas.
    """
    import re
    lines = src.splitlines()
    result = []
    for line in lines:
        # Eliminar todo después de # (excepto dentro de strings)
        in_string = None
        cleaned = []
        i = 0
        while i < len(line):
            c = line[i]
            if in_string:
                cleaned.append(c)
                if c == in_string and (i == 0 or line[i-1] != '\\'):
                    in_string = None
            elif c in ('"', "'"):
                in_string = c
                cleaned.append(c)
            elif c == '#':
                break  # Comentario, resto de línea se ignora
            else:
                cleaned.append(c)
            i += 1
        result.append(''.join(cleaned))
    return '\n'.join(result)


def run_script_piton(path_str: str, timeout: float = 30.0) -> tuple[int, bytes, bytes, str | None]:
    """Ejecuta script con PITON. Devuelve (rc, stdout, stderr, error_type)."""
    src = Path(path_str).read_text(encoding="utf-8")
    # Traducir Python a PITON (keywords en espanol)
    piton_src = python_to_piton(src)
    
    with tempfile.TemporaryDirectory(prefix="piton-sec-run-") as directory:
        try:
            # Importar compile_native desde piton
            sys.path.insert(0, str(REPO))
            from piton.linux_x86 import compile_native_linux
            
            exe = compile_native_linux(piton_src, Path(directory) / "p")
            
            native = subprocess.run(
                [str(exe)],
                capture_output=True,
                timeout=timeout,
                cwd=directory,
                stdin=subprocess.DEVNULL,
            )
            return native.returncode, native.stdout, native.stderr, None
            
        except subprocess.TimeoutExpired:
            return -1, b"", b"", "TIMEOUT"
        except Exception as exc:
            exc_name = type(exc).__name__
            # Verificar si es un error de compilacion conocido
            if "NativeBuildError" in str(type(exc).__module__) or "ParseError" in str(type(exc).__name__):
                return 2, b"", str(exc).encode("utf-8"), exc_name
            return 2, b"", str(exc).encode("utf-8"), exc_name


def compare_results(cpython: subprocess.CompletedProcess, piton_rc: int, piton_out: bytes, piton_err: bytes) -> dict:
    """Compara resultados entre CPython y PITON."""
    c_rc = cpython.returncode
    c_out = cpython.stdout
    c_err = cpython.stderr
    
    result = {
        "cpython_rc": c_rc,
        "piton_rc": piton_rc,
        "cpython_stdout": c_out.decode("utf-8", "replace"),
        "piton_stdout": piton_out.decode("utf-8", "replace"),
        "diff": None,
    }
    
    # Caso 1: Ambos códigos de salida coinciden
    if piton_rc == c_rc:
        # Caso 1a: stdout coincide byte a byte
        if c_out == piton_out:
            result["status"] = EQUIV
            return result
        
        # Caso 1b: Ambos con error y excepcion coincide
        c_exc = _exception_name(c_err)
        p_exc = _exception_name(piton_err)
        if c_exc and p_exc and c_exc == p_exc:
            result["status"] = RAISING_EQ
            result["detail"] = c_exc
            return result
        
        # Caso 1c: DIVERGENTES (stdout diferente)
        result["status"] = DIVERGENT_MISMATCH
        # Encontrar primer diferencia
        c_lines = c_out.decode("utf-8", "replace").splitlines()
        p_lines = piton_out.decode("utf-8", "replace").splitlines()
        for i, (cl, pl) in enumerate(zip(c_lines, p_lines)):
            if cl != pl:
                result["diff"] = {
                    "line": i + 1,
                    "cpython": repr(cl),
                    "piton": repr(pl)
                }
                break
        if not result.get("diff") and len(c_lines) != len(p_lines):
            result["diff"] = {
                "line": min(len(c_lines), len(p_lines)) + 1,
                "note": f"line count mismatch: cpython={len(c_lines)}, piton={len(p_lines)}"
            }
        return result
    
    # Caso 2: Códigos de salida diferentes
    # PITON CRASH
    if piton_rc not in (0, 1):
        result["status"] = DIVERGENT_CRASH
        result["detail"] = f"piton rc={piton_rc} stderr={_short(piton_err)}"
        return result
    
    if c_rc not in (0, 1):
        result["status"] = "ORACLE_CRASH"
        result["detail"] = f"oracle rc={c_rc} stderr={_short(c_err)}"
        return result
    
    # Caso 3: Un lanza excepción, otro no
    c_exc = _exception_name(c_err)
    p_exc = _exception_name(piton_err)
    
    if c_exc or p_exc:
        if c_exc and p_exc:
            if c_exc == p_exc:
                result["status"] = RAISING_EQ
                result["detail"] = c_exc
            else:
                result["status"] = DIVERGENT_MISMATCH
                result["detail"] = f"cpython: {c_exc}, piton: {p_exc}"
        else:
            result["status"] = DIVERGENT_MISMATCH
            result["detail"] = f"one raises: cpython={c_exc}, piton={p_exc}"
        return result
    
    # Fallback
    result["status"] = DIVERGENT_MISMATCH
    result["detail"] = f"rc mismatch: cpython={c_rc}, piton={piton_rc}"
    return result


def run_corpus(script_dir: Path, timeout: float = 30.0) -> list[dict]:
    """Ejecuta todos los scripts en el corpus."""
    scripts = sorted(script_dir.glob("*.py"))
    results = []
    
    for script in scripts:
        record = {
            "script": str(script),
            "name": script.name,
        }
        
        try:
            # Ejecutar CPython (oracle)
            try:
                cpython = run_script_cpython(str(script), timeout)
                record["cpython_rc"] = cpython.returncode
                record["cpython_stdout"] = cpython.stdout.decode("utf-8", "replace")
                record["cpython_stderr"] = cpython.stderr.decode("utf-8", "replace")
            except subprocess.TimeoutExpired:
                record["status"] = TIMEOUT
                record["detail"] = "oracle timeout"
                results.append(record)
                continue
            except Exception as exc:
                record["status"] = RUNNER_ERROR
                record["detail"] = f"oracle error: {type(exc).__name__}: {exc}"
                results.append(record)
                continue
            
            # Ejecutar PITON
            try:
                piton_rc, piton_out, piton_err, piton_exc = run_script_piton(str(script), timeout)
                record["piton_rc"] = piton_rc
                record["piton_stdout"] = piton_out.decode("utf-8", "replace")
                record["piton_stderr"] = piton_err.decode("utf-8", "replace")
            except subprocess.TimeoutExpired:
                record["status"] = TIMEOUT
                record["detail"] = "native timeout"
                results.append(record)
                continue
            except Exception as exc:
                record["status"] = RUNNER_ERROR
                record["detail"] = f"native error: {type(exc).__name__}: {exc}"
                results.append(record)
                continue
            
            if piton_exc:
                record["status"] = piton_exc
                record["detail"] = piton_err.decode("utf-8", "replace")[:200]
                results.append(record)
                continue
            
            # Comparar resultados
            comp = compare_results(cpython, piton_rc, piton_out, piton_err)
            record["status"] = comp["status"]
            if "diff" in comp:
                record["diff"] = comp["diff"]
            if "detail" in comp:
                record["detail"] = comp["detail"]
                
        except Exception as exc:
            record["status"] = RUNNER_ERROR
            record["detail"] = str(exc)[:200]
        
        results.append(record)
    
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", metavar="PATH", help="ruta para volcar el resultado completo en JSON")
    parser.add_argument("--dir", metavar="PATH", default=str(REPO / "corpus_sec" / "stdlib"),
                        help="directorio con los scripts del corpus (default: corpus_sec/stdlib/)")
    parser.add_argument("--timeout", type=float, default=30.0, help="timeout de ejecucion en segundos")
    args = parser.parse_args()
    
    script_dir = Path(args.dir)
    if not script_dir.exists():
        print(f"ERROR: directorio no existe: {script_dir}", file=sys.stderr)
        return 1
    
    results = run_corpus(script_dir, args.timeout)
    
    # Contar buckets
    buckets = {}
    for r in results:
        status = r.get("status", "UNKNOWN")
        buckets[status] = buckets.get(status, 0) + 1
    
    # Imprimir tabla
    print(f"\n{'Script':<30} | {'Estado':<20} | Detalle")
    print("-" * 80)
    for r in results:
        name = r.get("name", r.get("script", "unknown"))
        status = r.get("status", "UNKNOWN")
        detail = r.get("detail", "")
        if detail:
            detail = detail[:40].replace("\n", " ")
        print(f"{name:<30} | {status:<20} | {detail}")
    
    # Resumen
    total = len(results)
    equiv = buckets.get(EQUIV, 0)
    raising = buckets.get(RAISING_EQ, 0)
    honest = equiv + raising
    diverge = buckets.get(DIVERGENT_MISMATCH, 0) + buckets.get(DIVERGENT_CRASH, 0)
    
    print(f"\nResumen:")
    print(f"  Total: {total}")
    print(f"  EQUIV: {equiv}")
    print(f"  RAISE_EQ: {raising}")
    print(f"  Honesto (EQUIV+RAISE_EQ): {honest}")
    print(f"  DIVERGENT: {diverge}")
    print(f"  TIMEOUT: {buckets.get(TIMEOUT, 0)}")
    print(f"  COMPILE_ERROR: {buckets.get(COMPILE_ERROR, 0)}")
    
    # JSON output
    output = {
        "meta": {
            "oracle": f"CPython {sys.version.split()[0]}",
            "total": total,
            "equiv": equiv,
            "raising_eq": raising,
            "divergent": diverge,
            "buckets": buckets,
        },
        "results": results,
    }
    
    if args.json:
        Path(args.json).write_text(json.dumps(output, indent=2, ensure_ascii=False))
        print(f"\nJSON -> {args.json}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())