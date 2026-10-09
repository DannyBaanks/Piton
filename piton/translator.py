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
    # alias que el subconjunto nativo ya soporta pero el traductor dejaba
    # sin traducir: el oracle recibia `suma(...)` y moria con NameError,
    # haciendo que un programa valido pareciera divergente.
    "suma": "sum",
    "redondear": "round",
    "es_instancia": "isinstance",
    "es_subclase": "issubclass",
    "tiene_atr": "hasattr",
    "obtener_atr": "getattr",
    "fijar_atr": "setattr",
    "establecer_atr": "setattr",
    "metodo_estatico": "staticmethod",
    "metodo_clase": "classmethod",
    "propiedad": "property",
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



_ALIAS_NUNCA_OCULTABLES = frozenset({
    "imprimir", "print", "entrada", "input", "rango", "range", "longitud", "len",
    "enumerar", "enumerate", "lista", "list", "diccionario", "dict", "conjunto",
    "set", "tupla", "tuple", "entero", "int", "decimal", "float", "texto", "str",
    "booleano", "bool", "abrir", "open", "ordenar", "sorted",
})


def _nombres_de_star_import(tokens: list[tokenize.TokenInfo]) -> set[str]:
    """Con `... importar *` no se sabe que nombres trae el modulo; solo se
    bloquea el alias para los nombres que el propio archivo USA, que es donde
    una colision es observable."""
    hay_star = False
    for indice, tok in enumerate(tokens):
        anterior = _anterior_significativo(tokens, indice)
        if (
            tok.type == tokenize.OP and tok.string == "*"
            and anterior is not None and anterior.type == tokenize.NAME
            and anterior.string in {"importar", "import"}
        ):
            hay_star = True
            break
    if not hay_star:
        return set()
    usados: set[str] = set()
    for indice, tok in enumerate(tokens):
        if tok.type != tokenize.NAME or tok.string not in ALIASES_BUILTIN:
            continue
        # `imprimir(...)`, `longitud(...)` etc. son builtins del lenguaje y no
        # pueden venir de un star-import: nunca se bloquean. Lo que se bloquea
        # es un alias MAS RARO (suma, redondear) que podria venir del modulo.
        if tok.string in _ALIAS_NUNCA_OCULTABLES:
            continue
        usados.add(tok.string)
    return usados


def _nombres_definidos_por_usuario(tokens: list[tokenize.TokenInfo]) -> set[str]:
    """Nombres que ocultan un builtin a NIVEL DE MODULO.

    Solo una definicion de nivel de modulo oculta el builtin: un metodo
    (`clase Caja: funcion imprimir(self)`) vive en el espacio de nombres de la
    clase y NO oculta el builtin global `imprimir`. Sin esta distincion, el
    ejemplo `examples/07_seguridad_lexica.piton` dejaba de traducir sus
    llamadas globales a `imprimir` y el programa moria con NameError.

    SHADOW_ALIAS_V1: un alias de builtin (`suma` -> `sum`) no debe reescribir
    el nombre del usuario. Incluye `def`/`funcion` de nivel de modulo, nombres
    de clase y nombres importados explicitamente. Las definiciones anidadas en
    una funcion se tratan tambien como shadowing (conservador: el alias no se
    aplica y el nombre sobrevive intacto).
    """
    definidos: set[str] = set()
    profundidad = 0
    cuerpos_de_clase: set[int] = set()
    pendiente_clase = False
    en_import = False
    anterior: tokenize.TokenInfo | None = None
    ignorados = {tokenize.NL, tokenize.COMMENT, tokenize.ENCODING, tokenize.ENDMARKER}

    for actual in tokens:
        if actual.type == tokenize.INDENT:
            profundidad += 1
            if pendiente_clase:
                cuerpos_de_clase.add(profundidad)
            pendiente_clase = False
            continue
        if actual.type == tokenize.DEDENT:
            cuerpos_de_clase.discard(profundidad)
            profundidad = max(0, profundidad - 1)
            continue
        if actual.type == tokenize.NEWLINE:
            en_import = False
            continue
        if actual.type in ignorados:
            continue
        if actual.type == tokenize.NAME:
            if actual.string in {"clase", "class"}:
                pendiente_clase = True
                en_import = False
                continue
            if actual.string in {"importar", "import"}:
                en_import = True
                continue
            if actual.string in {"desde", "from"}:
                if anterior is None or anterior.string not in {"producir", "yield"}:
                    en_import = True
                    continue
            if en_import:
                definidos.add(actual.string)
            elif (
                anterior is not None
                and anterior.type == tokenize.NAME
                and anterior.string in {"def", "funcion"}
                and profundidad not in cuerpos_de_clase
            ):
                definidos.add(actual.string)
        if actual.type == tokenize.OP and actual.string == "(":
            en_import = False
        if actual.type == tokenize.NAME or actual.type == tokenize.OP:
            anterior = actual

    return definidos


