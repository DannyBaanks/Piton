# piton/lexer.py
"""Lexer para Pitón — tokens con posiciones exactas para source maps."""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import Enum, auto
from typing import Iterator, Optional


class TokenType(Enum):
    # Literales
    STRING = auto()
    FSTRING_START = auto()
    FSTRING_MIDDLE = auto()
    FSTRING_END = auto()
    FSTRING_EXPR_START = auto()
    FSTRING_EXPR_END = auto()
    NUMBER = auto()

    # Identificadores y keywords
    NAME = auto()

    # Operadores y puntuación
    PLUS = auto()
    MINUS = auto()
    STAR = auto()
    SLASH = auto()
    DOUBLE_STAR = auto()
    DOUBLE_SLASH = auto()
    PERCENT = auto()
    AT = auto()

    EQUAL = auto()
    PLUS_EQUAL = auto()
    MINUS_EQUAL = auto()
    STAR_EQUAL = auto()
    SLASH_EQUAL = auto()
    DOUBLE_STAR_EQUAL = auto()
    DOUBLE_SLASH_EQUAL = auto()
    PERCENT_EQUAL = auto()
    AT_EQUAL = auto()

    EQ = auto()
    NOT_EQ = auto()
    LT = auto()
    LT_EQ = auto()
    GT = auto()
    GT_EQ = auto()

    AMPER = auto()
    AMPER_EQUAL = auto()
    VBAR = auto()
    VBAR_EQUAL = auto()
    CIRCUMFLEX = auto()
    CIRCUMFLEX_EQUAL = auto()
    LEFT_SHIFT = auto()
    LEFT_SHIFT_EQUAL = auto()
    RIGHT_SHIFT = auto()
    RIGHT_SHIFT_EQUAL = auto()

    TILDE = auto()

    LPAREN = auto()
    RPAREN = auto()
    LBRACKET = auto()
    RBRACKET = auto()
    LBRACE = auto()
    RBRACE = auto()

    COMMA = auto()
    COLON = auto()
    SEMI = auto()
    DOT = auto()
    DOT_DOT_DOT = auto()
    ARROW = auto()

    # Operadores de comparación y pertenencia (keywords en Python)
    IN = auto()
    NOT_IN = auto()
    IS = auto()
    IS_NOT = auto()

    # Keywords (hard) — se tokenizan como NAME y se reclasifican
    # Soft keywords también NAME, reclasificación en parser

    # Especiales
    NEWLINE = auto()
    INDENT = auto()
    DEDENT = auto()
    ENDMARKER = auto()
    COMMENT = auto()
    NL = auto()
    ENCODING = auto()

    # Error
    ERROR = auto()


# Keywords hard de Pitón (español)
HARD_KEYWORDS = {
    "si", "sino_si", "sino", "para", "mientras", "en",
    "funcion", "devolver", "producir", "clase",
    "intentar", "excepto", "finalmente", "lanzar",
    "con", "como", "importar", "desde",
    "Verdadero", "Falso", "Nada",
    "y", "o", "no",
    "romper", "continuar", "pasar",
    "asincrono", "esperar", "afirmar", "borrar",
    "no_local", "es", "global", "lambda",
}

# Soft keywords
SOFT_KEYWORDS = {
    "segun", "caso", "tipo",
}

# Builtin aliases
BUILTIN_ALIASES = {
    "imprimir", "entrada", "rango", "longitud", "enumerar",
    "lista", "diccionario", "conjunto", "tupla",
    "entero", "decimal", "texto", "booleano",
    "abrir", "ordenar",
}


@dataclass(frozen=True, slots=True)
class Token:
    type: TokenType
    value: str
    line: int
    col: int
    start: int
    end: int

    def with_type(self, new_type: TokenType) -> Token:
        return replace(self, type=new_type)

    def __repr__(self) -> str:
        return f"Token({self.type.name}, {self.value!r}, {self.line}:{self.col})"


