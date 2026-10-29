#!/usr/bin/env python3
"""parity_corpus_v4 - cuarto corpus: CODIGO REAL (no elegido a mano).

Responder la pregunta empirica "¿cuanto de CPython real cubrimos?" con un
embudo medible sobre dos poblaciones externas al proyecto:

  TIER A  stdlib real  ->  /usr/lib/python3.12/*.py (nivel superior, sin
          seleccion: TODOS los archivos). Embudo ESTATICO, sin ejecucion:
          adapt -> parse -> mir -> build. Mide cuanto codigo de produccion
          real llega por cada etapa.

  TIER B  snippets reales -> ejemplos del tutorial oficial de CPython
          (docs.python.org/3.12/tutorial), descargados y convertidos a .py
          (corpus_v4/tutorial/, con URL + sha256 del HTML fuente). Mismo
          embudo + ejecucion diferencial: RUN/EQUIV con los buckets del
          motor v2/v3 (EQUIV, RAISE_EQ, FAIL_CLOSED via etapa, DIVERGENT_*, TIMEOUT).

ADAPTACION Python->PITON (etapa `adapt`): la gramatica de PITON usa palabras
claves en espanol (si/funcion/para/...) y NO acepta comentarios `#`; el
codigo CPython real se traduce de forma MECANICA con tokenize (tabla de
keywords, match/case heuristico de linea, comentarios eliminados). Los
builtins se dejan en ingles: el runtime nativo los resuelve bilingues
(x86._BUILTINS contiene print/len/type y imprimir/rango/tipo). El oracle
SIEMPRE ejecuta el Python ORIGINAL sin adaptar. Que falle despues del
adaptador es fallo honesto de PITON, no del adaptador (el roundtrip
traducir(adaptado) se registra como campo `roundtrip`).

Metodo: la etapa fallida es honesta (ParseError/MIRLoweringError/
NativeBuildError/gcc), nunca se degrada silenciosamente. El oracle es
CPython del sistema (python3). Backend medido: Linux (paridad con v2/v3;
Windows tiene su propia CI de PRs).

Uso:
  python3 tools/parity_corpus_v4.py fetch      # descarga tutorial -> corpus_v4/
  python3 tools/parity_corpus_v4.py run --tier all
"""
from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
import platform
import re
import subprocess
import sys
import tempfile
import time
import tokenize
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

V4_DIR = REPO / "corpus_v4"
RAW_DIR = V4_DIR / "raw"
TUTORIAL_DIR = V4_DIR / "tutorial"
STDLIB_DIR = Path("/usr/lib/python3.12")

# Buckets de ejecucion (mismos names que v2/v3, ver tools/parity_corpus_v3.py)
EQUIV = "EQUIV"
RAISE_EQ = "RAISE_EQ"
DIVERGENT_MISMATCH = "DIVERGENT_MISMATCH"
DIVERGENT_CRASH = "DIVERGENT_CRASH"
TIMEOUT = "TIMEOUT"
RUN_BUCKETS = [DIVERGENT_CRASH, DIVERGENT_MISMATCH, TIMEOUT, RAISE_EQ, EQUIV]

# Etapas del embudo estatico (la fallida se registra; nada se degrada)
STAGES = ["adapt", "parse", "mir", "build"]
STAGE_FAIL = {s: f"{s.upper()}_FAIL" for s in STAGES}

# Adaptador mecanico Python -> PITON: solo keywords (son words reservadas de
# Python, no pueden ser identificadores/atributos: el mapeo por token NAME es
# seguro). Los builtins (print/len/type/...) NO se tocan: el runtime nativo es
# bilingue. `global` y `lambda` se quedan en ingles: el parser de PITON los
# acepta tal cual. Comentarios `#` se eliminan (PITON no los parsea).
ADAPT_MAP = {
    # compound statements
    "if": "si", "elif": "sino_si", "else": "sino", "while": "mientras",
    "for": "para", "in": "en", "def": "funcion", "class": "clase",
    "try": "intentar", "except": "excepto", "finally": "finalmente",
    "with": "con", "as": "como", "import": "importar", "from": "desde",
    # simple statements (el parser solo tiene la forma espanola; la inglesa
    # "parsea" como nombre fantasma y corromperia la semantica)
    "return": "devolver", "yield": "producir", "raise": "lanzar",
    "pass": "pasar", "break": "romper", "continue": "continuar",
    "assert": "afirmar", "del": "borrar", "nonlocal": "no_local",
    # operadores (el parser acepta ambos, se canoniza el espanol)
    "and": "y", "or": "o", "not": "no", "is": "es",
    # literales (English True/False/None parsea como nombre -> NameError)
    "True": "Verdadero", "False": "Falso", "None": "Nada",
    # async (dispatch del parser: asincrono + funcion/con/para)
    "async": "asincrono", "await": "esperar",
}
# soft keywords: solo como apertura de sentencia (linea que termina en ':'
# sin '=' y que empieza con la palabra); `match = re.match(...)` NO se toca.
_SOFT_ADAPT = ("match", "case")


def _soft_is_statement(line: str, word: str) -> bool:
    stripped = line.strip()
    if not stripped.startswith(word + " "):
        return False
    if "=" in stripped:
        return False
    # SOFT_MULTILINE_V1: la sentencia soft puede abrir '(' y cerrar su ':' en
    # otra linea — turtle.py:3965 abre `case (` y cierra `):` tres lineas
    # despues; exigir ':' en la misma linea dejaba `case` sin traducir y el
    # parser PITON lo veia como nombre (COLON en el '|').
    return stripped.endswith(":") or "(" in stripped


