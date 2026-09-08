"""Pitón: una superficie en español sobre Python."""

from .import_hook import desinstalar_hook, instalar_hook
from .repl import compilar_piton, ejecutar_repl
from .runtime import compilar, ejecutar_archivo
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
    "compilar_piton",
    "desinstalar_hook",
    "ejecutar_archivo",
    "ejecutar_repl",
    "instalar_hook",
    "traducir_archivo",
    "traducir_fuente",
    "traducir_fuente_con_mapa",
]
__version__ = "1.0.0"
