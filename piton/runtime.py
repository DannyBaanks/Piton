from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .import_hook import instalar_hook
from .translator import traducir_archivo, traducir_fuente


def ejecutar_archivo(ruta: str | Path, argumentos: list[str] | None = None, estricto: bool = False) -> None:
    ruta = Path(ruta).resolve()
    traducido = traducir_archivo(ruta, estricto=estricto)
    codigo = compile(traducido, str(ruta), "exec")
    argv_anterior = sys.argv
    sys.argv = [str(ruta), *(argumentos or [])]
    espacio = {
        "__name__": "__main__",
        "__file__": str(ruta),
        "__package__": None,
        "__cached__": None,
    }
    try:
        exec(codigo, espacio, espacio)
    finally:
        sys.argv = argv_anterior


def compilar(fuente: str, archivo: str = "<piton>") -> Any:
    """Public API: compile Pitón source to a Python code object."""
    from .repl import compilar_piton
    return compilar_piton(fuente, archivo)
