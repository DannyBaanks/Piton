# piton/parser.py
"""Parser recursivo descendente para Pitón — tokens → CST."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, List, Union, Any
from piton.lexer import Token, TokenType, tokenize
from piton.cst import (
    CSTNodeType, Module, ExprStmt, Assign, AnnAssign, AugAssign,
    IfStmt, WhileStmt, ForStmt, FuncDef, ClassDef, ReturnStmt,
    YieldStmt, YieldFromStmt, RaiseStmt, TryStmt, ExceptHandler,
    WithStmt, WithItem, MatchStmt, CaseBlock, ImportStmt, ImportFromStmt,
    Alias, GlobalStmt, NonlocalStmt, AssertStmt, DelStmt,
    PassStmt, BreakStmt, ContinueStmt,
    Name, Constant, BinOp, UnaryOp, Compare, BoolOp, Call, Keyword,
    Attribute, Subscript, Slice, List, Tuple, Set, Dict,
    ListComp, SetComp, DictComp, GenExpr, CompFor, IfExpr, Lambda,
    Await, FString, FStringPart, FStringExpr,
    MatchValue, MatchSingleton, MatchSequence, MatchMapping,
    MatchClass, MatchStar, MatchAs, MatchOr,
    TypeParams, TypeVar, ParamSpec, TypeVarTuple,
    Arguments, Arg, Decorator,
    CSTNode,
)

# Operadores binarios y sus precedencias (mayor = más apretado)
BINOP_PRECEDENCE = {
    "**": 13,
    "*": 12, "/": 12, "//": 12, "%": 12, "@": 12,
    "+": 11, "-": 11,
    "<<": 10, ">>": 10,
    "&": 9,
    "^": 8,
    "|": 7,
    # Comparaciones son no-asociativas, manejadas aparte
    "in": 6, "not in": 6, "is": 6, "is not": 6,
    "<": 6, "<=": 6, ">": 6, ">=": 6, "==": 6, "!=": 6,
    "y": 5,   # and
    "o": 4,   # or
}

# Keywords que son operadores en expresiones
COMPARE_OPS = {"==", "!=", "<", "<=", ">", ">=", "es", "no es", "en", "no en"}

# Traducción de operadores Pitón -> Python
OP_MAP = {
    "y": "and", "o": "or", "no": "not",
    "es": "is", "no es": "is not",
    "en": "in", "no en": "not in",
    "mas": "+", "menos": "-", "por": "*", "div": "/",
    "div_entera": "//", "mod": "%", "pot": "**",
}

SOFT_KEYWORDS = {"segun", "caso", "tipo"}
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


@dataclass
class Parser:
    tokens: List[Token]
    pos: int = 0
    filename: str = "<source>"

    def parse(self) -> Module:
        module = Module()
        module.line = 1
        module.col = 1
        while not self._at_end() and not self._check(TokenType.ENDMARKER):
            stmt = self._parse_statement()
            if stmt:
                module.body.append(stmt)
        module.end_line = self.line
        module.end_col = self.col
        return module

    # ---- Utilidades de navegación ----

    def _peek(self) -> Token:
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return Token(TokenType.ENDMARKER, "", self.line, self.col, len(self.source), len(self.source))

    def _peek_n(self, n: int) -> Token:
        if self.pos + n < len(self.tokens):
            return self.tokens[self.pos + n]
        return Token(TokenType.ENDMARKER, "", self.line, self.col, len(self.source), len(self.source))

    def _advance(self) -> Token:
        tok = self._peek()
        self.pos += 1
        return tok

    def _check(self, *types: TokenType) -> bool:
        return self._peek().type in types

    def _match(self, *types: TokenType) -> bool:
        if self._check(*types):
            self._advance()
            return True
        return False

    def _consume(self, type_: TokenType, msg: str = "") -> Token:
        if self._check(type_):
            return self._advance()
        tok = self._peek()
        raise ParseError(f"Se esperaba {type_.name}: {msg}", tok)

    def _at_end(self) -> bool:
        return self._peek().type == TokenType.ENDMARKER

    @property
    def line(self) -> int:
        return self._peek().line

    @property
    def col(self) -> int:
        return self._peek().col

    @property
    def source(self) -> str:
        # Para errores, necesitamos el source original
        return getattr(self, "_source", "")

    # ---- Parsing de statements ----

    def _parse_statement(self) -> Optional[CSTNode]:
        # Saltar newlines, indents, dedents sueltos
        while self._check(TokenType.NEWLINE, TokenType.NL, TokenType.INDENT, TokenType.DEDENT):
            self._advance()
        if self._at_end():
            return None

        tok = self._peek()

        # Decoradores
        decorators = []
        while self._check(TokenType.NAME) and self._peek().value == "@":
            self._advance()  # consume @
            decorators.append(self._parse_decorator())

        # Keywords de statement
        if tok.type == TokenType.NAME:
            val = tok.value
            if val == "si":
                return self._parse_if()
            elif val == "mientras":
                return self._parse_while()
            elif val == "para":
                return self._parse_for()
            elif val == "funcion":
                return self._parse_func_def(decorators)
            elif val == "asincrono" and self._peek_n(1).value == "funcion":
                self._advance()  # consume asincrono
                return self._parse_func_def(decorators, is_async=True)
            elif val == "clase":
                return self._parse_class_def(decorators)
            elif val == "devolver":
                return self._parse_return()
            elif val == "producir":
                if self._peek_n(1).value == "desde":
                    return self._parse_yield_from()
                return self._parse_yield()
            elif val == "intentar":
                return self._parse_try()
            elif val == "con":
                return self._parse_with()
            elif val == "asincrono" and self._peek_n(1).value == "con":
                self._advance()
                return self._parse_with(is_async=True)
            elif val == "asincrono" and self._peek_n(1).value == "para":
                self._advance()
                return self._parse_for(is_async=True)
            elif val == "segun":
                return self._parse_match()
            elif val == "importar":
                return self._parse_import()
            elif val == "desde":
                return self._parse_import_from()
            elif val == "global":
                return self._parse_global()
            elif val == "no_local":
                return self._parse_nonlocal()
            elif val == "afirmar":
                return self._parse_assert()
            elif val == "borrar":
                return self._parse_del()
            elif val == "pasar":
                self._advance()
                return PassStmt().set_pos(tok)
            elif val == "romper":
                self._advance()
                return BreakStmt().set_pos(tok)
            elif val == "continuar":
                self._advance()
                return ContinueStmt().set_pos(tok)
            elif val == "tipo":
                return self._parse_type_alias()

        # Expression statement / assignment
        return self._parse_expr_stmt()

    def _parse_decorator(self) -> CSTNode:
        tok = self._peek()
        expr = self._parse_expression()
        return Decorator(func=expr, decorators=[]).set_pos(tok)

    def _parse_if(self) -> IfStmt:
        tok = self._advance()  # consume 'si'
        test = self._parse_expression()
        self._consume(TokenType.COLON)
        body = self._parse_block()
        orelse = []
        while self._check(TokenType.NAME) and self._peek().value in ("sino_si", "sino"):
            if self._peek().value == "sino_si":
                self._advance()
                elif_test = self._parse_expression()
                self._consume(TokenType.COLON)
                elif_body = self._parse_block()
                orelse.append(IfStmt(test=elif_test, body=elif_body, orelse=[]).set_pos(tok))
            else:  # sino
                self._advance()
                self._consume(TokenType.COLON)
                orelse = self._parse_block()
        return IfStmt(test=test, body=body, orelse=orelse).set_pos(tok)

    def _parse_while(self) -> WhileStmt:
        tok = self._advance()  # consume 'mientras'
        test = self._parse_expression()
        self._consume(TokenType.COLON)
        body = self._parse_block()
        orelse = []
        if self._check(TokenType.NAME) and self._peek().value == "sino":
            self._advance()
            self._consume(TokenType.COLON)
            orelse = self._parse_block()
        return WhileStmt(test=test, body=body, orelse=orelse).set_pos(tok)

    def _parse_for(self, is_async: bool = False) -> ForStmt:
        tok = self._advance()  # consume 'para'
        target = self._parse_expression()
        self._consume(TokenType.NAME, "se esperaba 'en'")  # 'en'
        if self._peek().value != "en":
            raise ParseError("Se esperaba 'en' en bucle para", self._peek())
        self._advance()
        iter_ = self._parse_expression()
        self._consume(TokenType.COLON)
        body = self._parse_block()
        orelse = []
        if self._check(TokenType.NAME) and self._peek().value == "sino":
            self._advance()
            self._consume(TokenType.COLON)
            orelse = self._parse_block()
        return ForStmt(target=target, iter=iter_, body=body, orelse=orelse, is_async=is_async).set_pos(tok)

    def _parse_func_def(self, decorators: List, is_async: bool = False) -> FuncDef:
        tok = self._advance()  # consume 'funcion'
        name = self._consume(TokenType.NAME, "nombre de función").value
        self._consume(TokenType.LPAREN)
        args = self._parse_arguments()
        self._consume(TokenType.RPAREN)
        returns = None
        if self._check(TokenType.ARROW):
            self._advance()
            returns = self._parse_expression()
        self._consume(TokenType.COLON)
        body = self._parse_block()
        type_params = None
        # TODO: type params [T] después de nombre
        return FuncDef(
            name=name, args=args, body=body, returns=returns,
            decorators=decorators, is_async=is_async
        ).set_pos(tok)

    def _parse_class_def(self, decorators: List) -> ClassDef:
        tok = self._advance()  # consume 'clase'
        name = self._consume(TokenType.NAME, "nombre de clase").value
        bases = []
        keywords = []
        if self._check(TokenType.LPAREN):
            self._advance()
            while not self._check(TokenType.RPAREN):
                bases.append(self._parse_expression())
                if not self._match(TokenType.COMMA):
                    break
            self._consume(TokenType.RPAREN)
        if self._check(TokenType.NAME) and self._peek().value in ("como", "metaclass"):
            # TODO: keywords de clase
            pass
        self._consume(TokenType.COLON)
        body = self._parse_block()
        return ClassDef(name=name, bases=bases, keywords=keywords,
                       body=body, decorators=decorators).set_pos(tok)

    def _parse_return(self) -> ReturnStmt:
        tok = self._advance()
        value = None
        if not self._check(TokenType.NEWLINE, TokenType.ENDMARKER, TokenType.DEDENT):
            value = self._parse_expression()
        return ReturnStmt(value=value).set_pos(tok)

    def _parse_yield(self) -> YieldStmt:
        tok = self._advance()
        value = None
        if not self._check(TokenType.NEWLINE, TokenType.ENDMARKER, TokenType.DEDENT):
            value = self._parse_expression()
        return YieldStmt(value=value).set_pos(tok)

    def _parse_yield_from(self) -> YieldFromStmt:
        tok = self._advance()  # producir
        self._advance()  # desde
        value = self._parse_expression()
        return YieldFromStmt(value=value).set_pos(tok)

    def _parse_try(self) -> TryStmt:
        tok = self._advance()  # intentar
        self._consume(TokenType.COLON)
        body = self._parse_block()
        handlers = []
        orelse = []
        finalbody = []
        while self._check(TokenType.NAME):
            val = self._peek().value
            if val == "excepto":
                handlers.append(self._parse_except_handler())
            elif val == "sino":
                self._advance()
                self._consume(TokenType.COLON)
                orelse = self._parse_block()
            elif val == "finalmente":
                self._advance()
                self._consume(TokenType.COLON)
                finalbody = self._parse_block()
            else:
                break
        return TryStmt(body=body, handlers=handlers,
                      orelse=orelse, finalbody=finalbody).set_pos(tok)

    def _parse_except_handler(self) -> ExceptHandler:
        tok = self._advance()  # excepto
        is_star = False
        if self._match(TokenType.STAR):
            is_star = True
        type_ = None
        name = None
        if not self._check(TokenType.COLON):
            type_ = self._parse_expression()
            if self._check(TokenType.NAME) and self._peek().value == "como":
                self._advance()
                name = self._consume(TokenType.NAME).value
        self._consume(TokenType.COLON)
        body = self._parse_block()
        return ExceptHandler(type_=type_, name=name, body=body, is_star=is_star).set_pos(tok)

    def _parse_with(self, is_async: bool = False) -> WithStmt:
        tok = self._advance()  # con
        items = []
        while True:
            ctx = self._parse_expression()
            vars_ = None
            if self._check(TokenType.NAME) and self._peek().value == "como":
                self._advance()
                vars_ = self._parse_expression()
            items.append(WithItem(context_expr=ctx, optional_vars=vars_).set_pos(tok))
            if not self._match(TokenType.COMMA):
                break
        self._consume(TokenType.COLON)
        body = self._parse_block()
        return WithStmt(items=items, body=body, is_async=is_async).set_pos(tok)

    def _parse_match(self) -> MatchStmt:
        tok = self._advance()  # segun
        subject = self._parse_expression()
        self._consume(TokenType.COLON)
        cases = []
        while True:
            # Saltar newlines/indents
            while self._check(TokenType.NEWLINE, TokenType.NL, TokenType.INDENT):
                self._advance()
            if self._check(TokenType.DEDENT) or self._at_end():
                break
            if not self._check(TokenType.NAME) or self._peek().value != "caso":
                break
            cases.append(self._parse_case())
        return MatchStmt(subject=subject, cases=cases).set_pos(tok)

    def _parse_case(self) -> CaseBlock:
        tok = self._advance()  # caso
        pattern = self._parse_pattern()
        guard = None
        if self._check(TokenType.NAME) and self._peek().value == "si":
            self._advance()
            guard = self._parse_expression()
        self._consume(TokenType.COLON)
        body = self._parse_block()
        return CaseBlock(pattern=pattern, guard=guard, body=body).set_pos(tok)

    def _parse_pattern(self) -> CSTNode:
        # Simplificado: solo valor literal, nombre, secuencia, mapeo, clase, star, as, or
        if self._check(TokenType.NAME):
            val = self._peek().value
            if val == "_":
                self._advance()
                return MatchSingleton(value=None).set_pos(self._peek())  # wildcard
            elif val in ("Verdadero", "Falso", "Nada"):
                self._advance()
                v = {"Verdadero": True, "Falso": False, "Nada": None}[val]
                return MatchSingleton(value=v).set_pos(self._peek())
        # TODO: patrones completos
        return self._parse_expression()

    def _parse_import(self) -> ImportStmt:
        tok = self._advance()  # importar
        names = []
        while True:
            name = self._consume(TokenType.NAME).value
            asname = None
            if self._check(TokenType.NAME) and self._peek().value == "como":
                self._advance()
                asname = self._consume(TokenType.NAME).value
            names.append(Alias(name=name, asname=asname).set_pos(tok))
            if not self._match(TokenType.COMMA):
                break
        return ImportStmt(names=names).set_pos(tok)

    def _parse_import_from(self) -> ImportFromStmt:
        tok = self._advance()  # desde
        level = 0
        while self._match(TokenType.DOT):
            level += 1
        module = None
        if self._check(TokenType.NAME):
            module = self._consume(TokenType.NAME).value
        self._consume(TokenType.NAME, "importar")  # 'importar'
        names = []
        while True:
            name = self._consume(TokenType.NAME).value
            asname = None
            if self._check(TokenType.NAME) and self._peek().value == "como":
                self._advance()
                asname = self._consume(TokenType.NAME).value
            names.append(Alias(name=name, asname=asname).set_pos(tok))
            if not self._match(TokenType.COMMA):
                break
        return ImportFromStmt(module=module, names=names, level=level).set_pos(tok)

    def _parse_global(self) -> GlobalStmt:
        tok = self._advance()
        names = []
        while True:
            names.append(self._consume(TokenType.NAME).value)
            if not self._match(TokenType.COMMA):
                break
        return GlobalStmt(names=names).set_pos(tok)

    def _parse_nonlocal(self) -> NonlocalStmt:
        tok = self._advance()
        names = []
        while True:
            names.append(self._consume(TokenType.NAME).value)
            if not self._match(TokenType.COMMA):
                break
        return NonlocalStmt(names=names).set_pos(tok)

    def _parse_assert(self) -> AssertStmt:
        tok = self._advance()  # afirmar
        test = self._parse_expression()
        msg = None
        if self._match(TokenType.COMMA):
            msg = self._parse_expression()
        return AssertStmt(test=test, msg=msg).set_pos(tok)

    def _parse_del(self) -> DelStmt:
        tok = self._advance()  # borrar
        targets = []
        while True:
            targets.append(self._parse_expression())
            if not self._match(TokenType.COMMA):
                break
        return DelStmt(targets=targets).set_pos(tok)

    def _parse_type_alias(self) -> AnnAssign:
        tok = self._advance()  # tipo
        target = self._parse_expression()
        self._consume(TokenType.EQUAL)
        value = self._parse_expression()
        return AnnAssign(target=target, annotation=value, value=None, simple=1).set_pos(tok)

    def _parse_expr_stmt(self) -> CSTNode:
        # Podría ser asignación, augmented assign, annotation, o expresión simple
        start_tok = self._peek()
        expr = self._parse_expression()

        # Verificar si es augmented assign
        if self._check(TokenType.PLUS_EQUAL, TokenType.MINUS_EQUAL,
                       TokenType.STAR_EQUAL, TokenType.SLASH_EQUAL,
                       TokenType.PERCENT_EQUAL, TokenType.AT_EQUAL,
                       TokenType.AMPER_EQUAL, TokenType.VBAR_EQUAL,
                       TokenType.CIRCUMFLEX_EQUAL,
                       TokenType.LEFT_SHIFT_EQUAL, TokenType.RIGHT_SHIFT_EQUAL,
                       TokenType.DOUBLE_STAR_EQUAL, TokenType.DOUBLE_SLASH_EQUAL):
            op_tok = self._advance()
            value = self._parse_expression()
            return AugAssign(target=expr, op=op_tok.value, value=value).set_pos(start_tok)

        # Verificar si es asignación simple
        if self._match(TokenType.EQUAL):
            # Podría ser asignación múltiple
            targets = [expr]
            while self._match(TokenType.COMMA):
                targets.append(self._parse_expression())
            self._consume(TokenType.EQUAL)
            value = self._parse_expression()
            if len(targets) == 1:
                return Assign(targets=targets, value=value).set_pos(start_tok)
            return Assign(targets=targets, value=value).set_pos(start_tok)

        # Verificar si es annotated assignment (tipo Nombre = expr)
        if isinstance(expr, Name) and self._check(TokenType.COLON):
            self._advance()  # :
            annotation = self._parse_expression()
            value = None
            if self._match(TokenType.EQUAL):
                value = self._parse_expression()
            return AnnAssign(target=expr, annotation=annotation, value=value, simple=1).set_pos(start_tok)

        return ExprStmt(value=expr).set_pos(start_tok)

    def _parse_block(self) -> List[CSTNode]:
        """Parsea un bloque indentado."""
        stmts = []
        # Esperar INDENT
        while self._check(TokenType.NEWLINE, TokenType.NL):
            self._advance()
        if not self._match(TokenType.INDENT):
            # Single statement en misma línea
            stmt = self._parse_statement()
            if stmt:
                stmts.append(stmt)
            return stmts

        while not self._check(TokenType.DEDENT) and not self._at_end():
            stmt = self._parse_statement()
            if stmt:
                stmts.append(stmt)
        self._match(TokenType.DEDENT)  # consume DEDENT
        return stmts

    def _parse_arguments(self) -> Arguments:
        args = Arguments()
        # Simplificado: solo positional args por ahora
        while not self._check(TokenType.RPAREN):
            if self._check(TokenType.STAR):
                self._advance()
                if self._check(TokenType.STAR):
                    self._advance()
                    args.kwarg = Arg(arg=self._consume(TokenType.NAME).value).set_pos(self._peek())
                else:
                    args.vararg = Arg(arg=self._consume(TokenType.NAME).value).set_pos(self._peek())
            else:
                name = self._consume(TokenType.NAME).value
                annotation = None
                if self._match(TokenType.COLON):
                    annotation = self._parse_expression()
                default = None
                if self._match(TokenType.EQUAL):
                    default = self._parse_expression()
                arg = Arg(arg=name, annotation=annotation).set_pos(self._peek())
                args.args.append(arg)
                if default:
                    args.defaults.append(default)
            if not self._match(TokenType.COMMA):
                break
        return args

    # ---- Expresiones (precedencia) ----

    def _parse_expression(self) -> CSTNode:
        return self._parse_if_expr()

    def _parse_if_expr(self) -> CSTNode:
        # a si cond sino b
        expr = self._parse_bool_or()
        if self._check(TokenType.NAME) and self._peek().value == "si":
            self._advance()
            test = self._parse_expression()
            self._consume(TokenType.NAME, "sino")  # 'sino'
            orelse = self._parse_expression()
            return IfExpr(test=test, body=expr, orelse=orelse).set_pos(self._peek())
        return expr

    def _parse_bool_or(self) -> CSTNode:
        left = self._parse_bool_and()
        while self._check(TokenType.NAME) and self._peek().value == "o":
            op = self._advance().value
            right = self._parse_bool_and()
            left = BoolOp(op=op, values=[left, right]).set_pos(self._peek())
        return left

    def _parse_bool_and(self) -> CSTNode:
        left = self._parse_comparison()
        while self._check(TokenType.NAME) and self._peek().value == "y":
            op = self._advance().value
            right = self._parse_comparison()
            left = BoolOp(op=op, values=[left, right]).set_pos(self._peek())
        return left

    def _parse_comparison(self) -> CSTNode:
        left = self._parse_bitwise_or()
        ops = []
        comparators = []
        while True:
            if self._check(TokenType.NAME) and self._peek().value in COMPARE_OPS:
                op = self._advance().value
                if op == "no" and self._check(TokenType.NAME) and self._peek().value in ("es", "en"):
                    op += " " + self._advance().value
                right = self._parse_bitwise_or()
                ops.append(op)
                comparators.append(right)
            elif self._check(TokenType.EQ, TokenType.NOT_EQ, TokenType.LT, TokenType.LT_EQ,
                           TokenType.GT, TokenType.GT_EQ, TokenType.IN, TokenType.NOT_IN,
                           TokenType.IS, TokenType.IS_NOT):
                op_tok = self._advance()
                op = op_tok.value
                right = self._parse_bitwise_or()
                ops.append(op)
                comparators.append(right)
            else:
                break
        if ops:
            return Compare(left=left, ops=ops, comparators=comparators).set_pos(self._peek())
        return left

    def _parse_bitwise_or(self) -> CSTNode:
        left = self._parse_bitwise_xor()
        while self._match(TokenType.VBAR):
            right = self._parse_bitwise_xor()
            left = BinOp(left=left, op="|", right=right).set_pos(self._peek())
        return left

    def _parse_bitwise_xor(self) -> CSTNode:
        left = self._parse_bitwise_and()
        while self._match(TokenType.CIRCUMFLEX):
            right = self._parse_bitwise_and()
            left = BinOp(left=left, op="^", right=right).set_pos(self._peek())
        return left

    def _parse_bitwise_and(self) -> CSTNode:
        left = self._parse_shift()
        while self._match(TokenType.AMPER):
            right = self._parse_shift()
            left = BinOp(left=left, op="&", right=right).set_pos(self._peek())
        return left

    def _parse_shift(self) -> CSTNode:
        left = self._parse_additive()
        while self._match(TokenType.LEFT_SHIFT, TokenType.RIGHT_SHIFT):
            op = self._peek().value
            right = self._parse_additive()
            left = BinOp(left=left, op=op, right=right).set_pos(self._peek())
        return left

    def _parse_additive(self) -> CSTNode:
        left = self._parse_multiplicative()
        while self._match(TokenType.PLUS, TokenType.MINUS):
            op = self._peek().value
            right = self._parse_multiplicative()
            left = BinOp(left=left, op=op, right=right).set_pos(self._peek())
        return left

    def _parse_multiplicative(self) -> CSTNode:
        left = self._parse_unary()
        while self._match(TokenType.STAR, TokenType.SLASH, TokenType.DOUBLE_SLASH,
                          TokenType.PERCENT, TokenType.AT):
            op = self._peek().value
            right = self._parse_unary()
            left = BinOp(left=left, op=op, right=right).set_pos(self._peek())
        return left

    def _parse_unary(self) -> CSTNode:
        if self._match(TokenType.PLUS, TokenType.MINUS, TokenType.TILDE):
            op = self._peek().value
            operand = self._parse_unary()
            return UnaryOp(op=op, operand=operand).set_pos(self._peek())
        if self._check(TokenType.NAME) and self._peek().value == "no":
            tok = self._advance()
            operand = self._parse_unary()
            return UnaryOp(op="not", operand=operand).set_pos(tok)
        if self._check(TokenType.NAME) and self._peek().value == "esperar":
            tok = self._advance()
            value = self._parse_unary()
            return Await(value=value).set_pos(tok)
        return self._parse_power()

    def _parse_power(self) -> CSTNode:
        left = self._parse_primary()
        if self._match(TokenType.DOUBLE_STAR):
            right = self._parse_unary()  # right-associative
            left = BinOp(left=left, op="**", right=right).set_pos(self._peek())
        return left

    def _parse_primary(self) -> CSTNode:
        tok = self._peek()

        # Literales
        if tok.type == TokenType.NUMBER:
            self._advance()
            val = tok.value
            # Parsear número
            if val.startswith(("0x", "0X")):
                val = int(val, 16)
            elif val.startswith(("0o", "0O")):
                val = int(val, 8)
            elif val.startswith(("0b", "0B")):
                val = int(val, 2)
            elif "." in val or "e" in val.lower():
                val = float(val)
            else:
                val = int(val)
            return Constant(value=val).set_pos(tok)

        if tok.type == TokenType.STRING:
            self._advance()
            # Quitar quotes
            val = tok.value
            if val.startswith(('"""', "'''")):
                val = val[3:-3]
            elif val.startswith(('"', "'")):
                val = val[1:-1]
            # Manejar escapes
            val = val.encode().decode('unicode_escape')
            return Constant(value=val).set_pos(tok)

        # Identificadores / keywords
        if tok.type == TokenType.NAME:
            val = tok.value
            self._advance()

            # Literales booleanos / None
            if val == "Verdadero":
                return Constant(value=True).set_pos(tok)
            if val == "Falso":
                return Constant(value=False).set_pos(tok)
            if val == "Nada":
                return Constant(value=None).set_pos(tok)

            # Llamada: nombre seguido de (
            if self._check(TokenType.LPAREN):
                return self._parse_call(Name(id=val, ctx="Load").set_pos(tok))

            # Atributo/subscript: nombre seguido de . o [
            node = Name(id=val, ctx="Load").set_pos(tok)
            return self._parse_postfix(node)

        # Paréntesis / tuplas
        if self._match(TokenType.LPAREN):
            if self._check(TokenType.RPAREN):
                self._advance()
                return Tuple(elts=[], ctx="Load").set_pos(tok)
            expr = self._parse_expression()
            if self._match(TokenType.COMMA):
                elts = [expr]
                while not self._check(TokenType.RPAREN):
                    elts.append(self._parse_expression())
                    if not self._match(TokenType.COMMA):
                        break
                self._consume(TokenType.RPAREN)
                return Tuple(elts=elts, ctx="Load").set_pos(tok)
            self._consume(TokenType.RPAREN)
            return expr

        # Listas
        if self._match(TokenType.LBRACKET):
            elts = []
            if not self._check(TokenType.RBRACKET):
                while True:
                    # Comprehension?
                    if self._check(TokenType.NAME) and self._peek().value == "para":
                        comp = self._parse_comprehension()
                        return ListComp(elt=comp.target, generators=[comp]).set_pos(tok)
                    elts.append(self._parse_expression())
                    if not self._match(TokenType.COMMA):
                        break
            self._consume(TokenType.RBRACKET)
            return List(elts=elts, ctx="Load").set_pos(tok)

        # Sets
        if self._match(TokenType.LBRACE):
            if self._check(TokenType.RBRACE):
                self._advance()
                return Set(elts=[]).set_pos(tok)
            # Dict o set
            first = self._parse_expression()
            if self._match(TokenType.COLON):
                # Dict
                keys = [first]
                values = [self._parse_expression()]
                while self._match(TokenType.COMMA):
                    k = self._parse_expression()
                    self._consume(TokenType.COLON)
                    keys.append(k)
                    values.append(self._parse_expression())
                self._consume(TokenType.RBRACE)
                return Dict(keys=keys, values=values).set_pos(tok)
            else:
                # Set
                elts = [first]
                while self._match(TokenType.COMMA):
                    elts.append(self._parse_expression())
                self._consume(TokenType.RBRACE)
                return Set(elts=elts).set_pos(tok)

        # Lambda
        if self._check(TokenType.NAME) and self._peek().value == "lambda":
            self._advance()
            args = Arguments()
            while not self._check(TokenType.COLON):
                name = self._consume(TokenType.NAME).value
                args.args.append(Arg(arg=name).set_pos(self._peek()))
                if not self._match(TokenType.COMMA):
                    break
            self._consume(TokenType.COLON)
            body = self._parse_expression()
            return Lambda(args=args, body=body).set_pos(tok)

        raise ParseError(f"Expresión inesperada: {tok}", tok)

    def _parse_postfix(self, node: CSTNode) -> CSTNode:
        while True:
            if self._match(TokenType.DOT):
                attr = self._consume(TokenType.NAME).value
                node = Attribute(value=node, attr=attr, ctx="Load").set_pos(self._peek())
            elif self._match(TokenType.LBRACKET):
                slice_ = self._parse_expression()
                self._consume(TokenType.RBRACKET)
                node = Subscript(value=node, slice=slice_, ctx="Load").set_pos(self._peek())
            elif self._match(TokenType.LPAREN):
                node = self._parse_call(node)
            else:
                break
        return node

    def _parse_call(self, func: CSTNode) -> Call:
        args = []
        keywords = []
        if not self._check(TokenType.RPAREN):
            while True:
                if self._check(TokenType.NAME) and self._peek_n(1).type == TokenType.EQUAL:
                    # keyword arg
                    arg_name = self._advance().value
                    self._advance()  # =
                    value = self._parse_expression()
                    keywords.append(Keyword(arg=arg_name, value=value).set_pos(self._peek()))
                elif self._check(TokenType.STAR):
                    self._advance()
                    if self._check(TokenType.STAR):
                        self._advance()
                        value = self._parse_expression()
                        keywords.append(Keyword(arg=None, value=value).set_pos(self._peek()))
                    else:
                        value = self._parse_expression()
                        args.append(value)
                elif self._check(TokenType.DOUBLE_STAR):
                    self._advance()
                    value = self._parse_expression()
                    keywords.append(Keyword(arg=None, value=value).set_pos(self._peek()))
                else:
                    args.append(self._parse_expression())
                if not self._match(TokenType.COMMA):
                    break
        self._consume(TokenType.RPAREN)
        return Call(func=func, args=args, keywords=keywords).set_pos(self._peek())

    def _parse_comprehension(self) -> CompFor:
        tok = self._advance()  # 'para'
        is_async = False
        target = self._parse_expression()
        self._consume(TokenType.NAME, "se esperaba 'en'")
        if self._peek().value != "en":
            raise ParseError("Se esperaba 'en'", self._peek())
        self._advance()
        iter_ = self._parse_expression()
        ifs = []
        while self._check(TokenType.NAME) and self._peek().value == "si":
            self._advance()
            ifs.append(self._parse_expression())
        return CompFor(target=target, iter=iter_, ifs=ifs, is_async=is_async).set_pos(tok)


class ParseError(Exception):
    def __init__(self, msg: str, token: Token):
        self.msg = msg
        self.token = token
        super().__init__(f"{msg} en {token.line}:{token.col}")


def parse(source: str, filename: str = "<source>") -> Module:
    """Función de conveniencia."""
    tokens = tokenize(source, filename)
    parser = Parser(tokens)
    parser._source = source
    return parser.parse()