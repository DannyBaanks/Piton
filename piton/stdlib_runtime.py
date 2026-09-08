"""Superficie stdlib/FFI explícita para el runtime bootstrap."""
from __future__ import annotations

import ctypes
import importlib
import json
import math
import os
import pathlib
import subprocess
import sys
import time
from typing import Any, Sequence


def stdlib_modules() -> dict[str, Any]:
    return {
        "sys": sys,
        "os": os,
        "pathlib": pathlib,
        "math": math,
        "time": time,
        "json": json,
    }


def run_subprocess(argv: Sequence[str], *, cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    if not argv or any(not isinstance(item, str) for item in argv):
        raise TypeError("subprocess argv must be a non-empty sequence of strings")
    return subprocess.run(list(argv), cwd=cwd, capture_output=True, text=True, shell=False, check=False)


class ExplicitFFI:
    def __init__(self, library: str):
        self.library_name = library
        self.library = ctypes.CDLL(library)

    def symbol(self, name: str, restype: Any = ctypes.c_int, argtypes: Sequence[Any] = ()):
        function = getattr(self.library, name)
        function.restype = restype
        function.argtypes = list(argtypes)
        return function


def load_module(name: str) -> Any:
    """Carga un módulo solo por petición explícita, sin traducir nombres."""
    return importlib.import_module(name)
