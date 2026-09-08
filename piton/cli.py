from __future__ import annotations

import argparse
import ast
import io
import sys
import token
import tokenize
from pathlib import Path

from .runtime import ejecutar_archivo
from .translator import PitonSyntaxError, analizar_tokens, leer_fuente, traducir_archivo


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="piton",
        description="Frontend en español para Python: traduce .piton y ejecuta con CPython.",
    )
    subcomandos = parser.add_subparsers(dest="comando", required=True)

    ejecutar = subcomandos.add_parser("ejecutar", help="traduce y ejecuta un archivo con CPython")
    ejecutar.add_argument("archivo", type=Path)
    ejecutar.add_argument("argumentos", nargs=argparse.REMAINDER, help="argumentos para el programa")
    ejecutar.add_argument("-x", "--estricto", action="store_true", help="rechaza nombres ambiguos de soft keywords")

    traducir = subcomandos.add_parser("traducir", help="muestra o escribe el Python generado")
    traducir.add_argument("archivo", type=Path)
    traducir.add_argument("-o", "--salida", type=Path)
    traducir.add_argument("-x", "--estricto", action="store_true", help="rechaza nombres ambiguos de soft keywords")

    verificar = subcomandos.add_parser("verificar", help="traduce y valida sin ejecutar")
    verificar.add_argument("archivo", type=Path)
    verificar.add_argument("-x", "--estricto", action="store_true", help="rechaza nombres ambiguos de soft keywords")

    tokens_cmd = subcomandos.add_parser("tokens", help="muestra los tokens y cambios léxicos")
    tokens_cmd.add_argument("archivo", type=Path)
    tokens_cmd.add_argument("-x", "--estricto", action="store_true", help="rechaza nombres ambiguos de soft keywords")

    ast_cmd = subcomandos.add_parser("ast", help="muestra el AST de Python generado")
    ast_cmd.add_argument("archivo", type=Path)
    ast_cmd.add_argument("-x", "--estricto", action="store_true", help="rechaza nombres ambiguos de soft keywords")

    return parser


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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    estricto = getattr(args, "estricto", False)
    try:
        if args.comando == "traducir":
            resultado = traducir_archivo(args.archivo, estricto=estricto)
            if args.salida:
                args.salida.write_text(resultado, encoding="utf-8")
            else:
                sys.stdout.write(resultado)
        elif args.comando == "verificar":
            traducir_archivo(args.archivo, estricto=estricto)
            print(f"PITON_VALIDATION = PASS ({args.archivo})")
        elif args.comando == "ejecutar":
            ejecutar_archivo(args.archivo, args.argumentos, estricto=estricto)
        elif args.comando == "tokens":
            _mostrar_tokens(args.archivo, estricto=estricto)
        elif args.comando == "ast":
            from .translator import traducir_fuente
            fuente = leer_fuente(args.archivo)
            traducido = traducir_fuente(fuente, str(args.archivo), estricto=estricto)
            arbol = ast.parse(traducido)
            print(ast.dump(arbol, indent=2))
    except PitonSyntaxError as error:
        print(error, file=sys.stderr)
        return 2
    except OSError as error:
        print(f"PITON_IO_ERROR\n{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
