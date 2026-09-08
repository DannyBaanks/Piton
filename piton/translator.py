from __future__ import annotations

import ast
import io
import re
import sys
import token
import tokenize
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


HARD_KEYWORDS = {
    "si": "if",
    "sino_si": "elif",
    "sino": "else",
    "para": "for",
    "mientras": "while",
    "en": "in",
    "funcion": "def",
    "devolver": "return",
    "producir": "yield",
    "intentar": "try",
    "excepto": "except",
    "finalmente": "finally",
    "lanzar": "raise",
    "con": "with",
    "como": "as",
    "importar": "import",
    "desde": "from",
    "clase": "class",
    "Verdadero": "True",
    "Falso": "False",
    "Nada": "None",
    "y": "and",
    "o": "or",
    "no": "not",
    "romper": "break",
    "continuar": "continue",
    "pasar": "pass",
    "asincrono": "async",
    "esperar": "await",
    "afirmar": "assert",
    "borrar": "del",
    "no_local": "nonlocal",
    "es": "is",
}

SOFT_KEYWORDS = {
    "segun": "match",
    "caso": "case",
    "tipo": "type",
}

ALIASES_BUILTIN = {
    "imprimir": "print",
    "entrada": "input",
    "rango": "range",
    "longitud": "len",
    "enumerar": "enumerate",
    "lista": "list",
    "diccionario": "dict",
    "conjunto": "set",
    "tupla": "tuple",
    "entero": "int",
    "decimal": "float",
    "texto": "str",
    "booleano": "bool",
    "abrir": "open",
    "ordenar": "sorted",
}

_TRIVIA = {
    token.ENCODING,
    token.ENDMARKER,
    token.INDENT,
    token.DEDENT,
    token.NEWLINE,
    tokenize.NL,
    token.COMMENT,
}


@dataclass(frozen=True)
class CambioToken:
    linea: int
    columna: int
    original: str
    traducido: str
    categoria: str


@dataclass
class MapaFuente:
    """Source map: maps generated (line, col) back to original (line, col)."""
    original: dict[tuple[int, int], tuple[int, int]] = field(default_factory=dict)

    def agregar(self, gen_linea: int, gen_col: int, orig_linea: int, orig_col: int) -> None:
        self.original[(gen_linea, gen_col)] = (orig_linea, orig_col)

    def resolver(self, gen_linea: int, gen_col: int) -> tuple[int, int] | None:
        return self.original.get((gen_linea, gen_col))


class PitonSyntaxError(Exception):
    def __init__(
        self,
        archivo: str,
        mensaje: str,
        linea: int | None = None,
        columna: int | None = None,
        texto: str | None = None,
    ) -> None:
        super().__init__(mensaje)
        self.archivo = archivo
        self.mensaje = mensaje
        self.linea = linea
        self.columna = columna
        self.texto = texto

    def __str__(self) -> str:
        lineas = [self._encabezado(), f"archivo: {self.archivo}"]
        if self.linea is not None:
            lineas.append(f"linea: {self.linea}")
        if self.columna is not None:
            lineas.append(f"columna: {self.columna}")
        lineas.extend(["", "Python rechazó la traducción:", self.mensaje])
        if self.texto:
            lineas.extend(["", self.texto.rstrip("\n")])
        return "\n".join(lineas)

    def _encabezado(self) -> str:
        return "PITON_SYNTAX_ERROR"


class PitonStrictError(PitonSyntaxError):
    def _encabezado(self) -> str:
        return "PITON_STRICT_ERROR"


def _anterior_significativo(tokens: list[tokenize.TokenInfo], indice: int) -> tokenize.TokenInfo | None:
    for candidato in reversed(tokens[:indice]):
        if candidato.type not in _TRIVIA:
            return candidato
    return None


def _siguiente_significativo(tokens: list[tokenize.TokenInfo], indice: int) -> tokenize.TokenInfo | None:
    for candidato in tokens[indice + 1 :]:
        if candidato.type not in _TRIVIA:
            return candidato
    return None


def _es_contexto_segun(tokens: list[tokenize.TokenInfo], indice: int) -> bool:
    siguiente = _siguiente_significativo(tokens, indice)
    if siguiente is None:
        return True
    if siguiente.type == token.OP and siguiente.string in ("(", "="):
        return False
    return True


def _es_contexto_caso(tokens: list[tokenize.TokenInfo], indice: int) -> bool:
    siguiente = _siguiente_significativo(tokens, indice)
    if siguiente is not None and siguiente.type == token.OP and siguiente.string == "=":
        return False
    return True