def _es_llamada_builtin(tokens: list[tokenize.TokenInfo], indice: int) -> bool:
    """True when a soft keyword is used as a CALL: `tipo(` -> `type(`."""
    siguiente = _siguiente_significativo(tokens, indice)
    return (
        siguiente is not None
        and siguiente.type == token.OP
        and siguiente.string == "("
    )


def _es_contexto_tipo(tokens: list[tokenize.TokenInfo], indice: int) -> bool:
    """True when `tipo` spells `type` rather than being a plain name.

    Besides the `segun`/`caso tipo:` pattern (a NAME follows), the CALL form
    `tipo(x)` is also the builtin: the native backends implement `tipo`, so the
    translation must say `type(x)`. It used to emit `tipo(1)`, which Python
    cannot resolve — the oracle raised NameError while the native program
    printed `<class 'int'>`, and every `tipo()` case was measured DIVERGENT.
    """
    siguiente = _siguiente_significativo(tokens, indice)
    if siguiente is None:
        return False
    if siguiente.type == token.NAME:
        return True
    if siguiente.type == token.OP and siguiente.string == "(":
        return True
    return False


# KEYWORD_EXPR_V1: Spanish keywords that spell an EXPRESSION operator and are
# therefore indistinguishable from a plain identifier by text alone.
# `y = 5` (a variable named y) used to translate to `and = 5`, which is not
# valid Python, so the translator emitted a program that could not even be
# parsed. Statement keywords (`si`, `para`, ...) are NOT in this set: they can
# only appear at statement start, so they always translate.
_KEYWORDS_BINARIOS = {"y", "o", "es", "en"}
_KEYWORDS_UNARIOS = {"no", "esperar"}
_KEYWORDS_INFIJOS = {"como"}
_KEYWORDS_EXPRESION = _KEYWORDS_BINARIOS | _KEYWORDS_UNARIOS | _KEYWORDS_INFIJOS
# VALUE keywords are translated too, but they are CONSTANTS: they end an
# expression, so `imprimir(Verdadero y 1)` must still read `y` as `and`.
# Statement keywords (`si`, `para`, ...) never end one.
_KEYWORDS_VALOR = {"Verdadero", "Falso", "Nada"}
_KEYWORDS_NO_TERMINAN = (set(HARD_KEYWORDS) - _KEYWORDS_VALOR) | set(SOFT_KEYWORDS)


def _termina_expresion(token_: "tokenize.TokenInfo | None") -> bool:
    """True when `token_` can end an expression (so the next NAME is an operator)."""
    if token_ is None:
        return False
    if token_.type in (token.NUMBER, token.STRING):
        return True
    if token_.type == token.OP and token_.string in {")", "]", "}"}:
        return True
    if token_.type == token.NAME:
        if (token_.start[0], token_.start[1]) in _IDENTIFICADORES_DECIDIDOS:
            return True
        return token_.string not in _KEYWORDS_NO_TERMINAN
    return False


