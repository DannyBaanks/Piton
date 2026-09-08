"""REPL for Pitón (v0.8)."""
from __future__ import annotations

import code
import sys
from typing import Any

from .translator import PitonSyntaxError, traducir_fuente


class PitonConsole(code.InteractiveConsole):
    """Interactive console that translates Pitón to Python on the fly."""

    def __init__(self, locals: dict[str, Any] | None = None) -> None:
        super().__init__(locals=locals)
        self._buffer: list[str] = []

    def runsource(self, source: str, filename: str = "<stdin>", symbol: str = "single") -> bool:
        """Translate Pitón source and run it. Returns True if more input is needed."""
        try:
            traducido = traducir_fuente(source, filename, validar=False)
        except PitonSyntaxError as error:
            # Could be incomplete input (need more lines) or a real error
            if self._es_entrada_incompleta(source):
                return True
            self.showsyntaxerror(filename)
            return False

        try:
            code_obj = compile(traducido, filename, symbol)
        except SyntaxError as error:
            if self._es_entrada_incompleta(source):
                return True
            self.showsyntaxerror(filename)
            return False

        try:
            exec(code_obj, self.locals)
        except SystemExit:
            raise
        except Exception:
            self.showtraceback()
        return False

    def _es_entrada_incompleta(self, source: str) -> bool:
        """Heuristic: if the translated code has a syntax error that looks like
        incomplete input (unexpected EOF), treat it as needing more lines."""
        from .translator import traducir_fuente
        try:
            traducido = traducir_fuente(source, "<stdin>", validar=True)
            return False
        except PitonSyntaxError:
            # Check if the raw source ends with ':' or has unbalanced parens/brackets
            stripped = source.rstrip()
            if stripped.endswith(":"):
                return True
            if stripped.endswith("\\"):
                return True
            # Count open parens/brackets
            opens = source.count("(") - source.count(")")
            brackets = source.count("[") - source.count("]")
            if opens > 0 or brackets > 0:
                return True
            return False


def ejecutar_repl() -> None:
    """Launch the Pitón interactive REPL."""
    console = PitonConsole()
    banner = (
        "Pitón REPL — Python hablando español\n"
        "Escribe código en Pitón y presiona Enter.\n"
        "Ctrl+D (Unix) o Ctrl+Z+Enter (Windows) para salir.\n"
    )
    try:
        console.interact(banner=banner, exitmsg="¡Adiós!")
    except SystemExit:
        pass


def compilar_piton(fuente: str, archivo: str = "<piton>") -> Any:
    """Compile Pitón source to a Python code object."""
    traducido = traducir_fuente(fuente, archivo)
    return compile(traducido, archivo, "exec")
