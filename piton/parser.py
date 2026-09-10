# piton/parser.py
"""Parser recursivo descendente para Pitón — tokens → CST."""
from __future__ import annotations

from typing import List, Optional, Union
from piton.lexer import Token, TokenType, tokenize
from piton.cst import (
    CSTNode, CSTNodeType, Module, ExprStmt, Assign, AnnAssign, AugAssign,
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
)

# Precedencia de operadores (mayor = más apretado)
PRECEDENCE = {
    "lambda": 1,
    "if_expr": 2,
    "or": 3, "o": 3,
    "and": 4, "y": 4,
    "not": 5,
    "in": 6, "en": 6, "not in": 6, "no en": 6,
    "is": 6, "es": 6, "is not": 6, "no es": 6,
    "==": 7, "!=": 7, "<": 7, "<=": 7, ">": 7, ">=": 7,
    "|": 8,
    "^": 9,
    "&": 10,
    "<<": 11, ">>": 11,
    "+": 12, "-": 12,
    "*": 13, "/": 13, "//": 13, "%": 13, "@": 13,
    "unary": 14,  # +, -, ~, not
    "**": 15,
    "await": 16,
    "call": 17,  # (), [], ., :: (postfix)
}

# Keywords que son operadores en expresiones
COMPARE_OPS = {"==", "!=", "<", "<=", ">", ">=", "es", "no es", "en", "no en"}

SOFT_KEYWORDS = {"segun", "caso", "tipo"}
STATEMENT_KEYWORDS = {
    "si", "sino_si", "sino", "para", "mientras", "funcion", "clase",
    "devolver", "producir", "intentar", "excepto", "finalmente", "lanzar",
    "con", "importar", "desde", "global", "no_local", "afirmar", "borrar",
    "romper", "continuar", "pasar", "asincrono", "esperar", "tipo",
}


class ParseError(Exception):
    def __init__(self, msg: str, token: Token):
        self.msg = msg
        self.token = token
        super().__init__(f"{msg} en {token.line}:{token.col}")


