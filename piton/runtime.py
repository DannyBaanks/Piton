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


def compile_piton(fuente: str, archivo: str = "<piton>") -> Any:
    """API explícita para compilar fuente Pitón a un code object Python."""
    return compilar(fuente, archivo)


def exec_piton(fuente: str, globals_: dict[str, Any] | None = None,
               locals_: dict[str, Any] | None = None, archivo: str = "<piton>") -> dict[str, Any]:
    """Ejecuta Pitón solo cuando el caller lo solicita explícitamente."""
    espacio = globals_ if globals_ is not None else {}
    destino = locals_ if locals_ is not None else espacio
    exec(compile_piton(fuente, archivo), espacio, destino)
    return destino


def eval_piton(fuente: str, globals_: dict[str, Any] | None = None,
               locals_: dict[str, Any] | None = None, archivo: str = "<piton>") -> Any:
    """Evalúa una expresión Pitón mediante una llamada explícita."""
    espacio = globals_ if globals_ is not None else {}
    destino = locals_ if locals_ is not None else espacio
    traducido = traducir_fuente(fuente, archivo)
    return eval(compile(traducido, archivo, "eval"), espacio, destino)


def inspect_piton_source(fuente: str, archivo: str = "<piton>") -> dict[str, Any]:
    """Devuelve fuente original, Python generado y líneas inspeccionables."""
    from .translator import traducir_fuente_con_mapa
    generated, mapping = traducir_fuente_con_mapa(fuente, archivo)
    return {
        "filename": archivo,
        "source": fuente,
        "generated": generated,
        "source_map": mapping,
    }
