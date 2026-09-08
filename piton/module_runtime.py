"""Loader/cache explícitos para módulos Pitón en el runtime bootstrap."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .translator import leer_fuente, traducir_fuente


@dataclass
class PitonModule:
    name: str
    file: Path
    package: str | None
    namespace: dict[str, Any] = field(default_factory=dict)


class PitonModuleCache:
    def __init__(self):
        self.modules: dict[str, PitonModule] = {}

    def load(self, name: str, path: str | Path) -> PitonModule:
        path = Path(path).resolve()
        if name in self.modules and self.modules[name].file == path:
            return self.modules[name]
        package = name.rsplit(".", 1)[0] if "." in name else None
        module = PitonModule(name, path, package)
        self.modules[name] = module
        generated = traducir_fuente(leer_fuente(path), str(path))
        module.namespace.update({"__name__": name, "__file__": str(path), "__package__": package, "__spec__": None})
        exec(compile(generated, str(path), "exec"), module.namespace, module.namespace)
        return module

    def invalidate(self, name: str) -> None:
        self.modules.pop(name, None)

    def clear(self) -> None:
        self.modules.clear()