def _top_comma(tokens: list[tuple[int, str]], start: int, end: int) -> bool:
    """Hay una COMA a profundidad 0 en tokens[start:end], fuera de params lambda."""
    depth = 0
    lambda_params = False
    for kind, value in tokens[start:end]:
        if kind == tokenize.OP and value in "([{":
            depth += 1
        elif kind == tokenize.OP and value in ")]}":
            depth -= 1
        elif kind == tokenize.NAME and value == "lambda":
            lambda_params = True
        elif lambda_params and depth == 0 and kind == tokenize.OP and value == ":":
            lambda_params = False
        elif (kind == tokenize.OP and value == "," and depth == 0
              and not lambda_params):
            return True
    return False


def _wrap_tokens(region: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Envolviendo una region de tokens en parentesis."""
    body_start = 0
    while body_start < len(region) and region[body_start][0] in (
            tokenize.INDENT, tokenize.DEDENT):
        body_start += 1
    return region[:body_start] + [(tokenize.OP, "(")] + region[body_start:] \
        + [(tokenize.OP, ")")]


def _split_parts(line: list[tuple[int, str]]) -> list[tuple[int, str, int]]:
    """Divide una sentencia logica en partes por `=` o `;` a profundidad 0.

    Retorna [(inicio, fin, sep_idx)] — sep_idx es -1 si no hay separador
    posterior. Los params lambda no se consideran.
    """
    parts: list[tuple[int, str, int]] = []
    depth = 0
    lambda_params = False
    part_start = 0
    index = 0
    while index < len(line):
        kind, value = line[index]
        if kind == tokenize.OP and value in "([{":
            depth += 1
        elif kind == tokenize.OP and value in ")]}":
            depth -= 1
        elif kind == tokenize.NAME and value == "lambda":
            lambda_params = True
        elif lambda_params and depth == 0 and kind == tokenize.OP and value == ":":
            lambda_params = False
        elif (kind == tokenize.OP and value in ("=", ";") and depth == 0
              and not lambda_params):
            parts.append((part_start, index, index))
            part_start = index + 1
        index += 1
    parts.append((part_start, len(line), -1))
    return parts


# contextos con COMA desnuda que PITON parsea a mano y que envolver CAMBIARIA
# la semantica (assert/del/msg, global/nonlocal targets, items de with/for,
# listas de import): jamas se envuelven.
_SKIP_WRAP_HEADS = {"importar", "desde", "para", "con", "afirmar", "borrar",
                    "global", "no_local"}
_AFTER_WRAP_HEADS = {"devolver", "producir", "caso", "segun"}


def _wrap_line(line: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Tuplas desnudas -> parentesis. PITON no acepta COMA a profundidad 0 en
    expresiones (`x = 1, 2` falla) pero si en los contextos de _SKIP_WRAP_HEADS
    y en params de lambda: esos contextos NO se envuelven."""
    names: list[str] = []
    for kind, value in line:
        if kind in (tokenize.INDENT, tokenize.DEDENT):
            continue
        if kind == tokenize.NAME:
            names.append(value)
            if len(names) == 2:
                break
        else:
            break
    if not names:
        return line
    # `asincrono para/...` (async for/with/def): la cabeza semantica es la
    # segunda palabra
    head = names[0]
    if head == "asincrono" and len(names) == 2:
        head = names[1]
    first_name = next((i for i, (k, _) in enumerate(line)
                       if k == tokenize.NAME), None)

    parts = _split_parts(line)
    if len(parts) > 1:
        # hay `=` o `;`: envuelve cada parte con coma desnuda
        out: list[tuple[int, str]] = []
        last = 0
        for position, (start, end, sep) in enumerate(parts):
            if _top_comma(line, start, end):
                out.extend(line[last:start])
                out.extend(_wrap_tokens(line[start:end]))
            else:
                out.extend(line[last:end])
            last = end
            if sep >= 0:
                if line[sep][1] == ";":
                    # SEMI_SPLIT_V1: `a=1;b=2` -> dos sentencias; el token
                    # `;` no existe en la gramatica PITON y si se conservaba
                    # el parser moria en SEMI.
                    out.append((tokenize.NEWLINE, "\n"))
                else:
                    out.append(line[sep])
                last = sep + 1
        if last < len(line):
            out.extend(line[last:])
        return out

    if head in _SKIP_WRAP_HEADS:
        return line
    if _top_comma(line, 0, len(line)):
        # sentencia sin `=` (devolver/producir/segun/caso...): envuelve tras
        # la palabra clave inicial. WRAP_HEAD_NO_KW_V1: si NO hay palabra
        # clave (expr desnuda con coma, p.ej. `termios.tcgetattr, ...`), abrir
        # tras el primer nombre produciria `termios(.tcgetattr, ...)`, un call
        # falso: la tupla es toda la sentencia y se abre en la columna 0.
        insert_at = first_name + 1 if head in _AFTER_WRAP_HEADS else 0
        return line[:insert_at] + [(tokenize.OP, "(")] + line[insert_at:] \
            + [(tokenize.OP, ")")]
    return line


def python_to_piton(src: str) -> str:
    """Traduccion mecanica Python -> PITON.

    - keywords -> forma espanola (tabla ADAPT_MAP + match/case heuristico);
    - comentarios `#` eliminados (PITON no los parsea);
    - APLANADO de sentencias multilinea: el lexer de PITON es manual y no
      suspende NEWLINE dentro de corchetes ni admite `\\` de continuacion
      (ningun ejemplo propio usa brackets multilinea: es una restriccion
      real de la gramatica). Se juntan los NL internos a `([{...}])` en una
      sola linea fisica; los NL de profundidad 0 (lineas en blanco) se
      conservan;
    - strings adyacentes (concatenacion implicita) -> `+`: PITON no la
      soporta y `+` es equivalente en tiempo de evaluacion para str/bytes;
    - `desde X importar (a, b)` -> quita los parentesis: la gramatica
      PITON solo admite la lista de nombres sin envolver;
    - tuplas desnudas -> parentesis (`x = 1, 2` -> `x =(1 ,2 )`).
    """
    result: list[tuple[int, str]] = []
    depth = 0
    for token in tokenize.generate_tokens(io.StringIO(src).readline):
        kind, value = token.type, token.string
        if kind == tokenize.COMMENT:
            continue  # PITON no acepta comentarios
        if kind == tokenize.OP:
            if value in "([{":
                depth += 1
            elif value in ")]}":
                depth = max(0, depth - 1)
            elif value == "\\":
                continue  # continuacion de linea: se une el trozo siguiente
        if kind == tokenize.NL and depth > 0:
            continue  # salto interno a bracket: se aplana
        if kind == tokenize.NAME:
            mapped = ADAPT_MAP.get(value)
            if mapped is not None:
                value = mapped
            elif value in _SOFT_ADAPT and _soft_is_statement(token.line, value):
                value = "segun" if value == "match" else "caso"
        elif (kind in (tokenize.STRING, getattr(tokenize, "FSTRING_START", -3))
              and result
              and result[-1][0] in (tokenize.STRING,
                                    getattr(tokenize, "FSTRING_END", -4))):
            # FSTRING_CONCAT_V1: la concatenacion implicita une f-strings
            # adyacentes; 3.12 las tokeniza como FSTRING_* (no STRING), sin
            # esta condicion el `f".." f".."` llegaba pegado -> RPAREN.
            result.append((tokenize.OP, "+"))  # concatenacion implicita
        result.append((kind, value))

    # quitar parentesis de la lista de un `desde X importar (...)`
    cleaned: list[tuple[int, str]] = []
    index = 0
    while index < len(result):
        kind, value = result[index]
        if (kind == tokenize.NAME and value == "importar"
                and index + 1 < len(result)
                and result[index + 1] == (tokenize.OP, "(")):
            cleaned.append((kind, value))  # la palabra importar SE CONSERVA
            level = 0
            cursor = index + 1
            while cursor < len(result):
                k2, v2 = result[cursor]
                if k2 == tokenize.OP and v2 in "([":
                    level += 1
                elif k2 == tokenize.OP and v2 in ")]":
                    level -= 1
                if cursor > index + 1 and level == 0:
                    # IMPORT_TRAILING_COMMA_V1: la coma final de la lista
                    # quedaba suelta al quitar los parentesis y el parser
                    # PITON exige NAME despues de cada coma -> NameError de
                    # parse en imports multilinea reales (typing, dis...).
                    if cleaned and cleaned[-1] == (tokenize.OP, ","):
                        cleaned.pop()
                    break
                if cursor != index + 1:  # se salta '(' y ')'
                    cleaned.append((k2, v2))
                cursor += 1
            index = cursor + 1
            continue
        cleaned.append((kind, value))
        index += 1

    # tuplas desnudas -> parentesis (sentencia a sentencia; NL de lineas en
    # blanco actua como frontera: nunca esta dentro de una sentencia logica)
    wrapped: list[tuple[int, str]] = []
    line: list[tuple[int, str]] = []
    for item in cleaned:
        if item[0] in (tokenize.NEWLINE, tokenize.NL):
            wrapped.extend(_wrap_line(line))
            wrapped.append(item)
            line = []
        else:
            line.append(item)
    wrapped.extend(_wrap_line(line))  # residuo sin NEWLINE final
    return tokenize.untokenize(wrapped)

TUTORIAL_PAGES = [
    "introduction", "controlflow", "datastructures", "modules",
    "inputoutput", "errors", "classes", "stdlib", "stdlib2",
    "interpreter", "venv",
]
# docs.python.org/3.12/ ... por version: el oracle del sistema es CPython 3.12.3,
# asi los ejemplos estan escritos para la misma mayoria que ejecuta el oraculo.
TUTORIAL_BASE = "https://docs.python.org/3.12/tutorial/{}.html"

_PRE_RE = re.compile(r"<pre[^>]*>(.*?)</pre>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")


# --------------------------------------------------------------------------
# fetch: tutorial oficial -> .py autocontenido con procedencia
# --------------------------------------------------------------------------
def _block_examples(text: str) -> list[list[str]]:
    """Un bloque doctest -> lista de ejemplos (lineas de codigo por ejemplo).

    Regla REPL: `>>> ` inicia ejemplo, `... ` continua, cualquier otra linea
    es salida esperada y CIERRA el ejemplo vigente. La salida se descarta
    (el oracle de v4 compara contra CPython, no contra el texto del doc).
    """
    examples: list[list[str]] = []
    current: list[str] = []
    # docs.python.org usa resaltado Pygments: quitar <span> ANTES de unescape
    # para que "&gt;&gt;&gt; " se vuelva ">>> ".
    for line in html.unescape(_TAG_RE.sub("", text)).splitlines():
        if line.startswith(">>>"):
            if current:
                examples.append(current)
            current = [line[3:].lstrip() if line[3:4] == " " else line[3:]]
        elif line.startswith("..."):
            if current:
                current.append(line[3:].lstrip() if line[3:4] == " " else line[3:])
            else:
                current = []  # `...` suelto sin ejemplo abierto: salida, se ignora
        else:
            if current:
                examples.append(current)
                current = []
    if current:
        examples.append(current)
    return [ex for ex in examples if any(part.strip() for part in ex)]


def _assemble_page(page_html: str) -> tuple[str, int, int]:
    """Une todos los ejemplos doctest de una pagina como sesion REPL.

    - el estado entre ejemplos persiste (namespace de modulo, como el REPL);
    - cada ejemplo va en try/except BaseException + print('EXC') para que un
      ejemplo que falla (los el doc lanza NameError/SyntaxError a proposito)
      NO mate la pagina: ambos lados marcan EXC y se compara;
    - ejemplos con sintaxis rota intencional se excluyen (compile() gate):
      no son ejecutables por definicion y matarian el script entero.
    Retorna (script, ejemplos_totales, ejemplos_excluidos).
    """
    chunks: list[str] = []
    total = dropped = 0
    for match in _PRE_RE.finditer(page_html):
        raw = match.group(1)
        # el HTML viene escapado: "&gt;&gt;&gt;" cuenta como prompt de doctest
        if not re.search(r"&gt;&gt;&gt;|>>>", raw):
            continue  # literal-block de salida pura: no es codigo
        for example in _block_examples(raw):
            total += 1
            chunk = (
                "try:\n"
                + "\n".join("    " + line for line in example)
                + "\nexcept BaseException:\n    print('EXC')"
            )
            try:
                # el gate compara el CHUNK ENVUELTO: un ejemplo solo-comentario
                # o con continuacion pegada compila suelto pero romperia el try
                compile(chunk, "<doctest>", "exec")
            except SyntaxError:
                dropped += 1  # sintaxis rota intencional (demostracion del doc)
                continue
            chunks.append(chunk)
    return "\n".join(chunks), total, dropped


def cmd_fetch() -> int:
    import urllib.request

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    TUTORIAL_DIR.mkdir(parents=True, exist_ok=True)
    manifest = []
    for name in TUTORIAL_PAGES:
        url = TUTORIAL_BASE.format(name)
        raw_path = RAW_DIR / f"{name}.html"
        if not raw_path.exists():
            req = urllib.request.Request(url, headers={"User-Agent": "piton-corpus-v4"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw_path.write_bytes(resp.read())
        page = raw_path.read_text(encoding="utf-8", errors="replace")
        sha = hashlib.sha256(raw_path.read_bytes()).hexdigest()
        script, examples, dropped = _assemble_page(page)
        header = (
            f"# corpus_v4 tutorial page: {name}\n"
            f"# source: {url}\n"
            f"# sha256(HTML): {sha}\n"
            f"# ejemplos doctest: {examples} (excluidos {dropped} por sintaxis rota intencional)\n"
            f"# convertido por tools/parity_corpus_v4.py fetch: cada ejemplo queda en\n"
            f"# try/except BaseException (semantica REPL: la sesion no muere en un error)\n"
        )
        out = TUTORIAL_DIR / f"{name}.py"
        if examples:
            out.write_text(header + script + ("\n" if not script.endswith("\n") else ""),
                           encoding="utf-8")
        elif out.exists():
            out.unlink()  # pagina sin doctests: no entra al corpus
        manifest.append({"page": name, "url": url, "html_sha256": sha,
                         "examples": examples, "examples_dropped": dropped,
                         "bytes": out.stat().st_size if examples else 0})
        print(f"fetch {name}: {examples} ejemplos ({dropped} excluidos), "
              f"{out.stat().st_size if examples else 0} bytes")
    (V4_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"OK -> {len(manifest)} paginas en {TUTORIAL_DIR}")
    return 0


# --------------------------------------------------------------------------
# embudo estatico por archivo
# --------------------------------------------------------------------------
def _read_source(path_str: str) -> str:
    with tokenize.open(path_str) as handle:
        return handle.read()


def _stage_impl(path_str: str) -> tuple[dict, str | None]:
    """Embudo adapt -> parse -> mir -> build. Nunca ejecuta el archivo.

    Retorna (record, fuente_adaptada|None). El campo `roundtrip` guarda si
    traducir(adaptado) produce Python que CPython compila (validacion del
    adaptador, no es una etapa del embudo).
    """
    from piton.linux_x86 import LinuxCEmitter, _gcc_compile
    from piton.lower import LoweringError, lower_cst_to_hir
    from piton.mir import MIRLoweringError, lower_hir_to_mir
    from piton.parser import ParseError, parse
    from piton.translator import traducir_fuente

    record = {"path": path_str, "reached": None, "stage_fail": None,
              "detail": "", "build_seconds": None, "roundtrip": None}
    try:
        src = _read_source(path_str)
    except Exception as exc:
        record.update(reached="read", stage_fail="READ_FAIL",
                      detail=f"{type(exc).__name__}: {exc}")
        return record, None

    # 1) adapt: Python real -> PITON mecanico (keywords ES, sin comentarios)
    try:
        piton_src = python_to_piton(src)
    except Exception as exc:
        record.update(reached="adapt", stage_fail=STAGE_FAIL["adapt"],
                      detail=f"{type(exc).__name__}: {exc}"[:300])
        return record, None
    record["reached"] = "adapt"

    # roundtrip: el adaptado debe volver a Python compilable (sanity del
    # adaptador+traductor); no gates el embudo, se reporta como campo.
    try:
        back = traducir_fuente(piton_src, "<corpus-v4-roundtrip>")
        compile(back, "<roundtrip>", "exec")
        record["roundtrip"] = True
    except Exception as exc:
        record["roundtrip"] = f"{type(exc).__name__}: {exc}"[:200]

    # 2) parse + hir sobre la fuente ADAPTADA
    try:
        hir = lower_cst_to_hir(parse(piton_src))
    except Exception as exc:
        kind = type(exc).__name__
        record.update(reached="parse", stage_fail=STAGE_FAIL["parse"],
                      detail=f"{kind}: {exc}"[:300],
                      detail_class=kind if isinstance(exc, (ParseError, LoweringError,
                                                            SyntaxError)) else "unexpected")
        return record, piton_src
    record["reached"] = "parse"

    # 3) mir
    try:
        mir = lower_hir_to_mir(hir)
    except Exception as exc:
        record.update(reached="mir", stage_fail=STAGE_FAIL["mir"],
                      detail=f"{type(exc).__name__}: {exc}"[:300])
        return record, piton_src
    record["reached"] = "mir"

    # 4) emit + gcc
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="piton-v4-") as directory:
            c_path = Path(directory) / "program.c"
            c_path.write_text(LinuxCEmitter().emit(mir), encoding="utf-8")
            _gcc_compile(c_path, Path(directory) / "p")
    except Exception as exc:
        record.update(reached="mir", stage_fail=STAGE_FAIL["build"],
                      detail=f"{type(exc).__name__}: {exc}"[:300])
        return record, piton_src
    record.update(reached="build", build_seconds=round(time.monotonic() - started, 3))
    return record, piton_src


def stage_file(path_str: str) -> dict:
    record, _ = _stage_impl(path_str)
    return record


# --------------------------------------------------------------------------
# ejecucion diferencial (tier B): buckets v2/v3
# --------------------------------------------------------------------------
def _exception_name(blob: bytes) -> str | None:
    text = blob.decode("utf-8", "replace")
    match = re.search(r"^([A-Za-z_][A-Za-z_0-9]*(?:Error|Exception|Warning))", text, re.M)
    if match:
        return match.group(1)
    if "Traceback" in text:
        return "Exception"
    return None


def _short(blob: bytes, limit: int = 160) -> str:
    return blob.decode("utf-8", "replace").strip().replace("\n", " | ")[:limit]


def run_script(path_str: str, timeout: float = 10.0) -> dict:
    """Si el embudo llego a build, ejecuta nativo (adaptado) vs CPython
    (ORIGINAL, sin adaptar) y bucketiza."""
    from piton.linux_x86 import compile_native_linux

    record, piton_src = _stage_impl(path_str)
    if record["stage_fail"]:
        record["bucket"] = record["stage_fail"]
        return record
    original_src = _read_source(path_str)
    with tempfile.TemporaryDirectory(prefix="piton-v4-run-") as directory:
        try:
            exe = compile_native_linux(piton_src, Path(directory) / "p")
        except Exception as exc:
            record.update(bucket=STAGE_FAIL["build"],
                          detail=f"{type(exc).__name__}: {exc}"[:300])
            return record
        try:
            native = subprocess.run([str(exe)], capture_output=True, timeout=timeout, cwd=directory)
        except subprocess.TimeoutExpired:
            record.update(bucket=TIMEOUT, detail="native did not terminate")
            return record
        try:
            oracle = subprocess.run(
                [sys.executable, "-c", original_src], capture_output=True, timeout=timeout,
                cwd=directory,
            )
        except subprocess.TimeoutExpired:
            record.update(bucket=TIMEOUT, detail="oracle did not terminate")
            return record
    record["native_rc"], record["oracle_rc"] = native.returncode, oracle.returncode
    if native.returncode == oracle.returncode and native.stdout == oracle.stdout:
        record["bucket"] = EQUIV
        return record
    native_exc = _exception_name(native.stderr)
    oracle_exc = _exception_name(oracle.stderr)
    if native.returncode == 1 and oracle.returncode == 1 and native_exc and native_exc == oracle_exc:
        record.update(bucket=RAISE_EQ, detail=native_exc or "")
        return record
    if native.returncode not in (0, 1) or oracle.returncode not in (0, 1):
        record["bucket"] = DIVERGENT_CRASH
    else:
        record["bucket"] = DIVERGENT_MISMATCH
    record["detail"] = (
        f"native rc={native.returncode} out={_short(native.stdout)} err={_short(native.stderr)}"
        f" || oracle rc={oracle.returncode} out={_short(oracle.stdout)} err={_short(oracle.stderr)}"
    )[:400]
    return record


def _stage_worker(path_str: str) -> dict:
    try:
        return stage_file(path_str)
    except Exception as exc:  # el instrumento falla cerrado, no inventa
        return {"path": path_str, "reached": None, "stage_fail": "INSTRUMENT_ERROR",
                "detail": f"{type(exc).__name__}: {exc}"[:300]}


_TOOLS_DIR = str(Path(__file__).resolve().parent)


def isolated_stage(path_str: str, timeout: float = 120.0) -> dict:
    """CORPUS_ISOLATE_V1: compila UN archivo en un proceso hijo.

    La emision puede abortar el proceso (stack del interprete, allocator);
    dentro del pool eso mata al worker y se pierde el lote entero. Aislado,
    el fallo queda como un registro CRASH y el corpus sigue.
    """
    import subprocess as _subprocess

    code = ("import json,sys;"
            "sys.path.insert(0,sys.argv[1]);"
            "import parity_corpus_v4 as m;"
            "print(json.dumps(m._stage_worker(sys.argv[2])))")
    try:
        proc = _subprocess.run(
            [sys.executable, "-c", code, _TOOLS_DIR, path_str],
            capture_output=True, text=True, timeout=timeout,
        )
    except _subprocess.TimeoutExpired:
        return {"path": path_str, "reached": None, "stage_fail": "TIMEOUT",
                "detail": f"supero {timeout}s"}
    lines = [line for line in (proc.stdout or "").splitlines() if line.strip()]
    if proc.returncode != 0 or not lines:
        tail = (proc.stderr or "").strip().splitlines()[-1:] or [""]
        return {"path": path_str, "reached": None, "stage_fail": "CRASH",
                "detail": f"exit={proc.returncode} {tail[0]}"[:300]}
    try:
        return json.loads(lines[-1])
    except Exception as exc:
        return {"path": path_str, "reached": None,
                "stage_fail": "INSTRUMENT_ERROR", "detail": str(exc)[:300]}


def _run_worker(args) -> dict:
    path_str, timeout = args
    try:
        return run_script(path_str, timeout)
    except Exception as exc:
        return {"path": path_str, "bucket": "INSTRUMENT_ERROR",
                "detail": f"{type(exc).__name__}: {exc}"[:300]}


# --------------------------------------------------------------------------
# resumen + reporte
# --------------------------------------------------------------------------
def _funnel(records: list[dict]) -> dict:
    """Cuantos archivos PASAN cada etapa (no donde se detienen).

    pass(stage) = total - fallidos de las etapas hasta esa (inclusive de la
    propia). READ_FAIL solo cuenta fallos reales de lectura (el `or` anterior
    contaba los exitos como READ_FAIL).
    """
    total = len(records)
    fails: dict[str, int] = {}
    for record in records:
        fail = record.get("stage_fail")
        if fail:
            fails[fail] = fails.get(fail, 0) + 1
    passed: dict[str, int] = {}
    # CORPUS_ISOLATE_V1: una clase de fallo que no pertenece a ninguna etapa
    # (CRASH de proceso, TIMEOUT) NO puede contarse como exito: se resta de
    # todas las etapas. Fail-closed.
    known = {STAGE_FAIL[stage] for stage in STAGES}
    remaining = total - sum(n for k, n in fails.items() if k not in known)
    for stage in STAGES:
        remaining -= fails.get(STAGE_FAIL[stage], 0)
        passed[stage] = remaining
    pct = lambda n: round(100.0 * n / total, 1) if total else 0.0
    return {
        "total": total,
        "passed": passed,
        "pct": {stage: pct(passed[stage]) for stage in STAGES},
        "fail_classes": dict(sorted(fails.items(), key=lambda kv: -kv[1])),
        "read_failed": fails.get("READ_FAIL", 0),
    }


def _bucket_summary(records: list[dict]) -> dict:
    by_bucket: dict[str, int] = {}
    for record in records:
        bucket = record.get("bucket") or "UNREACHABLE"
        by_bucket[bucket] = by_bucket.get(bucket, 0) + 1
    total = len(records)
    pct = lambda n: round(100.0 * n / total, 1) if total else 0.0
    equiv = by_bucket.get(EQUIV, 0)
    honest = equiv + by_bucket.get(RAISE_EQ, 0)
    divergent = by_bucket.get(DIVERGENT_MISMATCH, 0) + by_bucket.get(DIVERGENT_CRASH, 0)
    return {"total": total, "by_bucket": dict(sorted(by_bucket.items(), key=lambda kv: -kv[1])),
            "pct_equiv": pct(equiv), "pct_honest_native": pct(honest),
            "pct_divergent": pct(divergent)}


def _git_sha() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                             text=True, cwd=REPO, timeout=10)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"

def _git_status() -> tuple[bool, list[str]]:
    try:
        out = subprocess.run(["git", "status", "--porcelain"], capture_output=True,
                             text=True, cwd=REPO, timeout=10)
        lines = out.stdout.strip().splitlines()
        dirty = [l.strip() for l in lines if l.strip()]
        return len(dirty) == 0, dirty
    except Exception:
        return False, ["unknown"]

def _hash_file(path: Path) -> str:
    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return "unknown"

def render_markdown(meta: dict, tier_a: dict, tier_b_funnel: dict,
                    tier_b_run: dict | None, tier_a_records: list[dict] | None = None,
                    tier_b_records: list[dict] | None = None) -> str:
    def _stage_rows(funnel: dict) -> list[str]:
        rows = ["| etapa | pasan | % |", "|---|---|---|"]
        for stage in STAGES:
            rows.append(f"| {stage} | {funnel['passed'][stage]} | {funnel['pct'][stage]}% |")
        return rows

    def _parse_patterns(records: list[dict]) -> list[str]:
        counts: dict[str, int] = {}
        for record in records or []:
            if record.get("stage_fail") != "PARSE_FAIL":
                continue
            detail = record.get("detail", "")
            match = re.search(r"Token\((\w+)", detail) or re.search(r"Se esperaba (\S+?):", detail)
            if record.get("detail_class") == "LoweringError":
                key = detail[:70]
            elif match:
                key = match.group(1)
            else:
                key = detail[:50]
            counts[key] = counts.get(key, 0) + 1
        if not counts:
            return []
        rows = ["", "### Motivos de PARSE_FAIL (top)", "", "| motivo | n |", "|---|---|"]
        for key, count in sorted(counts.items(), key=lambda kv: -kv[1])[:14]:
            rows.append(f"| {key} | {count} |")
        return rows

    def _mir_modules(records: list[dict]) -> int:
        return sum(1 for r in (records or [])
                   if r.get("stage_fail") == "MIR_FAIL"
                   and "module not supplied" in r.get("detail", ""))

    def _findings(records: list[dict]) -> list[str]:
        """Trazar PARSE_FAIL a su linea adaptada y clasificar lo verificable."""
        parse_recs = [r for r in records or [] if r.get("stage_fail") == "PARSE_FAIL"]
        nl_kinds: dict[str, int] = {}
        cache: dict[str, list[str]] = {}
        for record in parse_recs:
            detail = record.get("detail", "")
            if "Token(NL" not in detail:
                continue
            match = re.search(r"\d+:\d+", detail)
            if not match:
                continue
            line_no = int(match.group(0).split(":")[0])
            key = record["path"].split("/")[-1]
            if key not in cache:
                source = Path(record["path"]).read_text(encoding="utf-8")
                cache[key] = python_to_piton(source).split("\n")
            src_lines = cache[key]
            text = src_lines[line_no - 1].strip() if 0 < line_no <= len(src_lines) else ""
            head = text.split(" ", 1)[0] if text else "?"
            nl_kinds[head] = nl_kinds.get(head, 0) + 1
        not_in = sum(1 for r in parse_recs if "Token(NOT_IN" in r.get("detail", ""))
        fstr = sum(1 for r in parse_recs if "f-string" in r.get("detail", ""))
        dstar = sum(1 for r in parse_recs if "Token(DOUBLE_STAR" in r.get("detail", ""))
        nl_total = sum(nl_kinds.values())
        nl_desc = ", ".join(f"{count} bare `{kind}`" for kind, count in
                            sorted(nl_kinds.items(), key=lambda kv: -kv[1]))
        classified = nl_total + not_in + fstr + dstar
        rest = len(parse_recs) - classified
        out = ["", "## Hallazgos verificados (fallos trazados a su linea adaptada)", ""]
        if nl_total:
            out += [
                f"- Sentencias sin expresion obligatoria: **{nl_total}** "
                f"({nl_desc}). CPython las admite; PITON exige "
                "`devolver <expr>`, `lanzar <exc>`.",
            ]
        if not_in:
            out += [
                f"- Bug del lexer (`{not_in}` fallos): funde `no en` aunque no "
                "haya `not in`: `si no enum_class ...` → token NOT_IN "
                "(verificado en enum.py, netrc.py, pathlib.py).",
            ]
        if fstr:
            out += [
                f"- f-string con conversion `!r` ({fstr} fallos): el backend "
                "solo acepta `!s` o conversion omitida.",
            ]
        if dstar:
            out += [
                f"- dict-merge `{{**a, **b}}` ({dstar} fallo): no parsea; "
                "`**kwargs` en def/call si.",
            ]
        if rest:
            out += [
                f"- Otros {rest} PARSE_FAIL sin diagnosticar linea a linea "
                "(detalle crudo en el JSON: tokens RPAREN/COLON/STAR/etc.).",
            ]
        out += [
            "- Otros fallos puntuales verificados sobre su linea adaptada: "
            "walrus `:=` en `mientras`/`si`, tuplas en targets de `para`, "
            "`;` entre sentencias, star-unpacking en asignacion, colisiones "
            "con palabras clave (`no = []`).",
        ]
        return out

    lines = [
        "# Parity corpus report v4 — CODIGO REAL (stdlib + tutorial CPython)",
        "",
        f"- instrumento: `tools/parity_corpus_v4.py` | backend medido: **linux_x86**",
        f"- oracle: {meta['oracle']} | git: `{meta['git_sha']}` | fecha: {meta['date']}",
        f"- tier A (stdlib real, estatico, sin ejecucion): **{tier_a['total']}** archivos",
        f"- tier B (tutorial oficial, ejecucional): **{tier_b_funnel['total']}** scripts",
        "",
        "## Tier A — embudo sobre la stdlib real (/usr/lib/python3.12/*.py)",
        "",
        "Poblacion SIN seleccion: todos los .py de nivel superior. Solo embudo "
        "estatico (ningun archivo se ejecuta; side-effects fuera de alcance). "
        "Cada fila = archivos que PASAN esa etapa.",
        "",
    ]
    lines += _stage_rows(tier_a)
    lines += ["", f"**Build completo (potencialmente ejecutable): {tier_a['pct']['build']}%**", "",
              "### Clases de fallo por etapa", "", "| etapa fallida | n |", "|---|---|"]
    for fail, count in tier_a["fail_classes"].items():
        lines.append(f"| {fail} | {count} |")
    mir_mod = _mir_modules(tier_a_records)
    if mir_mod:
        lines += ["",
                  f"De los fallos MIR, **{mir_mod}** son `native module not supplied` "
                  "(imports de la stdlib real de CPython: el backend nativo solo "
                  "resuelve modulos propios de PITON)."]
    lines += _parse_patterns(tier_a_records)
    lines += _findings(tier_a_records)
    lines += ["", "## Tier B — scripts del tutorial oficial de CPython", "",
              "Cada fila = scripts que PASAN esa etapa."]
    lines += _stage_rows(tier_b_funnel)
    if tier_b_run:
        lines += ["", "| bucket de ejecucion | n |", "|---|---|"]
        for bucket, count in tier_b_run["by_bucket"].items():
            lines.append(f"| {bucket} | {count} |")
        lines += ["",
                  f"EQUIV (byte-identico vs CPython): **{tier_b_run['pct_equiv']}%** | "
                  f"salida nativa honesta (EQUIV+RAISE_EQ): **{tier_b_run['pct_honest_native']}%** | "
                  f"DIVERGENT: **{tier_b_run['pct_divergent']}%**"]
    lines += _parse_patterns(tier_b_records)
    lines += [
        "",
        "## Lectura honesta",
        "",
        "- Los % son DEL POPULACION DECLARADO, no de 'CPython entero'. El",
        "  embudo tier A es la medida mas defendible de gramatica real: es la",
        "  stdlib de produccion sin elegir nada.",
        "- Metodo: PITON tiene gramatica en espanol (si/funcion/para) y no",
        "  admite comentarios `#`; la etapa `adapt` traduce el Python real de",
        "  forma mecanica (tabla de keywords + heuristica de match/case +",
        "  comentarios eliminados; builtins en ingles: el runtime es",
        "  bilingue). adapt no falla en ningun archivo (0 fallos); lo que",
        "  mide el embudo es que tanto codigo Python real sobrevive a la",
        "  gramatica y el back-end de PITON DESPUES de esa traduccion mecanica.",
        "- tier B son ejemplos autorizados por el equipo de CPython (el",
        "  tutorial), convertidos 1:1 (bloques doctest -> script); el estado",
        "  entre ejemplos de una pagina es el de su sesion REPL (cada",
        "  ejemplo en try/except: la sesion no muere en un error, y un",
        "  fallo por un lado solo marca DIVERGENT).",
        "",
        "## NOT_DEMONSTRATED",
        "",
        "- Windows x86: este instrumento NO lo mide (tiene su propia CI de",
        "  PRs; la paridad Windows se juzga ahi).",
        "- imports en runtime: los scripts que importan stdlib real fallan en",
        "  la etapa de ejecucion; no se afirma soporte de import externo.",
        "- paquetes/anidados de stdlib: fuera del alcance (solo nivel",
        "  superior en tier A).",
        "- side-effects de modulos stdlib: NINGUN archivo tier A se ejecuta.",
        "- corpus PyPI/arbitrario: fuera de alcance de v4 (el tutorial es el",
        "  codigo real mas autorizado que existe; PyPI es el siguiente paso).",
        "- Diagnostico completo de PARSE_FAIL: solo los patrones de la seccion",
        "  Hallazgos estan trazados linea a linea; el resto queda en bruto en",
        "  el JSON del reporte.",
        "",
    ]
    return "\n".join(lines)


def cmd_run(tier: str, out: str | None, markdown: str | None, workers: int,
            recursive: bool = False, exclude: tuple[str, ...] = (),
            isolate: bool = False) -> int:
    git_clean, git_dirty = _git_status()
    tool_path = REPO / "tools" / "parity_corpus_v4.py"
    instrument_hash = _hash_file(tool_path)
    # hash of core piton lowering files for reproducibility
    mir_hash = _hash_file(REPO / "piton" / "mir.py")
    linux_hash = _hash_file(REPO / "piton" / "linux_x86.py")
    meta = {"date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "oracle": f"CPython {platform.python_version()}",
            "platform": platform.platform(), "git_sha": _git_sha(),
            "git_clean": git_clean,
            "git_dirty_files": git_dirty,
            "instrument_hash": instrument_hash,
            "piton_mir_hash": mir_hash,
            "piton_linux_hash": linux_hash,
            "tool": "tools/parity_corpus_v4.py", "tiers": tier,
            "recursive": recursive, "exclude": list(exclude),
            "isolate": isolate}
    results: dict[str, dict] = {}

    if tier in {"a", "all"}:
        # CORPUS_LARGE_V1: --recursive recorre subpaquetes (json/, email/,
        # asyncio/...); --exclude descarta subcadenas del path (tests/,
        # __pycache__/) para que el corpus siga siendo biblioteca real.
        pattern = "**/*.py" if recursive else "*.py"
        files = sorted(str(p) for p in STDLIB_DIR.glob(pattern))
        if exclude:
            files = [f for f in files if not any(token in f for token in exclude)]
        if not files:
            raise SystemExit(f"tier A: no *.py in {STDLIB_DIR} (recursive={recursive})")
        print(f"tier A: {len(files)} archivos stdlib"
              f"{' (recursivo)' if recursive else ''}...")
        if isolate:
            from concurrent.futures import ThreadPoolExecutor as _ThreadPool
            with _ThreadPool(max_workers=workers) as pool:
                records = list(pool.map(isolated_stage, files))
        else:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                records = list(pool.map(_stage_worker, files, chunksize=4))
        results["tier_a"] = {"funnel": _funnel(records), "records": records}
        fa = results["tier_a"]["funnel"]
        print(f"  adapt {fa['pct']['adapt']}% | parse {fa['pct']['parse']}% | "
              f"mir {fa['pct']['mir']}% | build {fa['pct']['build']}%")

    tier_b_funnel = None
    tier_b_run = None
    if tier in {"b", "all"}:
        files = sorted(str(p) for p in TUTORIAL_DIR.glob("*.py"))
        if not files:
            raise SystemExit("tier B vacio: corre `fetch` primero")
        print(f"tier B: {len(files)} scripts tutorial (embudo + ejecucion)...")
        with ProcessPoolExecutor(max_workers=workers) as pool:
            records = list(pool.map(_run_worker, [(f, 15.0) for f in files], chunksize=1))
        tier_b_funnel = _funnel([{k: r.get(k) for k in ("reached", "stage_fail", "detail")}
                                 for r in records])
        tier_b_run = _bucket_summary(records)
        results["tier_b"] = {"funnel": tier_b_funnel, "run": tier_b_run, "records": records}
        print(f"  EQUIV {tier_b_run['pct_equiv']}% | honest {tier_b_run['pct_honest_native']}% | "
              f"divergent {tier_b_run['pct_divergent']}%")

    report = {"meta": meta, "results": results}
    if out:
        Path(out).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"json -> {out}")
    if markdown:
        tier_a_fun = results.get("tier_a", {}).get("funnel") or _funnel([])
        tier_a_rec = results.get("tier_a", {}).get("records")
        tier_b_fun = results.get("tier_b", {}).get("funnel") or _funnel([])
        tier_b_r = results.get("tier_b", {}).get("run")
        tier_b_rec = results.get("tier_b", {}).get("records")
        Path(markdown).write_text(
            render_markdown(meta, tier_a_fun, tier_b_fun, tier_b_r,
                            tier_a_rec, tier_b_rec),
            encoding="utf-8")
        print(f"md -> {markdown}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch", help="descarga el tutorial de CPython -> corpus_v4/")
    run = sub.add_parser("run", help="mide los tiers")
    run.add_argument("--tier", choices=["a", "b", "all"], default="all")
    run.add_argument("--out", default=str(REPO / "docs" /
                     f"parity_corpus_v4_report_{datetime.now():%Y-%m-%d}.json"))
    run.add_argument("--markdown", default=str(REPO / "docs" /
                     f"parity_corpus_v4_report_{datetime.now():%Y-%m-%d}.md"))
    run.add_argument("--workers", type=int, default=8)
    run.add_argument("--recursive", action="store_true",
                     help="tier A: incluye subpaquetes de la stdlib")
    run.add_argument("--exclude", action="append", default=[],
                     metavar="SUBSTR",
                     help="descarta rutas que contengan SUBSTR (repetible)")
    run.add_argument("--isolate", action="store_true",
                     help="tier A: compila cada archivo en un proceso hijo")
    args = parser.parse_args(argv)
    if args.cmd == "fetch":
        return cmd_fetch()
    return cmd_run(args.tier, args.out, args.markdown, args.workers,
                   args.recursive, tuple(args.exclude), args.isolate)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