def _es_contexto_tipo(tokens: list[tokenize.TokenInfo], indice: int) -> bool:
    siguiente = _siguiente_significativo(tokens, indice)
    if siguiente is None:
        return False
    if siguiente.type != token.NAME:
        return False
    return True


def _nombre_asignado_en_scope(tokens: list[tokenize.TokenInfo]) -> set[str]:
    """Pre-scan: find names that are assigned (left side of = or augmented assign)."""
    asignados: set[str] = set()
    for i, actual in enumerate(tokens):
        if actual.type != token.NAME:
            continue
        anterior = _anterior_significativo(tokens, i)
        siguiente = _siguiente_significativo(tokens, i)
        if siguiente is not None and siguiente.type == token.OP and siguiente.string == "=":
            asignados.add(actual.string)
        if actual.string in ("global", "no_local"):
            sig2 = _siguiente_significativo(tokens, i + 1)
            if sig2 is not None and sig2.type == token.NAME:
                asignados.add(sig2.string)
    return asignados


def _es_carga_builtin(
    tokens: list[tokenize.TokenInfo],
    indice: int,
    nombre: str,
    asignados: set[str],
) -> bool:
    """Check if this NAME token is a builtin load (not a definition or attribute)."""
    if nombre not in ALIASES_BUILTIN:
        return False
    anterior = _anterior_significativo(tokens, indice)
    if anterior is not None and anterior.type == token.OP and anterior.string == ".":
        return False
    if anterior is not None and anterior.type == token.NAME and anterior.string in {"def", "funcion", "class", "clase"}:
        return False
    if anterior is not None and anterior.type == token.NAME and anterior.string in {"importar", "import", "desde", "from"}:
        return False
    if nombre in asignados:
        return False
    return True


def _traducir_fstring(expresion: str) -> str:
    """Translate Pitón keywords/builtins inside f-string expressions."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(expresion).readline))
    except tokenize.TokenError:
        return expresion
    resultado: list[tokenize.TokenInfo] = []
    for actual in tokens:
        if actual.type == token.NAME:
            if actual.string in HARD_KEYWORDS:
                resultado.append(actual._replace(string=HARD_KEYWORDS[actual.string]))
                continue
            if actual.string in ALIASES_BUILTIN:
                siguiente = _siguiente_significativo(tokens, tokens.index(actual))
                if siguiente is not None and siguiente.type == token.OP and siguiente.string == "(":
                    resultado.append(actual._replace(string=ALIASES_BUILTIN[actual.string]))
                    continue
        resultado.append(actual)
    return tokenize.untokenize(resultado).decode() if resultado else expresion


def _remapear_ubicacion(
    error: SyntaxError,
    mapa: MapaFuente | None,
) -> tuple[int | None, int | None, str | None]:
    """Remap a SyntaxError's location back to original .piton source."""
    if mapa is None or error.lineno is None or error.offset is None:
        return error.lineno, error.offset, error.text
    original = mapa.resolver(error.lineno, error.offset)
    if original is not None:
        return original[0], original[1], error.text
    return error.lineno, error.offset, error.text


