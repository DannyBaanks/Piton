"""Import hook for .piton files (v0.7)."""
from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import sys
from pathlib import Path
from typing import Any

from .translator import PitonSyntaxError, leer_fuente, traducir_fuente


class PitonFinder(importlib.abc.MetaPathFinder):
    """Finds .piton modules on sys.path."""

    def find_module(
        self,
        fullname: str,
        path: list[str] | None = None,
    ) -> PitonLoader | None:
        partes = fullname.split(".")
        nombre_archivo = partes[-1]
        buscar_en = path or sys.path

        for directorio in buscar_en:
            if not isinstance(directorio, str):
                continue
            ruta = Path(directorio)
            candidatos = [
                ruta / f"{nombre_archivo}.piton",
                ruta / nombre_archivo / "__init__.piton",
            ]
            for candidato in candidatos:
                if candidato.is_file():
                    return PitonLoader(candidato, fullname)
        return None

    def find_spec(
        self,
        fullname: str,
        path: list[str] | None = None,
        target: Any = None,
    ) -> importlib.machinery.ModuleSpec | None:
        loader = self.find_module(fullname, path)
        if loader is None:
            return None
        partes = fullname.split(".")
        if loader.es_paquete:
            ubicacion = [str(loader.ruta.parent)]
        else:
            ubicacion = [str(loader.ruta.parent)]
        return importlib.machinery.ModuleSpec(
            fullname,
            loader,
            origin=str(loader.ruta),
            is_package=loader.es_paquete,
        )


class PitonLoader(importlib.abc.Loader):
    """Loads and executes .piton files."""

    def __init__(self, ruta: Path, fullname: str) -> None:
        self.ruta = ruta
        self.fullname = fullname
        self.es_paquete = ruta.name == "__init__.piton"

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> Any:
        return None

    def exec_module(self, module: Any) -> None:
        fuente = leer_fuente(self.ruta)
        try:
            traducido = traducir_fuente(fuente, str(self.ruta))
        except PitonSyntaxError as error:
            raise ImportError(f"Error de traducción Pitón: {error}") from error

        module.__file__ = str(self.ruta)
        module.__package__ = self.fullname.rsplit(".", 1)[0] if "." in self.fullname else self.fullname
        module.__spec__ = importlib.machinery.ModuleSpec(
            self.fullname,
            self,
            origin=str(self.ruta),
        )
        codigo = compile(traducido, str(self.ruta), "exec")
        exec(codigo, module.__dict__)


def instalar_hook() -> None:
    """Install the .piton import hook on sys.meta_path."""
    if not any(isinstance(f, PitonFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, PitonFinder())


def desinstalar_hook() -> None:
    """Remove the .piton import hook from sys.meta_path."""
    sys.meta_path[:] = [f for f in sys.meta_path if not isinstance(f, PitonFinder)]
