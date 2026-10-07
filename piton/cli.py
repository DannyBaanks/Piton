from __future__ import annotations

import argparse
import ast
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import token
import tokenize
from pathlib import Path

from . import __version__
from .final_dashboard import build_dashboard, render_markdown, render_summary
from .linux_x86 import compile_native_linux
from .lower import LoweringError, lower_cst_to_hir
from .mir import MIRLoweringError, lower_hir_to_mir
from .native_evidence import build_windows_evidence
from .parser import parse
from .runtime import ejecutar_archivo
from .translator import PitonSyntaxError, analizar_tokens, leer_fuente, traducir_archivo, traducir_fuente
from .x86 import NativeBuildError, compile_native_files

TARGETS = ("windows-x86_64", "linux-x86_64")
_ALIAS_TARGET = {"x86": "windows-x86_64", "linux": "linux-x86_64"}


def _deep_version() -> str:
    return __version__


def _programa_de_argv(argv0: str) -> str:
    base = os.path.basename(argv0)
    if base.endswith(".exe"):
        base = base[:-4]
    if base in {"pi", "pitn", "piton"}:
        return base
    return "piton"


def _target_host() -> str:
    return "windows-x86_64" if sys.platform == "win32" else "linux-x86_64"


def _resolver_target(nombre: str | None) -> str:
    if nombre is None:
        return _target_host()
    return _ALIAS_TARGET.get(nombre, nombre)


def _default_output(archivo: Path, target: str) -> Path:
    if target == "windows-x86_64":
        return archivo.with_suffix(".exe") if archivo.suffix else archivo.with_name(archivo.name + ".exe")
    return archivo.with_suffix("") if archivo.suffix else archivo.with_name(archivo.name + ".native")


def _toolchain(target: str) -> tuple[bool, str]:
    """(listo, detalle). Refleja la realidad del host, no targets declarados."""
    nasm = shutil.which("nasm")
    gcc = shutil.which("gcc")
    mingw = shutil.which("x86_64-w64-mingw32-gcc")
    if target == "windows-x86_64":
        if sys.platform != "win32":
            return False, "requiere host Windows (NASM + MinGW en PATH)"
        ok = bool(nasm) and bool(gcc or mingw)
        detalle = f"nasm={'ok' if nasm else 'FALTA'} gcc-mingw={'ok' if (gcc or mingw) else 'FALTA'}"
        return ok, detalle
    # linux-x86_64
    if sys.platform == "win32":
        wsl = shutil.which("wsl")
        return bool(wsl), f"wsl={'ok' if wsl else 'FALTA'} (GCC freestanding dentro de WSL)"
    return bool(gcc), f"gcc={gcc or 'FALTA'} (freestanding)"


def _target_soportado_en_host(target: str) -> str | None:
    """None si se puede construir en este host; si no, razón accionable."""
    if target == "windows-x86_64" and sys.platform != "win32":
        return ("windows-x86_64 requiere host Windows (NASM + MinGW). "
                "En este host solo puedes compilar linux-x86_64. "
                "hint: usa una máquina Windows o el CI para PE.")
    if target == "linux-x86_64" and sys.platform == "win32" and not shutil.which("wsl"):
        return ("linux-x86_64 en Windows requiere WSL instalado. "
                "hint: habilita WSL o compila en Linux/host nativo.")
    return None


def _mostrar_tokens(ruta: Path, estricto: bool = False) -> None:
    fuente = leer_fuente(ruta)
    _, cambios, _ = analizar_tokens(fuente, str(ruta), estricto=estricto)
    tokens = list(tokenize.generate_tokens(io.StringIO(fuente).readline))
    por_posicion = {(c.linea, c.columna, c.original): c for c in cambios}
    for actual in tokens:
        if actual.type in {token.ENDMARKER, token.ENCODING}:
            continue
        linea, columna_cero = actual.start
        cambio = por_posicion.get((linea, columna_cero + 1, actual.string))
        estado = "TRANSFORMA" if cambio else "conserva"
        detalle = f" -> {cambio.traducido!r} ({cambio.categoria})" if cambio else ""
        print(
            f"{linea}:{columna_cero + 1} {token.tok_name[actual.type]:<10} "
            f"{estado:<10} {actual.string!r}{detalle}"
        )


