"""BUNDLED_STDLIB_V1: modulos de la stdlib empaquetados en PITON.

El corpus v4 compila cada archivo AISLADO (los dos tiers), asi que un
programa que importa un modulo de la stdlib solo compila si ese modulo esta
en el binario. El backend no puede reimplementar la stdlib en C (es
infinito), pero si puede llevar una stdlib escrita en PITON: estos fuentes
se compilan e inlinean cuando el programa importa el modulo.

`fnmatch` es el primer modulo: 60 lineas de PITON que cubren `fnmatch` y
`fnmatchcase` con la misma semantica que CPython (patrones `*`, `?` y
clases `[...]` con `!` y rangos).

Cada modulo es un fuente PITON en este directorio; el nombre del archivo es
el nombre del modulo (`fnmatch.piton` -> `importar fnmatch`).
"""

from __future__ import annotations

from pathlib import Path

_STDLIB_DIR = Path(__file__).with_name("stdlib_piton")


def bundled_modules() -> dict[str, Path]:
    """Nombre del modulo -> ruta de su fuente PITON."""
    if not _STDLIB_DIR.is_dir():
        return {}
    out: dict[str, Path] = {}
    for path in sorted(_STDLIB_DIR.glob("*.piton")):
        out[path.stem] = path
    return out


def bundled_module_source(name: str) -> str | None:
    """El fuente PITON de un modulo empaquetado, o None si no existe."""
    path = bundled_modules().get(name)
    if path is None:
        return None
    return path.read_text(encoding="utf-8")


def bundled_module_path(name: str) -> str | None:
    """La ruta del fuente de un modulo empaquetado (para sys.modules)."""
    path = bundled_modules().get(name)
    return str(path) if path is not None else None