def _empieza_expresion(token_: "tokenize.TokenInfo | None") -> bool:
    """True when `token_` can start an expression (so a unary keyword applies)."""
    if token_ is None:
        return False
    if token_.type in (token.NUMBER, token.STRING):
        return True
    # `{` opens a set/dict literal, which is a valid right operand:
    # `1 en {1, 2}` is `1 in {1, 2}`. Omitting it left `en` untranslated.
    # A SIGN also starts an expression: `no -1` is `not -1` and `1 y -2` is
    # `1 and -2`. Without the sign the unary keyword was left untranslated and
    # the oracle got `no -1`, which Python cannot resolve.
    if token_.type == token.OP and token_.string in {"(", "[", "{", "-", "+", "~"}:
        return True
    if token_.type == token.NAME:
        return True
    return False


_IDENTIFICADORES_DECIDIDOS: "set[tuple[int, int]]" = set()


def _es_keyword_de_expresion(tokens: list[tokenize.TokenInfo], indice: int, nombre: str) -> bool:
    """KEYWORD_EXPR_V1: translate `nombre` only when it sits in operator position.

    A binary keyword needs an expression on BOTH sides (`a y b`); a unary one
    only needs the right side (`no x`, `esperar x`); an infix one needs only the
    left (`con f como g`). Anywhere else the token is an identifier and must be
    left alone — that is what makes `y = 5` translate to `y = 5`.
    """
    anterior = _anterior_significativo(tokens, indice)
    siguiente = _siguiente_significativo(tokens, indice)
    # `no en` / `no es` are ONE operator spelled as two tokens, but only when
    # the `no` itself sits in operator position (it has a left operand). `9 no
    # en [1]` is the pair; `imprimir(no en)` is unary `not` applied to a
    # variable called `en`. The left-operand test uses the decided-identifiers
    # set, so a variable named `y`/`o`/etc. still counts as an operand.
    for pareja in ("en", "es"):
        if (
            nombre == "no"
            and siguiente is not None
            and siguiente.type == token.NAME
            and siguiente.string == pareja
            and _termina_expresion(anterior)
        ):
            return True
        if nombre == pareja and anterior is not None and anterior.type == token.NAME and anterior.string == "no":
            try:
                idx_no = tokens.index(anterior)
            except ValueError:
                idx_no = None
            antes_de_no = _anterior_significativo(tokens, idx_no) if idx_no is not None else None
            if antes_de_no is not None and _termina_expresion(antes_de_no):
                return True
    if nombre in _KEYWORDS_BINARIOS:
        return _termina_expresion(anterior) and _empieza_expresion(siguiente)
    if nombre in _KEYWORDS_UNARIOS:
        return _empieza_expresion(siguiente)
    if nombre in _KEYWORDS_INFIJOS:
        return _termina_expresion(anterior)
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
            # Collect every name in the `global a, b, ...` statement so a
            # later load of a shadowed builtin is not mistranslated. Stop at
            # the statement boundary (NEWLINE / `;`): an earlier version
            # skipped newlines here and swallowed the next statement's first
            # NAME — `global g` followed by `imprimir(g)` marked `imprimir`
            # as assigned, so it was never translated to `print`.
            k = i + 1
            while k < len(tokens):
                tok = tokens[k]
                if tok.type in (token.INDENT, token.DEDENT, token.COMMENT, tokenize.NL):
                    k += 1
                    continue
                if tok.type == token.NAME:
                    asignados.add(tok.string)
                    k += 1
                    continue
                if tok.type == token.OP and tok.string == ",":
                    k += 1
                    continue
                break
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
    if anterior is not None and anterior.type == token.NAME and anterior.string in {"importar", "import"}:
        return False
    if anterior is not None and anterior.type == token.NAME and anterior.string in {"desde", "from"}:
        ant2 = _anterior_significativo(tokens, tokens.index(anterior))
        if ant2 is None or ant2.string not in {"producir", "yield"}:
            return False
    if nombre in asignados:
        return False
    # ALIAS_SHADOW_V1: if the module DEFINES this name, the alias must not
    # rewrite the user's function (`def suma(...)` is theirs, not `sum`).
    if nombre in _nombres_definidos_por_usuario(tokens):
        return False
    if nombre in _nombres_de_star_import(tokens):
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
    _IDENTIFICADORES_DECIDIDOS.clear()
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

            if actual.string == "reversa" and not es_atributo:
                # REVERSE_KW_V1: `reversa=` es el nombre del argumento en
                # PITON; en CPython's firma es `reverse=`.
                siguiente = _siguiente_significativo(tokens, indice)
                if siguiente is not None and siguiente.type == token.OP and siguiente.string == "=":
                    salida.append(actual._replace(string="reverse"))
                    continue

            if actual.string in HARD_KEYWORDS:
                # Hard keywords translate everywhere, including after a dot so
                # `desde . importar x` becomes `from . import x`; a hard keyword
                # can never be a valid attribute name (unlike soft keywords).
                # KEYWORD_EXPR_V1: EXCEPT the expression keywords, whose text is
                # also a legal identifier. Those translate only in operator
                # position, otherwise `y = 5` became `and = 5` — Python that
                # cannot even be parsed, so the oracle path silently broke.
                if actual.string in _KEYWORDS_EXPRESION and not _es_keyword_de_expresion(
                    tokens, indice, actual.string
                ):
                    inicio_stmt = False
                    _IDENTIFICADORES_DECIDIDOS.add((actual.start[0], actual.start[1]))
                    salida.append(actual)
                    continue
                # NO_PAREJA_V1: `no en` / `no es` are ONE operator written as two
                # tokens, and Python's spellings are `not in` / `is not` — a
                # token-by-token mapping produced `not in` (fine) but `not is`
                # (invalid) for the second. The pair is therefore rewritten as a
                # unit: the first token takes the leading word and the second
                # the trailing one, so the order comes out right.
                if actual.string == "no":
                    siguiente = _siguiente_significativo(tokens, indice)
                    if (
                        siguiente is not None
                        and siguiente.type == token.NAME
                        and siguiente.string == "es"
                    ):
                        # `no es` -> `is ... not`
                        reemplazo = "is"
                        categoria = "keyword-pair"
                        inicio_stmt = False
                        out_pair = None
                        salida.append(actual._replace(string="is"))
                        cambios.append(
                            CambioToken(
                                actual.start[0], actual.start[1] + 1,
                                actual.string, "is", "keyword-pair",
                            )
                        )
                        mapa.agregar(actual.end[0], actual.end[1], actual.start[0], actual.start[1])
                        continue
                if actual.string in {"es", "en"}:
                    anterior = _anterior_significativo(tokens, indice)
                    if (
                        anterior is not None
                        and anterior.type == token.NAME
                        and anterior.string == "no"
                        and actual.string == "es"
                    ):
                        # second token of `no es` -> `not`
                        reemplazo = "not"
                        categoria = "keyword-pair"
                        inicio_stmt = False
                        salida.append(actual._replace(string="not"))
                        cambios.append(
                            CambioToken(
                                actual.start[0], actual.start[1] + 1,
                                actual.string, "not", "keyword-pair",
                            )
                        )
                        mapa.agregar(actual.end[0], actual.end[1], actual.start[0], actual.start[1])
                        continue
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
                elif actual.string == "tipo" and _es_llamada_builtin(tokens, indice):
                    # TIPO_CALL_V1: `tipo(x)` is the builtin `type` even in the
                    # middle of an expression. The native backends implement
                    # `tipo`, so the translation must be `type(x)`; leaving it
                    # as `tipo(1)` gave the oracle a NameError while the native
                    # program printed `<class 'int'>`.
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

        if actual.type == token.NAME and reemplazo is None:
            _IDENTIFICADORES_DECIDIDOS.add((actual.start[0], actual.start[1]))
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