def analizar_tokens(
    fuente: str,
    archivo: str = "<entrada>",
    estricto: bool = False,
) -> tuple[list[tokenize.TokenInfo], list[CambioToken], MapaFuente]:
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(fuente).readline))
    except (tokenize.TokenError, IndentationError) as error:
        ubicacion = error.args[1] if len(error.args) > 1 and isinstance(error.args[1], tuple) else (None, None)
        raise PitonSyntaxError(archivo, str(error.args[0]), ubicacion[0], ubicacion[1]) from error

    asignados = _nombre_asignado_en_scope(tokens)
    mapa = MapaFuente()
    salida: list[tokenize.TokenInfo] = []
    cambios: list[CambioToken] = []
    inicio_stmt = True

    indent_stack: list[int] = []
    match_columns: list[int] = []
    match_depth = 0
    pending_match = False
    pending_match_column = -1

    for indice, actual in enumerate(tokens):
        if actual.type == token.INDENT:
            indent_stack.append(len(actual.string))
            if pending_match:
                match_columns.append(pending_match_column)
                match_depth += 1
                pending_match = False
            pending_match_column = -1
            inicio_stmt = True
            salida.append(actual)
            continue

        if actual.type == token.DEDENT:
            if indent_stack:
                indent_stack.pop()
            current_column = indent_stack[-1] if indent_stack else 0
            while match_columns and current_column < match_columns[-1]:
                match_columns.pop()
                match_depth -= 1
            if pending_match:
                pending_match = False
                pending_match_column = -1
            inicio_stmt = True
            salida.append(actual)
            continue

        if actual.type == token.NEWLINE:
            inicio_stmt = True
            salida.append(actual)
            continue

        if actual.type == tokenize.NL:
            inicio_stmt = True
            salida.append(actual)
            continue

        if actual.type in (token.ENCODING, token.ENDMARKER, token.COMMENT):
            salida.append(actual)
            continue

        if pending_match and inicio_stmt and actual.type == token.NAME:
            pending_match = False
            pending_match_column = -1

        reemplazo: str | None = None
        categoria = ""

        if actual.type == token.NAME:
            anterior = _anterior_significativo(tokens, indice)
            es_atributo = anterior is not None and anterior.type == token.OP and anterior.string == "."

            if not es_atributo and actual.string in HARD_KEYWORDS:
                reemplazo = HARD_KEYWORDS[actual.string]
                categoria = "keyword"
                inicio_stmt = False

            elif not es_atributo and actual.string in SOFT_KEYWORDS:
                es_keyword = False
                if inicio_stmt:
                    if actual.string == "segun" and _es_contexto_segun(tokens, indice):
                        es_keyword = True
                        pending_match = True
                        pending_match_column = actual.start[1]
                    elif actual.string == "caso" and match_depth > 0 and _es_contexto_caso(tokens, indice):
                        es_keyword = True
                    elif actual.string == "tipo" and _es_contexto_tipo(tokens, indice):
                        es_keyword = True

                if es_keyword:
                    reemplazo = SOFT_KEYWORDS[actual.string]
                    categoria = "soft-keyword"
                    inicio_stmt = False
                elif estricto:
                    raise PitonStrictError(
                        archivo,
                        f"nombre suave '{actual.string}' usado como identificador",
                        actual.start[0],
                        actual.start[1] + 1,
                    )
                else:
                    inicio_stmt = False

            elif _es_carga_builtin(tokens, indice, actual.string, asignados):
                siguiente = _siguiente_significativo(tokens, indice)
                if siguiente is not None and siguiente.type == token.OP and siguiente.string == "(":
                    categoria = "builtin-call"
                else:
                    categoria = "builtin-load"
                reemplazo = ALIASES_BUILTIN[actual.string]
                inicio_stmt = False
            else:
                inicio_stmt = False

        elif actual.type == token.OP and actual.string == ";":
            inicio_stmt = True
        else:
            inicio_stmt = False

        if reemplazo is not None:
            salida.append(actual._replace(string=reemplazo))
            cambios.append(CambioToken(actual.start[0], actual.start[1] + 1, actual.string, reemplazo, categoria))
            mapa.agregar(actual.end[0], actual.end[1], actual.start[0], actual.start[1])
        else:
            salida.append(actual)

    return salida, cambios, mapa


def traducir_fuente(
    fuente: str,
    archivo: str = "<entrada>",
    validar: bool = True,
    estricto: bool = False,
) -> str:
    tokens, _, mapa = analizar_tokens(fuente, archivo, estricto=estricto)
    traducido = tokenize.untokenize(tokens)
    if validar:
        try:
            ast.parse(traducido, filename=archivo)
        except SyntaxError as error:
            orig_linea, orig_col, orig_text = _remapear_ubicacion(error, mapa)
            raise PitonSyntaxError(
                archivo,
                error.msg,
                orig_linea,
                orig_col,
                orig_text,
            ) from error
    return traducido


def traducir_fuente_con_mapa(
    fuente: str,
    archivo: str = "<entrada>",
    validar: bool = True,
    estricto: bool = False,
) -> tuple[str, MapaFuente]:
    tokens, _, mapa = analizar_tokens(fuente, archivo, estricto=estricto)
    traducido = tokenize.untokenize(tokens)
    if validar:
        try:
            ast.parse(traducido, filename=archivo)
        except SyntaxError as error:
            orig_linea, orig_col, orig_text = _remapear_ubicacion(error, mapa)
            raise PitonSyntaxError(
                archivo,
                error.msg,
                orig_linea,
                orig_col,
                orig_text,
            ) from error
    return traducido, mapa


def leer_fuente(ruta: str | Path) -> str:
    try:
        with tokenize.open(ruta) as archivo:
            return archivo.read()
    except (OSError, SyntaxError, UnicodeError) as error:
        raise PitonSyntaxError(str(ruta), f"No se pudo leer la fuente: {error}") from error


def traducir_archivo(ruta: str | Path, validar: bool = True, estricto: bool = False) -> str:
    ruta = Path(ruta)
    return traducir_fuente(leer_fuente(ruta), str(ruta), validar=validar, estricto=estricto)
