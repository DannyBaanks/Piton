"""Pitón: una superficie en español sobre Python."""

from .import_hook import desinstalar_hook, instalar_hook
from .repl import compilar_piton, ejecutar_repl
from .runtime import (
    compilar, compile_piton, ejecutar_archivo, eval_piton, exec_piton,
    inspect_piton_source,
)
from .translator import (
    MapaFuente,
    PitonStrictError,
    PitonSyntaxError,
    traducir_archivo,
    traducir_fuente,
    traducir_fuente_con_mapa,
)

__all__ = [
    "PitonStrictError",
    "PitonSyntaxError",
    "MapaFuente",
    "compilar",
    "compile_piton",
    "compilar_piton",
    "desinstalar_hook",
    "ejecutar_archivo",
    "eval_piton",
    "exec_piton",
    "ejecutar_repl",
    "instalar_hook",
    "inspect_piton_source",
    "traducir_archivo",
    "traducir_fuente",
    "traducir_fuente_con_mapa",
]
__version__ = "1.0.0"