# Patrones regex para tokens simples
SIMPLE_PATTERNS = [
    (TokenType.DOUBLE_STAR_EQUAL, r"\*\*="),
    (TokenType.DOUBLE_SLASH_EQUAL, r"//="),
    (TokenType.DOUBLE_STAR, r"\*\*"),
    (TokenType.DOUBLE_SLASH, r"//"),
    (TokenType.LEFT_SHIFT_EQUAL, r"<<="),
    (TokenType.RIGHT_SHIFT_EQUAL, r">>="),
    (TokenType.LEFT_SHIFT, r"<<"),
    (TokenType.RIGHT_SHIFT, r">>"),
    (TokenType.PLUS_EQUAL, r"\+="),
    (TokenType.MINUS_EQUAL, r"-="),
    (TokenType.STAR_EQUAL, r"\*="),
    (TokenType.SLASH_EQUAL, r"/="),
    (TokenType.PERCENT_EQUAL, r"%="),
    (TokenType.AT_EQUAL, r"@="),
    (TokenType.AMPER_EQUAL, r"&="),
    (TokenType.VBAR_EQUAL, r"\|="),
    (TokenType.CIRCUMFLEX_EQUAL, r"\^="),
    (TokenType.NOT_EQ, r"!="),
    (TokenType.EQ, r"=="),
    (TokenType.LT_EQ, r"<="),
    (TokenType.GT_EQ, r">="),
    (TokenType.ARROW, r"->"),
    (TokenType.DOT_DOT_DOT, r"\.\.\."),
    (TokenType.PLUS, r"\+"),
    (TokenType.MINUS, r"-"),
    (TokenType.STAR, r"\*"),
    (TokenType.SLASH, r"/"),
    (TokenType.PERCENT, r"%"),
    (TokenType.AT, r"@"),
    (TokenType.AMPER, r"&"),
    (TokenType.VBAR, r"\|"),
    (TokenType.CIRCUMFLEX, r"\^"),
    (TokenType.TILDE, r"~"),
    (TokenType.LT, r"<"),
    (TokenType.GT, r">"),
    (TokenType.EQUAL, r"="),
    (TokenType.LPAREN, r"\("),
    (TokenType.RPAREN, r"\)"),
    (TokenType.LBRACKET, r"\["),
    (TokenType.RBRACKET, r"\]"),
    (TokenType.LBRACE, r"\{"),
    (TokenType.RBRACE, r"\}"),
    (TokenType.COMMA, r","),
    (TokenType.COLON, r":"),
    (TokenType.SEMI, r";"),
    (TokenType.DOT, r"\."),
]

# Compilar regex una vez
SIMPLE_REGEX = [(tt, re.compile(pat)) for tt, pat in SIMPLE_PATTERNS]

# Palabras que pueden empezar un número
NUMBER_START = set("0123456789")

# Identificador: ASCII letter/underscore + alnum/underscore
# Para Unicode completo se necesitaría el módulo `regex`
IDENTIFIER_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")