def _emit_python(archivo: Path, salida: Path | None, estricto: bool) -> None:
    resultado = traducir_archivo(archivo, estricto=estricto)
    if salida:
        salida.write_text(resultado, encoding="utf-8")
    else:
        sys.stdout.write(resultado)


def _emit_ast(archivo: Path, salida: Path | None, estricto: bool) -> None:
    fuente = leer_fuente(archivo)
    traducido = traducir_fuente(fuente, str(archivo), estricto=estricto)
    arbol = ast.parse(traducido)
    texto = ast.dump(arbol, indent=2)
    if salida:
        salida.write_text(texto + "\n", encoding="utf-8")
    else:
        print(texto)


def _emit_mir(archivo: Path, salida: Path | None, pretty: bool) -> None:
    fuente = leer_fuente(archivo)
    mir = lower_hir_to_mir(lower_cst_to_hir(parse(fuente)))
    if pretty:
        texto = json.dumps(mir.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
    else:
        texto = mir.to_json()
    if salida:
        salida.write_text(texto + "\n", encoding="utf-8")
    else:
        print(texto)


def _check(archivo: Path, estricto: bool) -> None:
    # Pipeline real compartido por ambos backends: fuente Pitón -> parse ->
    # CST -> HIR -> MIR. Si algo falla, no es checkable. Nada se emite.
    fuente = leer_fuente(archivo)
    traducir_fuente(fuente, str(archivo), estricto=estricto)  # superficie CPython validada
    mir = lower_hir_to_mir(lower_cst_to_hir(parse(fuente)))  # lowering nativo validado
    n_funciones = len(mir.functions)
    print(f"PITON_CHECK = PASS ({archivo}) — superficie + lowering a MIR ({n_funciones} funciones)")


def _build(args: argparse.Namespace) -> int:
    target = _resolver_target(args.target)
    if target not in TARGETS:
        print(f"PITON_NATIVE_BUILD_ERROR\ntarget desconocido: {args.target} (válidos: {', '.join(TARGETS)})", file=sys.stderr)
        return 2
    no_soportado = _target_soportado_en_host(target)
    if no_soportado:
        print(f"PITON_NATIVE_BUILD_ERROR\n{no_soportado}", file=sys.stderr)
        return 1
    salida = args.output if args.output is not None else _default_output(args.archivo, target)
    try:
        if salida.resolve() == args.archivo.resolve():
            print("PITON_NATIVE_BUILD_ERROR\nnative output cannot overwrite the source file", file=sys.stderr)
            return 1
    except OSError:
        pass
    if args.verbose:
        ok, detalle = _toolchain(target)
        print(f"[build] target={target} toolchain={'READY' if ok else 'NO LISTO'} ({detalle})", file=sys.stderr)
    try:
        if target == "windows-x86_64":
            artefacto = compile_native_files(args.archivo, salida)
        else:
            if args.evidencia:
                print("PITON_NATIVE_BUILD_ERROR\n--evidence solo está disponible para --target windows-x86_64", file=sys.stderr)
                return 1
            artefacto = compile_native_linux(leer_fuente(args.archivo), salida)
    except NativeBuildError as error:
        print(f"PITON_NATIVE_BUILD_ERROR\n{error}", file=sys.stderr)
        return 1
    except (LoweringError, MIRLoweringError) as error:
        print(f"PITON_NATIVE_BUILD_ERROR\n{error}", file=sys.stderr)
        return 1
    print(f"PITON_NATIVE_BUILD = PASS ({artefacto})")
    if args.evidencia:
        recibo = build_windows_evidence(args.archivo, artefacto, args.evidencia)
        print(f"PITON_NATIVE_SUBSET_1_0 = {recibo['native_subset_1_0']} ({args.evidencia.resolve()})")
        if recibo["native_subset_1_0"] != "PASS":
            raise NativeBuildError("native evidence gates failed")
    return 0


def _run_native(archivo: Path, argumentos: list[str], estricto: bool) -> int:
    target = _target_host()
    try:
        with tempfile.TemporaryDirectory(prefix="piton-run-") as tmp:
            salida = Path(tmp) / ("programa.exe" if target == "windows-x86_64" else "programa")
            if target == "windows-x86_64":
                artefacto = compile_native_files(archivo, salida)
            else:
                artefacto = compile_native_linux(leer_fuente(archivo), salida)
            try:
                completado = subprocess.run([str(artefacto), *argumentos], check=False)
                return completado.returncode
            except OSError as error:
                print(f"PITON_NATIVE_BUILD_ERROR\nno se pudo ejecutar {artefacto}: {error}", file=sys.stderr)
                return 1
    except NativeBuildError as error:
        print(f"PITON_NATIVE_BUILD_ERROR\n{error}\nhint: usa `--engine cpython` si quieres ejecución CPython", file=sys.stderr)
        return 1
    except (LoweringError, MIRLoweringError) as error:
        print(f"PITON_NATIVE_BUILD_ERROR\n{error}\nhint: usa `--engine cpython` si quieres ejecución CPython", file=sys.stderr)
        return 1


def _cmd_targets() -> int:
    print(f"Host: {platform.system()} {platform.machine()} (Python {platform.python_version()})")
    print(f"{'TARGET':<18} {'STATUS':<10} DETALLE")
    for target in TARGETS:
        ok, detalle = _toolchain(target)
        host = " (host)" if target == _target_host() else ""
        print(f"{target:<18} {'ready' if ok else 'MISSING':<10} {detalle}{host}")
    return 0


def _cmd_doctor() -> int:
    lineas: list[tuple[str, str, str]] = []
    lineas.append(("Piton", __version__, "OK"))
    lineas.append(("Host", f"{platform.system()} {platform.machine()}", "OK"))
    lineas.append(("Python compilador", sys.version.split()[0], "OK"))
    gcc = shutil.which("gcc")
    lineas.append(("GCC", gcc or "ausente", "OK" if gcc else "OPCIONAL*"))
    nasm = shutil.which("nasm")
    lineas.append(("NASM", nasm or "ausente", "OK" if nasm else "OPCIONAL*"))
    mingw = shutil.which("x86_64-w64-mingw32-gcc")
    lineas.append(("MinGW GCC", mingw or "ausente", "OK" if mingw else "OPCIONAL*"))
    print("PITON DOCTOR")
    ancho = max(len(k) for k, _, _ in lineas)
    for nombre, valor, estado in lineas:
        print(f"{nombre:<{ancho}}  {valor:<45} {estado}")
    print()
    listos = 0
    for target in TARGETS:
        ok, detalle = _toolchain(target)
        print(f"{target:<18} {'READY' if ok else 'UNAVAILABLE':<12} {detalle}")
        listos += 1 if ok else 0
    print()
    print("* opcional: solo el target que lo necesita lo marca como fallo.")
    host_target = _target_host()
    host_ok, _ = _toolchain(host_target)
    if listos == len(TARGETS):
        print("RESULT: READY")
        return 0
    if host_ok:
        print("RESULT: PARTIAL (target del host listo; el otro target necesita su toolchain)")
        return 0
    if listos > 0:
        print("RESULT: PARTIAL (el target del host no compila en esta máquina)")
        return 1
    print("RESULT: BROKEN (ningún target compila; revisa la tabla)")
    return 1


def _cmd_evidence(args: argparse.Namespace) -> int:
    from .native_evidence import load_windows_evidence
    evidence = load_windows_evidence(args.receipt) if args.receipt else None
    dashboard = build_dashboard(evidence)
    if args.format == "summary":
        sys.stdout.write(render_summary(dashboard))
    elif args.format == "markdown":
        sys.stdout.write(render_markdown(dashboard))
    else:
        sys.stdout.write(json.dumps(dashboard, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return 0 if dashboard["release_ready"] else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=_programa_de_argv(sys.argv[0]),
        description="PITÓN — compilador de una superficie en español: traduce .piton, ejecuta con CPython o compila a x86-64 nativo (PE Windows / ELF Linux).",
        epilog="Ejemplos:\n  pi run hola.piton\n  pi build hola.piton --target linux-x86_64\n  pi check hola.piton\n  pi doctor\n  pi evidence --format summary",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", "-V", action="version", version=f"Piton {__version__}")
    parser.add_argument("--debug", action="store_true", help="muestra tracebacks completos ante error interno")
    subcomandos = parser.add_subparsers(dest="comando", required=True)

    # ---- canónicos ----
    run = subcomandos.add_parser("run", help="ejecuta un archivo .piton")
    run.add_argument("archivo", type=Path)
    run.add_argument("--engine", choices=("cpython", "native"), default="cpython",
                     help="cpython (default) ejecuta vía CPython; native compila y ejecuta sin CPython")
    run.add_argument("-x", "--estricto", action="store_true", help="rechaza nombres ambiguos de soft keywords")

    build = subcomandos.add_parser("build", help="compila un archivo .piton a nativo x86-64")
    build.add_argument("archivo", type=Path)
    build.add_argument("-o", "--output", type=Path, help="ruta del artefacto (default: <nombre>.exe | <nombre>)")
    build.add_argument("--target", default=None,
                       choices=(*TARGETS, "x86", "linux"),
                       help="target nativo (default: host actual); alias: x86, linux")
    build.add_argument("--evidence", "--evidencia", dest="evidencia", type=Path,
                       help="escribe un recibo JSON verificable (solo --target windows-x86_64)")
    build.add_argument("-v", "--verbose", action="store_true", help="diagnóstico del build en stderr")

    check = subcomandos.add_parser("check", help="valida sintaxis + lowering a MIR (no emite ejecutable)")
    check.add_argument("archivo", type=Path)
    check.add_argument("-x", "--estricto", action="store_true", help="rechaza nombres ambiguos de soft keywords")

    emit = subcomandos.add_parser("emit", help="emite una representación del compilador")
    emit_sub = emit.add_subparsers(dest="representacion", required=True)
    e_python = emit_sub.add_parser("python", help="Python generado (stdout)")
    e_python.add_argument("archivo", type=Path)
    e_python.add_argument("-o", "--output", type=Path)
    e_python.add_argument("-x", "--estricto", action="store_true")
    e_ast = emit_sub.add_parser("ast", help="AST del Python generado (stdout)")
    e_ast.add_argument("archivo", type=Path)
    e_ast.add_argument("-o", "--output", type=Path)
    e_ast.add_argument("-x", "--estricto", action="store_true")
    e_tokens = emit_sub.add_parser("tokens", help="tokens y cambios léxicos (stdout)")
    e_tokens.add_argument("archivo", type=Path)
    e_tokens.add_argument("-x", "--estricto", action="store_true")
    e_mir = emit_sub.add_parser("mir", help="MIR (JSON determinista) del pipeline nativo")
    e_mir.add_argument("archivo", type=Path)
    e_mir.add_argument("-o", "--output", type=Path)
    e_mir.add_argument("--pretty", action="store_true", help="JSON con indentación")

    repl = subcomandos.add_parser("repl", help="REPL interactivo (engine CPython)")

    targets = subcomandos.add_parser("targets", help="lista targets nativos y estado de toolchain")

    doctor = subcomandos.add_parser("doctor", help="diagnostica el entorno del compilador")

    evidence = subcomandos.add_parser("evidence", help="muestra la evidencia verificada del compilador")
    evidence.add_argument("--receipt", "--recibo", dest="receipt", type=Path,
                          help="recibo nativo Windows requerido para NATIVE_SUBSET_1_0 = PASS")
    evidence.add_argument("--format", choices=("summary", "markdown", "json"), default="summary")

    test = subcomandos.add_parser("test", help="corre la suite de tests (pytest)")
    test.add_argument("rutas", nargs="*", default=["tests/"], help="rutas de tests (default: tests/)")
    test.add_argument("-v", "--verbose", action="store_true", help="salida verbosa")
    test.add_argument("-q", "--quiet", action="store_true", help="salida silenciosa")
    test.add_argument("--tb", default="short", help="traceback style (short|long|line|native)")
    test.add_argument("--ignore", action="append", default=[], help="ignora una ruta (repeatable)")

    corpus = subcomandos.add_parser("corpus", help="corre el corpus diferencial de paridad (tools/parity_corpus.py)")
    corpus.add_argument("--areas", default="", help="areas separadas por coma (default: todas)")
    corpus.add_argument("--jobs", type=int, default=12, help="workers paralelos")
    corpus.add_argument("--timeout", type=float, default=10.0, help="timeout por caso (segundos)")
    corpus.add_argument("--limit", type=int, default=0, help="solo los primeros N casos (smoke runs)")
    corpus.add_argument("--out", type=Path, default=Path("docs/parity_corpus_report.json"), help="reporte JSON")
    corpus.add_argument("--markdown", type=Path, default=Path("docs/parity_corpus_report.md"), help="reporte markdown")

    version = subcomandos.add_parser("version", help="muestra la versión de Piton")

    # ---- legacy (compatibles, mismos contratos históricos) ----
    ejecutar = subcomandos.add_parser("ejecutar", help="alias legacy de `run --engine cpython`")
    ejecutar.add_argument("archivo", type=Path)
    ejecutar.add_argument("argumentos", nargs=argparse.REMAINDER, help="argumentos para el programa")
    ejecutar.add_argument("-x", "--estricto", action="store_true", help="rechaza nombres ambiguos de soft keywords")

    traducir = subcomandos.add_parser("traducir", help="alias legacy de `emit python`")
    traducir.add_argument("archivo", type=Path)
    traducir.add_argument("-o", "--salida", type=Path)
    traducir.add_argument("-x", "--estricto", action="store_true", help="rechaza nombres ambiguos de soft keywords")

    verificar = subcomandos.add_parser("verificar", help="valida sin ejecutar (mensaje legacy)")
    verificar.add_argument("archivo", type=Path)
    verificar.add_argument("-x", "--estricto", action="store_true", help="rechaza nombres ambiguos de soft keywords")

    tokens_cmd = subcomandos.add_parser("tokens", help="alias legacy de `emit tokens`")
    tokens_cmd.add_argument("archivo", type=Path)
    tokens_cmd.add_argument("-x", "--estricto", action="store_true", help="rechaza nombres ambiguos de soft keywords")

    ast_cmd = subcomandos.add_parser("ast", help="alias legacy de `emit ast`")
    ast_cmd.add_argument("archivo", type=Path)
    ast_cmd.add_argument("-x", "--estricto", action="store_true", help="rechaza nombres ambiguos de soft keywords")

    compilar = subcomandos.add_parser("compilar", help="alias legacy de `build`")
    compilar.add_argument("archivo", type=Path)
    compilar.add_argument("--backend", choices=("x86", "linux"), default="x86")
    compilar.add_argument("-o", "--output", "--salida", dest="salida", type=Path)
    compilar.add_argument("--evidencia", type=Path, help="escribe un recibo JSON verificable (backend x86)")

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # `--` separa los argumentos del programa (solo para `run`/`ejecutar`)
    programa_args: list[str] = []
    if "--" in argv:
        idx = argv.index("--")
        cabeza, cola = argv[:idx], argv[idx + 1:]
        if argv and argv[0] in {"run", "ejecutar"}:
            programa_args = cola
        argv = cabeza

    args = _parser().parse_args(argv)
    estricto = getattr(args, "estricto", False)
    try:
        if args.comando == "traducir" or (args.comando == "emit" and args.representacion == "python"):
            salida = getattr(args, "salida", None) if args.comando == "traducir" else args.output
            _emit_python(args.archivo, salida, estricto)
        elif args.comando == "verificar":
            traducir_archivo(args.archivo, estricto=estricto)
            print(f"PITON_VALIDATION = PASS ({args.archivo})")
        elif args.comando == "check":
            _check(args.archivo, estricto)
        elif args.comando == "ejecutar":
            # legacy: REMAINDER sigue pudiendo contener args; `--` ya los separó
            argumentos = programa_args if programa_args else [a for a in args.argumentos]
            if argumentos and argumentos[0] == "--":
                argumentos = argumentos[1:]
            ejecutar_archivo(args.archivo, argumentos, estricto=estricto)
        elif args.comando == "run":
            if args.engine == "cpython":
                ejecutar_archivo(args.archivo, programa_args, estricto=estricto)
            else:
                return _run_native(args.archivo, programa_args, estricto)
        elif args.comando == "tokens" or (args.comando == "emit" and args.representacion == "tokens"):
            _mostrar_tokens(args.archivo, estricto=estricto)
        elif args.comando == "ast" or (args.comando == "emit" and args.representacion == "ast"):
            salida = getattr(args, "output", None) if args.comando == "emit" else None
            _emit_ast(args.archivo, salida, estricto)
        elif args.comando == "emit" and args.representacion == "mir":
            _emit_mir(args.archivo, args.output, args.pretty)
        elif args.comando == "test":
            import pytest
            pytest_args = list(args.rutas) or ["tests/"]
            if args.verbose:
                pytest_args.insert(0, "-v")
            if args.quiet:
                pytest_args.insert(0, "-q")
            pytest_args.extend(["--tb", args.tb])
            for ruta in args.ignore:
                pytest_args.extend(["--ignore", ruta])
            raise SystemExit(pytest.main(pytest_args))
        elif args.comando == "corpus":
            repo = Path(__file__).resolve().parents[1]
            corpus_script = repo / "tools" / "parity_corpus.py"
            corpus_argv = [sys.executable, str(corpus_script)]
            if args.areas:
                corpus_argv.extend(["--areas", args.areas])
            corpus_argv.extend([
                "--jobs", str(args.jobs),
                "--timeout", str(args.timeout),
                "--limit", str(args.limit),
                "--out", str(args.out),
                "--markdown", str(args.markdown),
            ])
            raise SystemExit(subprocess.call(corpus_argv))
        elif args.comando == "version":
            print(f"Piton {__version__}")
        elif args.comando == "compilar":
            args_build = argparse.Namespace(
                archivo=args.archivo,
                output=args.salida,
                target={"x86": "windows-x86_64", "linux": "linux-x86_64"}[args.backend],
                evidencia=args.evidencia,
                verbose=False,
            )
            return _build(args_build)
        elif args.comando == "build":
            return _build(args)
        elif args.comando == "repl":
            from .repl import ejecutar_repl
            ejecutar_repl()
        elif args.comando == "targets":
            return _cmd_targets()
        elif args.comando == "doctor":
            return _cmd_doctor()
        elif args.comando == "evidence":
            return _cmd_evidence(args)
    except PitonSyntaxError as error:
        print(error, file=sys.stderr)
        return 2
    except (NativeBuildError, LoweringError, MIRLoweringError) as error:
        print(f"PITON_NATIVE_BUILD_ERROR\n{error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"PITON_IO_ERROR\n{error}", file=sys.stderr)
        return 1
    except Exception as error:  # error interno: diagnóstico corto, sin traceback salvo --debug
        if args.debug:
            raise
        print(
            f"PITON_INTERNAL_ERROR\n{type(error).__name__}: {error}\n"
            f"hint: vuelve a ejecutar con --debug para la traza completa",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