class Parser:
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0
        self._source = ""

    def parse(self) -> Module:
        module = Module()
        module.line = 1
        module.col = 1
        while not self._at_end():
            stmt = self._parse_statement()
            if stmt:
                module.body.append(stmt)
        module.end_line = self.line
        module.end_col = self.col
        return module

    # ---- Utilidades ----

    def _peek(self) -> Token:
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return Token(TokenType.ENDMARKER, "", self.line, self.col, len(self._source), len(self._source))

    def _peek_n(self, n: int) -> Token:
        if self.pos + n < len(self.tokens):
            return self.tokens[self.pos + n]
        return Token(TokenType.ENDMARKER, "", self.line, self.col, len(self._source), len(self._source))

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

    def _skip_nl(self):
        """Salta separadores de línea, preservando INDENT para los bloques."""
        while self._check(TokenType.NEWLINE, TokenType.NL, TokenType.DEDENT):
            self._advance()

    def _is_statement_start(self) -> bool:
        """Verifica si el token actual inicia un statement."""
        self._skip_nl()
        if self._at_end():
            return False
        tok = self._peek()
        if tok.type != TokenType.NAME:
            return True  # Podría ser (, [, {, etc.
        val = tok.value
        return val in STATEMENT_KEYWORDS or val in SOFT_KEYWORDS

    # ---- Statements ----

    def _parse_statement(self) -> Optional[CSTNode]:
        self._skip_nl()
        if self._at_end():
            return None

        # Decoradores
        decorators = []
        while self._match(TokenType.AT):
            decorators.append(self._parse_decorator())
            self._skip_nl()

        tok = self._peek()

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
            elif val == "asincrono" and self._peek_n(1).type == TokenType.NAME and self._peek_n(1).value == "funcion":
                self._advance()
                return self._parse_func_def(decorators, is_async=True)
            elif val == "clase":
                return self._parse_class_def(decorators)
            elif val == "devolver":
                return self._parse_return()
            elif val == "producir":
                if self._peek_n(1).type == TokenType.NAME and self._peek_n(1).value == "desde":
                    return self._parse_yield_from()
                return self._parse_yield()
            elif val == "intentar":
                return self._parse_try()
            elif val == "lanzar":
                return self._parse_raise()
            elif val == "con":
                return self._parse_with()
            elif val == "asincrono" and self._peek_n(1).type == TokenType.NAME and self._peek_n(1).value == "con":
                self._advance()
                return self._parse_with(is_async=True)
            elif val == "asincrono" and self._peek_n(1).type == TokenType.NAME and self._peek_n(1).value == "para":
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

        # Expression statement / assignment / annotated assign
        return self._parse_expr_stmt()

    def _parse_decorator(self) -> Decorator:
        tok = self._peek()
        expr = self._parse_expression(0)
        return Decorator(func=expr, decorators=[]).set_pos(tok)

    def _parse_if(self) -> IfStmt:
        tok = self._advance()
        test = self._parse_expression(0)
        self._consume(TokenType.COLON)
        body = self._parse_block()
        orelse = []
        while self._check(TokenType.NAME) and self._peek().value in ("sino_si", "sino"):
            if self._peek().value == "sino_si":
                self._advance()
                elif_test = self._parse_expression(0)
                self._consume(TokenType.COLON)
                elif_body = self._parse_block()
                orelse.append(IfStmt(test=elif_test, body=elif_body, orelse=[]).set_pos(tok))
            else:
                self._advance()
                self._consume(TokenType.COLON)
                else_body = self._parse_block()
                if orelse:
                    orelse[-1].orelse = else_body
                else:
                    orelse = else_body
                break
        return IfStmt(test=test, body=body, orelse=orelse).set_pos(tok)

    def _parse_while(self) -> WhileStmt:
        tok = self._advance()
        test = self._parse_expression(0)
        self._consume(TokenType.COLON)
        body = self._parse_block()
        orelse = []
        if self._check(TokenType.NAME) and self._peek().value == "sino":
            self._advance()
            self._consume(TokenType.COLON)
            orelse = self._parse_block()
        return WhileStmt(test=test, body=body, orelse=orelse).set_pos(tok)

    def _parse_for(self, is_async: bool = False) -> ForStmt:
        tok = self._advance()
        target = self._parse_expression(7)
        if not self._match(TokenType.IN):
            raise ParseError("Se esperaba 'en' en bucle para", self._peek())
        iter_ = self._parse_expression(0)
        self._consume(TokenType.COLON)
        body = self._parse_block()
        orelse = []
        if self._check(TokenType.NAME) and self._peek().value == "sino":
            self._advance()
            self._consume(TokenType.COLON)
            orelse = self._parse_block()
        return ForStmt(target=target, iter=iter_, body=body, orelse=orelse, is_async=is_async).set_pos(tok)

    def _parse_func_def(self, decorators: List, is_async: bool = False) -> FuncDef:
        tok = self._advance()
        name = self._consume(TokenType.NAME, "nombre de función").value
        self._consume(TokenType.LPAREN)
        args = self._parse_arguments()
        self._consume(TokenType.RPAREN)
        returns = None
        if self._match(TokenType.ARROW):
            returns = self._parse_expression(0)
        self._consume(TokenType.COLON)
        body = self._parse_block()
        return FuncDef(name=name, args=args, body=body, returns=returns,
                       decorators=decorators, is_async=is_async).set_pos(tok)

    def _parse_class_def(self, decorators: List) -> ClassDef:
        tok = self._advance()
        name = self._consume(TokenType.NAME, "nombre de clase").value
        bases = []
        if self._match(TokenType.LPAREN):
            while not self._check(TokenType.RPAREN):
                bases.append(self._parse_expression(0))
                if not self._match(TokenType.COMMA):
                    break
            self._consume(TokenType.RPAREN)
        self._consume(TokenType.COLON)
        body = self._parse_block()
        return ClassDef(name=name, bases=bases, body=body, decorators=decorators).set_pos(tok)

    def _parse_return(self) -> ReturnStmt:
        tok = self._advance()
        value = None
        if not self._check(TokenType.NEWLINE, TokenType.ENDMARKER, TokenType.DEDENT):
            value = self._parse_expression(0)
        return ReturnStmt(value=value).set_pos(tok)

    def _parse_yield(self) -> YieldStmt:
        tok = self._advance()
        value = None
        if not self._check(TokenType.NEWLINE, TokenType.ENDMARKER, TokenType.DEDENT):
            value = self._parse_expression(0)
        return YieldStmt(value=value).set_pos(tok)

    def _parse_yield_from(self) -> YieldFromStmt:
        tok = self._advance()
        self._advance()  # 'desde'
        value = self._parse_expression(0)
        return YieldFromStmt(value=value).set_pos(tok)

    def _parse_raise(self) -> RaiseStmt:
        tok = self._advance()
        exc = None
        cause = None
        if not self._check(TokenType.NEWLINE, TokenType.ENDMARKER, TokenType.DEDENT):
            exc = self._parse_expression(0)
            if self._check(TokenType.NAME) and self._peek().value == "desde":
                self._advance()
                cause = self._parse_expression(0)
        return RaiseStmt(exc=exc, cause=cause).set_pos(tok)

    def _parse_try(self) -> TryStmt:
        tok = self._advance()
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
        return TryStmt(body=body, handlers=handlers, orelse=orelse, finalbody=finalbody).set_pos(tok)

    def _parse_except_handler(self) -> ExceptHandler:
        tok = self._advance()
        is_star = False
        if self._match(TokenType.STAR):
            is_star = True
        type_ = None
        name = None
        if not self._check(TokenType.COLON):
            type_ = self._parse_expression(0)
            if self._check(TokenType.NAME) and self._peek().value == "como":
                self._advance()
                name = self._consume(TokenType.NAME).value
        self._consume(TokenType.COLON)
        body = self._parse_block()
        return ExceptHandler(type_=type_, name=name, body=body, is_star=is_star).set_pos(tok)

    def _parse_with(self, is_async: bool = False) -> WithStmt:
        tok = self._advance()
        items = []
        while True:
            ctx = self._parse_expression(0)
            vars_ = None
            if self._check(TokenType.NAME) and self._peek().value == "como":
                self._advance()
                vars_ = self._parse_expression(0)
            items.append(WithItem(context_expr=ctx, optional_vars=vars_).set_pos(tok))
            if not self._match(TokenType.COMMA):
                break
        self._consume(TokenType.COLON)
        body = self._parse_block()
        return WithStmt(items=items, body=body, is_async=is_async).set_pos(tok)

    def _parse_match(self) -> MatchStmt:
        tok = self._advance()
        subject = self._parse_expression(0)
        self._consume(TokenType.COLON)
        while self._check(TokenType.NEWLINE, TokenType.NL):
            self._advance()
        self._match(TokenType.INDENT)
        cases = []
        while True:
            self._skip_nl()
            if self._check(TokenType.DEDENT) or self._at_end():
                break
            if not self._check(TokenType.NAME) or self._peek().value != "caso":
                break
            cases.append(self._parse_case())
        return MatchStmt(subject=subject, cases=cases).set_pos(tok)

    def _parse_case(self) -> CaseBlock:
        tok = self._advance()
        pattern = self._parse_pattern()
        guard = None
        if self._check(TokenType.NAME) and self._peek().value == "si":
            self._advance()
            guard = self._parse_expression(0)
        self._consume(TokenType.COLON)
        body = self._parse_block()
        return CaseBlock(pattern=pattern, guard=guard, body=body).set_pos(tok)

    def _parse_pattern(self) -> CSTNode:
        # Simplificado por ahora
        if self._check(TokenType.NAME):
            val = self._peek().value
            if val == "_":
                self._advance()
                return MatchSingleton(value=None).set_pos(self._peek())
            elif val in ("Verdadero", "Falso", "Nada"):
                self._advance()
                v = {"Verdadero": True, "Falso": False, "Nada": None}[val]
                return MatchSingleton(value=v).set_pos(self._peek())
        return self._parse_expression(0)

    def _parse_import(self) -> ImportStmt:
        tok = self._advance()
        names = []
        while True:
            name = self._consume(TokenType.NAME).value
            while self._match(TokenType.DOT) and self._check(TokenType.NAME):
                name += "." + self._consume(TokenType.NAME).value
            asname = None
            if self._check(TokenType.NAME) and self._peek().value == "como":
                self._advance()
                asname = self._consume(TokenType.NAME).value
            names.append(Alias(name=name, asname=asname).set_pos(tok))
            if not self._match(TokenType.COMMA):
                break
        return ImportStmt(names=names).set_pos(tok)

    def _parse_import_from(self) -> ImportFromStmt:
        tok = self._advance()
        level = 0
        while self._match(TokenType.DOT):
            level += 1
        module = None
        if self._check(TokenType.NAME) and self._peek().value != "importar":
            module = self._consume(TokenType.NAME).value
            while self._match(TokenType.DOT) and self._check(TokenType.NAME):
                module += "." + self._consume(TokenType.NAME).value
        self._consume(TokenType.NAME)  # 'importar'
        if self._match(TokenType.STAR):
            # `desde pkg importar *` — import star (entry-level binding).
            return ImportFromStmt(
                module=module, names=[Alias(name="*")], level=level, is_star=True
            ).set_pos(tok)
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
        tok = self._advance()
        test = self._parse_expression(0)
        msg = None
        if self._match(TokenType.COMMA):
            msg = self._parse_expression(0)
        return AssertStmt(test=test, msg=msg).set_pos(tok)

    def _parse_del(self) -> DelStmt:
        tok = self._advance()
        targets = []
        while True:
            targets.append(self._parse_expression(0))
            if not self._match(TokenType.COMMA):
                break
        return DelStmt(targets=targets).set_pos(tok)

    def _parse_type_alias(self) -> AnnAssign:
        tok = self._advance()
        target = self._parse_expression(0)
        self._consume(TokenType.EQUAL)
        value = self._parse_expression(0)
        return AnnAssign(target=target, annotation=value, value=None, simple=1).set_pos(tok)

    def _parse_expr_stmt(self) -> CSTNode:
        """Parsea: assignment, annotated assign, augmented assign, o expresión simple."""
        start_tok = self._peek()
        expr = self._parse_expression(0)

        # Verificar augmented assign
        if self._check(TokenType.PLUS_EQUAL, TokenType.MINUS_EQUAL,
                       TokenType.STAR_EQUAL, TokenType.SLASH_EQUAL,
                       TokenType.PERCENT_EQUAL, TokenType.AT_EQUAL,
                       TokenType.AMPER_EQUAL, TokenType.VBAR_EQUAL,
                       TokenType.CIRCUMFLEX_EQUAL,
                       TokenType.LEFT_SHIFT_EQUAL, TokenType.RIGHT_SHIFT_EQUAL,
                       TokenType.DOUBLE_STAR_EQUAL, TokenType.DOUBLE_SLASH_EQUAL):
            op_tok = self._advance()
            value = self._parse_expression(0)
            self._mark_store(expr)
            return AugAssign(target=expr, op=op_tok.value, value=value).set_pos(start_tok)

        # Verificar annotated assign (target: annotation = value)
        if isinstance(expr, Name) and self._match(TokenType.COLON):
            annotation = self._parse_expression(0)
            value = None
            if self._match(TokenType.EQUAL):
                value = self._parse_expression(0)
            self._mark_store(expr)
            return AnnAssign(target=expr, annotation=annotation, value=value, simple=1).set_pos(start_tok)

        # Verificar asignación simple (target = value)
        if self._match(TokenType.EQUAL):
            # Podría ser asignación múltiple: a, b = 1, 2
            targets = [expr]
            while self._match(TokenType.COMMA):
                targets.append(self._parse_expression(0))
            value = self._parse_expression(0)
            for target in targets:
                self._mark_store(target)
            return Assign(targets=targets, value=value).set_pos(start_tok)

        return ExprStmt(value=expr).set_pos(start_tok)

    def _mark_store(self, node: CSTNode) -> None:
        """Marca el contexto de los targets sin alterar expresiones cargadas."""
        if isinstance(node, Name):
            node.ctx = "Store"
        elif isinstance(node, (Tuple, List)):
            for element in node.elts:
                self._mark_store(element)
        elif isinstance(node, Attribute):
            node.ctx = "Store"
        elif isinstance(node, Subscript):
            node.ctx = "Store"

    def _parse_block(self) -> List[CSTNode]:
        """Parsea un bloque indentado."""
        stmts = []
        while self._check(TokenType.NEWLINE, TokenType.NL):
            self._advance()
        if not self._match(TokenType.INDENT):
            # Single statement en misma línea
            stmt = self._parse_statement()
            if stmt:
                stmts.append(stmt)
            return stmts

        while not self._at_end():
            while self._check(TokenType.NEWLINE, TokenType.NL):
                self._advance()
            if self._check(TokenType.DEDENT):
                break
            stmt = self._parse_statement()
            if stmt:
                stmts.append(stmt)
        self._match(TokenType.DEDENT)
        return stmts

    def _parse_arguments(self) -> Arguments:
        args = Arguments()
        keyword_only = False
        while not self._check(TokenType.RPAREN):
            if self._check(TokenType.DOUBLE_STAR):
                self._advance()
                args.kwarg = Arg(arg=self._consume(TokenType.NAME).value).set_pos(self._peek())
            elif self._check(TokenType.STAR):
                self._advance()
                keyword_only = True
                if not self._check(TokenType.COMMA):
                    args.vararg = Arg(arg=self._consume(TokenType.NAME).value).set_pos(self._peek())
            elif self._check(TokenType.SLASH):
                if not args.args or args.posonlyargs:
                    raise ParseError("'/' requires preceding positional parameters", self._peek())
                self._advance()
                args.posonlyargs = args.args
                args.args = []
            else:
                name = self._consume(TokenType.NAME).value
                annotation = None
                if self._match(TokenType.COLON):
                    annotation = self._parse_expression(0)
                default = None
                if self._match(TokenType.EQUAL):
                    default = self._parse_expression(0)
                arg = Arg(arg=name, annotation=annotation).set_pos(self._peek())
                if keyword_only:
                    args.kwonlyargs.append(arg)
                    args.kw_defaults.append(default)
                else:
                    args.args.append(arg)
                    if default:
                        args.defaults.append(default)
            if not self._match(TokenType.COMMA):
                break
        return args

    # ---- Expresiones (precedencia) ----

    def _parse_expression(self, min_prec: int = 0) -> CSTNode:
        # Parse lhs
        lhs = self._parse_primary()

        # Parse operators with precedence
        while True:
            tok = self._peek()
            if tok.type != TokenType.NAME and tok.type not in (
                TokenType.PLUS, TokenType.MINUS, TokenType.STAR, TokenType.SLASH,
                TokenType.DOUBLE_STAR, TokenType.DOUBLE_SLASH, TokenType.PERCENT,
                TokenType.AT, TokenType.AMPER, TokenType.VBAR, TokenType.CIRCUMFLEX,
                TokenType.LEFT_SHIFT, TokenType.RIGHT_SHIFT,
                TokenType.EQ, TokenType.NOT_EQ, TokenType.LT, TokenType.LT_EQ,
                TokenType.GT, TokenType.GT_EQ, TokenType.IN, TokenType.NOT_IN,
                TokenType.IS, TokenType.IS_NOT,
            ):
                break

            op = tok.value
            prec = PRECEDENCE.get(op, 0)
            if prec == 0 or prec < min_prec:
                break

            # Special handling for right-associative **
            next_min_prec = prec + 1 if op == "**" else prec

            if tok.type == TokenType.NAME and op in ("y", "o"):
                self._advance()
                rhs = self._parse_expression(next_min_prec)
                lhs = BoolOp(op=op, values=[lhs, rhs]).set_pos(tok)
            elif tok.type == TokenType.NAME and op == "no":
                if self._peek_n(1).type == TokenType.NAME and self._peek_n(1).value in ("es", "en"):
                    self._advance()
                    op2 = self._advance().value
                    rhs = self._parse_expression(next_min_prec)
                    lhs = Compare(left=lhs, ops=[f"no {op2}"], comparators=[rhs]).set_pos(tok)
                else:
                    # unary not - handled in primary
                    break
            elif tok.type in (TokenType.IN, TokenType.NOT_IN, TokenType.IS, TokenType.IS_NOT):
                self._advance()
                rhs = self._parse_expression(next_min_prec)
                lhs = Compare(left=lhs, ops=[op], comparators=[rhs]).set_pos(tok)
            elif tok.type in (TokenType.EQ, TokenType.NOT_EQ, TokenType.LT, TokenType.LT_EQ,
                             TokenType.GT, TokenType.GT_EQ):
                self._advance()
                rhs = self._parse_expression(next_min_prec)
                lhs = Compare(left=lhs, ops=[op], comparators=[rhs]).set_pos(tok)
            else:
                # Binary operators
                self._advance()
                rhs = self._parse_expression(next_min_prec)
                lhs = BinOp(left=lhs, op=op, right=rhs).set_pos(tok)

        return lhs

    def _parse_primary(self) -> CSTNode:
        tok = self._peek()

        # Literales
        if tok.type == TokenType.NUMBER:
            self._advance()
            val = tok.value
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
            val = tok.value
            if val.startswith(("f'", 'f"', "F'", 'F"')):
                return self._parse_fstring(val, tok)
            import ast
            val = ast.literal_eval(val)
            return Constant(value=val).set_pos(tok)

        # Identificadores / keywords
        if tok.type == TokenType.NAME:
            val = tok.value
            self._advance()

            if val == "esperar":
                return Await(value=self._parse_unary()).set_pos(tok)
            if val == "no":
                return UnaryOp(op="not", operand=self._parse_unary()).set_pos(tok)

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
                return self._parse_postfix(Tuple(elts=[], ctx="Load").set_pos(tok))
            expr = self._parse_expression(0)
            if self._match(TokenType.COMMA):
                elts = [expr]
                while not self._check(TokenType.RPAREN):
                    elts.append(self._parse_expression(0))
                    if not self._match(TokenType.COMMA):
                        break
                self._consume(TokenType.RPAREN)
                return self._parse_postfix(Tuple(elts=elts, ctx="Load").set_pos(tok))
            self._consume(TokenType.RPAREN)
            return self._parse_postfix(expr)

        # Listas / listcomp
        if self._match(TokenType.LBRACKET):
            if self._check(TokenType.RBRACKET):
                self._advance()
                return self._parse_postfix(List(elts=[], ctx="Load").set_pos(tok))
            elts = [self._parse_expression(0)]
            if self._check(TokenType.NAME) and self._peek().value == "para":
                comp = self._parse_comprehension()
                self._consume(TokenType.RBRACKET)
                return ListComp(elt=elts[0], generators=[comp]).set_pos(tok)
            while not self._check(TokenType.RBRACKET):
                if not self._match(TokenType.COMMA):
                    break
                if self._check(TokenType.RBRACKET):
                    break
                elts.append(self._parse_expression(0))
            self._consume(TokenType.RBRACKET)
            return self._parse_postfix(List(elts=elts, ctx="Load").set_pos(tok))

        # Sets / Dicts / Dictcomp
        if self._match(TokenType.LBRACE):
            if self._check(TokenType.RBRACE):
                self._advance()
                return self._parse_postfix(Dict(keys=[], values=[]).set_pos(tok))
            first = self._parse_expression(0)
            if self._match(TokenType.COLON):
                # Dict
                keys = [first]
                values = [self._parse_expression(0)]
                while self._match(TokenType.COMMA):
                    if self._check(TokenType.RBRACE):
                        break
                    k = self._parse_expression(0)
                    self._consume(TokenType.COLON)
                    keys.append(k)
                    values.append(self._parse_expression(0))
                self._consume(TokenType.RBRACE)
                return self._parse_postfix(Dict(keys=keys, values=values).set_pos(tok))
            else:
                # Set
                elts = [first]
                while self._match(TokenType.COMMA):
                    if self._check(TokenType.RBRACE):
                        break
                    elts.append(self._parse_expression(0))
                self._consume(TokenType.RBRACE)
                return self._parse_postfix(Set(elts=elts).set_pos(tok))

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
            body = self._parse_expression(0)
            return Lambda(args=args, body=body).set_pos(tok)

        # Await
        if self._check(TokenType.NAME) and self._peek().value == "esperar":
            self._advance()
            value = self._parse_unary()
            return Await(value=value).set_pos(tok)

        # Unary
        if self._match(TokenType.PLUS, TokenType.MINUS, TokenType.TILDE):
            op = tok.value
            operand = self._parse_unary()
            return UnaryOp(op=op, operand=operand).set_pos(tok)

        if self._check(TokenType.NAME) and self._peek().value == "no":
            tok = self._advance()
            operand = self._parse_unary()
            return UnaryOp(op="not", operand=operand).set_pos(tok)

        raise ParseError(f"Expresión inesperada: {tok}", tok)

    def _parse_unary(self) -> CSTNode:
        tok = self._peek()
        if tok.type in (TokenType.PLUS, TokenType.MINUS, TokenType.TILDE):
            self._advance()
            op = tok.value
            operand = self._parse_unary()
            return UnaryOp(op=op, operand=operand).set_pos(tok)
        if self._check(TokenType.NAME) and tok.value == "no":
            self._advance()
            operand = self._parse_unary()
            return UnaryOp(op="not", operand=operand).set_pos(tok)
        if self._check(TokenType.NAME) and tok.value == "esperar":
            self._advance()
            value = self._parse_unary()
            return Await(value=value).set_pos(tok)
        return self._parse_primary()

    def _parse_postfix(self, node: CSTNode) -> CSTNode:
        while True:
            if self._match(TokenType.DOT):
                attr = self._consume(TokenType.NAME).value
                node = Attribute(value=node, attr=attr, ctx="Load").set_pos(self._peek())
            elif self._match(TokenType.LBRACKET):
                slice_ = self._parse_expression(0)
                self._consume(TokenType.RBRACKET)
                node = Subscript(value=node, slice=slice_, ctx="Load").set_pos(self._peek())
            elif self._check(TokenType.LPAREN):
                node = self._parse_call(node)
            else:
                break
        return node

    def _parse_call(self, func: CSTNode) -> Call:
        self._consume(TokenType.LPAREN)  # consume '('
        args = []
        starred_args = []
        keywords = []
        if not self._check(TokenType.RPAREN):
            while True:
                if self._check(TokenType.NAME) and self._peek_n(1).type == TokenType.EQUAL:
                    arg_name = self._advance().value
                    self._advance()  # =
                    value = self._parse_expression(0)
                    keywords.append(Keyword(arg=arg_name, value=value).set_pos(self._peek()))
                elif self._check(TokenType.STAR):
                    self._advance()
                    if self._check(TokenType.STAR):
                        self._advance()
                        value = self._parse_expression(0)
                        keywords.append(Keyword(arg=None, value=value).set_pos(self._peek()))
                    else:
                        value = self._parse_expression(0)
                        args.append(value)
                        starred_args.append(value)
                elif self._check(TokenType.DOUBLE_STAR):
                    self._advance()
                    value = self._parse_expression(0)
                    keywords.append(Keyword(arg=None, value=value).set_pos(self._peek()))
                else:
                    args.append(self._parse_expression(0))
                if not self._match(TokenType.COMMA):
                    break
        self._consume(TokenType.RPAREN)
        return Call(func=func, args=args, starred_args=starred_args, keywords=keywords).set_pos(self._peek())

    def _parse_comprehension(self) -> CompFor:
        tok = self._advance()  # 'para'
        is_async = False
        target = self._parse_expression(7)
        if not self._match(TokenType.IN):
            raise ParseError("Se esperaba 'en'", self._peek())
        iter_ = self._parse_expression(0)
        ifs = []
        while self._check(TokenType.NAME) and self._peek().value == "si":
            self._advance()
            ifs.append(self._parse_expression(0))
        return CompFor(target=target, iter=iter_, ifs=ifs, is_async=is_async).set_pos(tok)

    def _parse_fstring(self, value: str, tok: Token) -> FString:
        body = value[2:-1]
        parts: List[CSTNode] = []
        cursor = 0
        literal_start = 0
        while cursor < len(body):
            if body.startswith("{{", cursor) or body.startswith("}}", cursor):
                cursor += 2
                continue
            if body[cursor] != "{":
                cursor += 1
                continue
            if cursor > literal_start:
                parts.append(FStringPart(value=body[literal_start:cursor]))
            depth = 1
            end = cursor + 1
            while end < len(body) and depth:
                if body[end] == "{":
                    depth += 1
                elif body[end] == "}":
                    depth -= 1
                end += 1
            if depth:
                raise ParseError("llave sin cerrar en f-string", tok)
            expression = body[cursor + 1:end - 1]
            format_spec = None
            conversion = -1
            if "!" in expression:
                expression, conversion_text = expression.rsplit("!", 1)
                conversion = ord(conversion_text[0]) if conversion_text else -1
            if ":" in expression:
                expression, format_text = expression.split(":", 1)
                format_spec = Constant(value=format_text)
            nested = Parser(tokenize(expression, "<fstring>"))
            nested._source = expression
            expr = nested._parse_expression(0)
            parts.append(FStringExpr(value=expr, format_spec=format_spec,
                                     conversion=conversion))
            cursor = end
            literal_start = end
        if literal_start < len(body):
            parts.append(FStringPart(value=body[literal_start:]))
        return FString(parts=parts).set_pos(tok)


def parse(source: str) -> Module:
    """Función de conveniencia."""
    tokens = tokenize(source, "<source>")
    parser = Parser(tokens)
    parser._source = source
    return parser.parse()