class Lexer:
    def __init__(self, source: str, filename: str = "<source>"):
        self.source = source
        self.filename = filename
        self.pos = 0
        self.line = 1
        self.col = 1
        self.line_start = 0
        self.indent_stack = [0]
        self.pending_dedents = 0
        self.at_line_start = True
        self.in_fstring = False
        self.fstring_depth = 0

    def tokenize(self) -> list[Token]:
        tokens = []
        while self.pos < len(self.source):
            tok = self._next_token()
            if tok:
                tokens.append(tok)
        # Emitir DEDENTS finales
        while len(self.indent_stack) > 1:
            tokens.append(self._make_dedent())
            self.indent_stack.pop()
        tokens.append(Token(TokenType.ENDMARKER, "", self.line, self.col, self.pos, self.pos))
        return tokens

    def _peek(self, n: int = 1) -> str:
        if self.pos + n - 1 < len(self.source):
            return self.source[self.pos + n - 1]
        return "\0"

    def _advance(self, n: int = 1) -> str:
        ch = self.source[self.pos:self.pos + n]
        self.pos += n
        for c in ch:
            if c == "\n":
                self.line += 1
                self.col = 1
                self.line_start = self.pos
            else:
                self.col += 1
        return ch

    def _make_token(self, type_: TokenType, value: str) -> Token:
        start = self.pos - len(value)
        line = self.line
        col = self.col - len(value)
        # Ajustar línea/col si el token tiene newlines internos
        if "\n" in value:
            lines = value.split("\n")
            line = self.line - len(lines) + 1
            col = len(lines[-1]) + 1
        return Token(type_, value, line, col, start, self.pos)

    def _next_token(self) -> Optional[Token]:
        if self.pos >= len(self.source):
            return None

        ch = self._peek()

        # FIN DE ARCHIVO
        if ch == "\0":
            return None

        # COMENTARIO
        if ch == "#":
            return self._read_comment()

        # WHITESPACE (no newline)
        if ch in " \t\r\f\v":
            return self._read_whitespace()

        # NEWLINE
        if ch == "\n":
            return self._read_newline()

        # STRING / F-STRING
        if ch in "\"'":
            return self._read_string()

        # NÚMERO
        if ch in NUMBER_START:
            return self._read_number()

        # IDENTIFICADOR / KEYWORD
        if ch.isalpha() or ch == "_":
            return self._read_identifier()

        # OPERADORES Y PUNTUACIÓN
        for tok_type, regex in SIMPLE_REGEX:
            match = regex.match(self.source, self.pos)
            if match:
                value = match.group(0)
                self._advance(len(value))
                return self._make_token(tok_type, value)

        # CARÁCTER NO RECONOCIDO
        self._advance()
        return self._make_token(TokenType.ERROR, ch)

    def _read_whitespace(self) -> Token:
        start = self.pos
        while self._peek() in " \t\r\f\v":
            self._advance()
        return self._make_token(TokenType.NL, self.source[start:self.pos])

    def _read_newline(self) -> Token:
        self._advance()  # consume \n
        # Manejar indentación en la siguiente línea
        indent = 0
        while self._peek() in " \t":
            if self._peek() == " ":
                indent += 1
            else:  # tab
                indent = (indent // 8 + 1) * 8
            self._advance()

        # Línea vacía o comentario → emitir NL
        if self._peek() in ("\n", "#", "\0"):
            return self._make_token(TokenType.NL, "\n")

        # Calcular DEDENT/INDENT
        current_indent = self.indent_stack[-1]
        if indent > current_indent:
            self.indent_stack.append(indent)
            return self._make_token(TokenType.INDENT, " " * (indent - current_indent))
        elif indent < current_indent:
            # Emitir múltiples DEDENTs
            while indent < self.indent_stack[-1]:
                self.pending_dedents += 1
                self.indent_stack.pop()
            if self.pending_dedents > 0:
                self.pending_dedents -= 1
                return self._make_token(TokenType.DEDENT, "")
        return self._make_token(TokenType.NEWLINE, "\n")

    def _make_dedent(self) -> Token:
        return self._make_token(TokenType.DEDENT, "")

    def _read_comment(self) -> Token:
        start = self.pos
        while self._peek() not in ("\n", "\0"):
            self._advance()
        return self._make_token(TokenType.COMMENT, self.source[start:self.pos])

    def _read_number(self) -> Token:
        start = self.pos
        # Hex, octal, binario
        if self._peek() == "0" and self._peek(2) in "xXoObB":
            self._advance(2)
            while self._peek().isalnum() or self._peek() == "_":
                self._advance()
            return self._make_token(TokenType.NUMBER, self.source[start:self.pos])

        # Decimal / float
        has_dot = False
        while True:
            ch = self._peek()
            if ch.isdigit() or ch == "_":
                self._advance()
            elif ch == "." and not has_dot and self._peek(2).isdigit():
                has_dot = True
                self._advance()
            elif ch in "eE" and not has_dot:
                self._advance()
                if self._peek() in "+-":
                    self._advance()
                if not self._peek().isdigit():
                    break
                while self._peek().isdigit() or self._peek() == "_":
                    self._advance()
                break
            else:
                break
        return self._make_token(TokenType.NUMBER, self.source[start:self.pos])

    def _read_identifier(self) -> Token:
        start = self.pos
        match = IDENTIFIER_RE.match(self.source, self.pos)
        if match:
            self.pos = match.end()
            self.col += match.end() - match.start()
        value = self.source[start:self.pos]

        # Operadores de comparación que son keywords en Python
        if value == "en":
            return self._make_token(TokenType.IN, value)
        if value == "no" and self._peek() == " " and self.source.startswith("en", self.pos + 1):
            self._advance()  # consume space
            self._advance(2)  # consume "en"
            return self._make_token(TokenType.NOT_IN, "no en")
        if value == "es":
            return self._make_token(TokenType.IS, value)
        if value == "no" and self._peek() == " " and self.source.startswith("es", self.pos + 1):
            self._advance()  # consume space
            self._advance(2)  # consume "es"
            return self._make_token(TokenType.IS_NOT, "no es")

        tok = self._make_token(TokenType.NAME, value)

        # Reclasificar keywords hard
        if value in HARD_KEYWORDS:
            return tok.with_type(TokenType.NAME)  # Parser decide
        return tok

    def _read_string(self) -> Token:
        quote = self._peek()
        is_triple = self.source.startswith(quote * 3, self.pos)
        delimiter = quote * 3 if is_triple else quote
        is_fstring = False

        # Check for f-string prefix
        if self.pos > 0 and self.source[self.pos - 1].lower() == "f":
            # Need to check if it's actually a prefix (not part of identifier)
            prefix_pos = self.pos - 1
            while prefix_pos > 0 and self.source[prefix_pos - 1].isalpha():
                prefix_pos -= 1
            if prefix_pos == self.pos - 1 or self.source[prefix_pos - 1] in " \t\n\r\f\v([{,;:+-*/%=&|^~<>!@":
                is_fstring = True

        start = self.pos
        self._advance(len(delimiter))

        content_start = self.pos
        while True:
            ch = self._peek()
            if ch == "\0":
                break
            if is_triple:
                if self.source.startswith(delimiter, self.pos):
                    self._advance(len(delimiter))
                    break
            else:
                if ch == "\n":
                    break
                if ch == quote and (self.pos == content_start or self.source[self.pos - 1] != "\\"):
                    self._advance()
                    break
            self._advance()

        value = self.source[start:self.pos]

        if is_fstring:
            # Para f-strings, tokenizar como STRING simple
            # El parser manejará la interpolación
            return self._make_token(TokenType.STRING, value)

        return self._make_token(TokenType.STRING, value)


def tokenize(source: str, filename: str = "<source>") -> list[Token]:
    """Función de conveniencia."""
    return Lexer(source, filename).tokenize()