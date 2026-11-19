"""NAMEDTUPLE_BASE_V1: ``Base = namedtuple('Base', campos)`` como clase tupla.

``collections.namedtuple`` devuelve una *closure* ctor; una clase que hereda
de ella (``clase Punto(Base): ...``) no tiene clase base estatica y MIR falla
cerrado (``super()`` no puede resolver la base evaluada dinamicamente).

Este pase CST->CST reescribe, SOLO a nivel de modulo, la asignacion
``Base = namedtuple('Base', campos)`` por una definicion ``clase Base`` cuyos
valores viven en una tupla (``self._nt_v``) y que expone los campos,
indexacion, ``len``, iteracion, ``in``, igualdad con tuplas, ``_asdict``,
``_fields`` y ``repr`` igual que CPython 3.12.

Alcance deliberadamente estrecho (el resto conserva el camino de siempre):

* el nombre de la asignacion es exactamente el ``typename`` (``clase`` y
  ``repr`` coinciden);
* ``campos`` es un str o una lista/tupla de str literales, identificadores
  ASCII validos, sin palabras reservadas, sin ``_`` inicial, sin duplicados;
* no hay ``defaults=``, ``rename=`` ni ``module=``;
* el nombre se liga UNA sola vez en todo el modulo;
* alguna clase lo usa como base (``clase X(Base)``).

Si algo de lo anterior no se cumple la asignacion queda intacta.
"""
from __future__ import annotations

import dataclasses
import keyword
import re
from typing import Any, Iterator

from piton import cst as _cst
from piton.lexer import HARD_KEYWORDS

_IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")
# `y`, `o`, `no`, `es`, `en` figuran en HARD_KEYWORDS como operadores, pero el
# parser los acepta como nombres de campo (namedtuple('P', 'x y') es el caso
# canonico): no se reservan. Las palabras clave de bloque si.
_OPERATOR_WORDS = {"y", "o", "no", "es", "en"}
_RESERVED_FIELDS = (
    set(keyword.kwlist)
    | (HARD_KEYWORDS - _OPERATOR_WORDS)
    | {"self", "fijar_atr"}
)


def _children(node: Any) -> Iterator[Any]:
    if not dataclasses.is_dataclass(node):
        return
    for f in dataclasses.fields(node):
        value = getattr(node, f.name)
        if isinstance(value, list):
            for item in value:
                if dataclasses.is_dataclass(item):
                    yield item
        elif dataclasses.is_dataclass(value):
            yield value


def _walk(root: Any) -> Iterator[Any]:
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(_children(node))


def _binding_count(root: Any, name: str) -> int:
    """Cuenta todo lo que puede ligar ``name`` (conservador: de sobra)."""
    total = 0
    for node in _walk(root):
        if isinstance(node, _cst.Name):
            if node.ctx != "Load" and node.id == name:
                total += 1
            continue
        if isinstance(node, _cst.Constant):
            continue
        for f in dataclasses.fields(node):
            value = getattr(node, f.name)
            if f.name in {"name", "asname", "arg"} and value == name:
                total += 1
            elif f.name in {"names"} and isinstance(value, list) and name in value:
                total += 1
    return total


def _is_str_const(node: Any) -> bool:
    return isinstance(node, _cst.Constant) and isinstance(node.value, str)


def _namedtuple_callees(module: _cst.Module) -> tuple[set[str], set[str]]:
    """Nombres que apuntan a ``collections.namedtuple`` / al modulo collections."""
    fn_names: set[str] = set()
    mod_names: set[str] = set()
    for stmt in module.body:
        if isinstance(stmt, _cst.ImportFromStmt) and stmt.module == "collections" and stmt.level == 0:
            for alias in stmt.names:
                if alias.name == "namedtuple":
                    fn_names.add(alias.asname or "namedtuple")
        elif isinstance(stmt, _cst.ImportStmt):
            for alias in stmt.names:
                if alias.name == "collections" and alias.asname is None:
                    mod_names.add("collections")
                elif alias.name == "collections" and alias.asname:
                    mod_names.add(alias.asname)
    accepted_fn = {n for n in fn_names if _binding_count(module, n) == 1}
    accepted_mod = {n for n in mod_names if _binding_count(module, n) == 1}
    return accepted_fn, accepted_mod


def _field_names(node: Any) -> list[str] | None:
    """Campos literales, o None si la forma no es la soportada."""
    if _is_str_const(node):
        names = node.value.replace(",", " ").split()
    elif isinstance(node, (_cst.List, _cst.Tuple)) and all(_is_str_const(e) for e in node.elts):
        names = [e.value for e in node.elts]
    else:
        return None
    for name in names:
        if not _IDENT.match(name) or name in _RESERVED_FIELDS or name.startswith("_"):
            return None
    if len(set(names)) != len(names):
        return None
    return names


def _getattr_lines(cls_name: str, fields: list[str]) -> list[str]:
    lines = ["    funcion __getattr__(self, nombre):"]
    for i, f in enumerate(fields):
        lines.append(f'        si nombre == "{f}":')
        lines.append(f"            devolver self._nt_v[{i}]")
    lines.append(
        f"        lanzar AttributeError(\"'{cls_name}' object has no attribute '\" + nombre + \"'\")"
    )
    return lines


def _repr_lines(cls_name: str, fields: list[str]) -> list[str]:
    parts = [f'"{cls_name}("']
    for i, f in enumerate(fields):
        sep = ", " if i else ""
        parts.append(f'"{sep}{f}=" + repr(self._nt_v[{i}])')
    parts.append('")"')
    return ["    funcion __repr__(self):", "        devolver " + " + ".join(parts)]


def _member_defs(typename: str, fields: list[str]) -> dict[str, list[str]]:
    """Cuerpo de cada metodo generado, por nombre (sangria de clase)."""
    n = len(fields)
    members: dict[str, list[str]] = {}
    members["__getattr__"] = _getattr_lines(typename, fields)
    members["__repr__"] = _repr_lines(typename, fields)
    if n:
        # Los campos son propiedades de solo lectura en CPython; el resto de
        # atributos (subclase con __dict__) se guarda normalmente.
        members["__setattr__"] = [
            "    funcion __setattr__(self, nombre, valor):",
            "        si " + " o ".join(f'nombre == "{f}"' for f in fields) + ":",
            "            lanzar AttributeError(\"can't set attribute\")",
            "        fijar_atr(self, nombre, valor)",
        ]
    members["__getitem__"] = [
        "    funcion __getitem__(self, indice):",
        "        devolver self._nt_v[indice]",
    ]
    members["__len__"] = ["    funcion __len__(self):", "        devolver len(self._nt_v)"]
    members["__iter__"] = ["    funcion __iter__(self):", "        devolver iter(self._nt_v)"]
    members["__contains__"] = [
        "    funcion __contains__(self, valor):",
        "        devolver valor en self._nt_v",
    ]
    # Igualdad como tupla: otra instancia namedtuple, o una tupla/lista de la
    # misma longitud y elementos. Elemento a elemento: la comparacion de
    # tuplas enteras sobre un atributo sin tipo estatico no es fiable.
    # Limitacion conocida: una lista con los mismos elementos tambien iguala.
    members["__eq__"] = [
        "    funcion __eq__(self, otro):",
        "        si hasattr(otro, \"_nt_v\"):",
        "            ov = otro._nt_v",
        "        sino:",
        "            ov = otro",
        "        n = len(self._nt_v)",
        "        si len(ov) != n:",
        "            devolver Falso",
        "        i = 0",
        "        mientras i < n:",
        "            si self._nt_v[i] != ov[i]:",
        "                devolver Falso",
        "            i = i + 1",
        "        devolver Verdadero",
    ]
    members["__ne__"] = ["    funcion __ne__(self, otro):", "        devolver no (self == otro)"]
    members["__hash__"] = ["    funcion __hash__(self):", "        devolver hash(self._nt_v)"]
    members["_asdict"] = (
        ["    funcion _asdict(self):", "        d = {}"]
        + [f'        d["{f}"] = self._nt_v[{i}]' for i, f in enumerate(fields)]
        + ["        devolver d"]
    )
    # tuple.count / tuple.index. Un campo homonimo gana (propiedad en CPython).
    if "count" not in fields:
        members["count"] = [
            "    funcion count(self, valor):",
            "        n = 0",
            "        para x en self._nt_v:",
            "            si x == valor:",
            "                n = n + 1",
            "        devolver n",
        ]
    if "index" not in fields:
        members["index"] = [
            "    funcion index(self, valor):",
            "        i = 0",
            "        para x en self._nt_v:",
            "            si x == valor:",
            "                devolver i",
            "            i = i + 1",
            "        lanzar ValueError(\"tuple.index(x): x not in tuple\")",
        ]
    return members


def _generated_source(typename: str, fields: list[str]) -> str:
    n = len(fields)
    tuple_lit = "(" + ", ".join(f'"{f}"' for f in fields) + ("," if n == 1 else "") + ")"
    params = ", ".join(fields)
    values_lit = "(" + ", ".join(fields) + ("," if n == 1 else "") + ")"
    lines = [
        f"clase {typename}:",
        f"    _fields = {tuple_lit}",
        f"    funcion __init__(self{', ' if n else ''}{params}):",
        f'        fijar_atr(self, "_nt_v", {values_lit})',
    ]
    for body in _member_defs(typename, fields).values():
        lines.extend(body)
    return "\n".join(lines) + "\n"


def _subclass_member_source(cls_name: str, fields: list[str]) -> str:
    """Subclases: __getattr__/__repr__ con su propio nombre (CPython usa
    type(self).__name__; el backend fija el tipo estatico de self a la base)."""
    lines = [f"clase {cls_name}:"]
    members = _member_defs(cls_name, fields)
    for key in ("__getattr__", "__repr__"):
        lines.extend(members[key])
    return "\n".join(lines) + "\n"


def _match_namedtuple_assign(
    stmt: Any, fn_names: set[str], mod_names: set[str],
) -> tuple[str, list[str]] | None:
    if not (
        isinstance(stmt, _cst.Assign)
        and len(stmt.targets) == 1
        and isinstance(stmt.targets[0], _cst.Name)
        and isinstance(stmt.value, _cst.Call)
    ):
        return None
    call = stmt.value
    func = call.func
    is_nt = (
        (isinstance(func, _cst.Name) and func.id in fn_names)
        or (
            isinstance(func, _cst.Attribute)
            and func.attr == "namedtuple"
            and isinstance(func.value, _cst.Name)
            and func.value.id in mod_names
        )
    )
    if not is_nt or call.keywords or call.starred_args or len(call.args) != 2:
        return None
    typename_node, fields_node = call.args
    typename = stmt.targets[0].id
    if not _is_str_const(typename_node) or typename_node.value != typename:
        return None
    if not _IDENT.match(typename) or typename in _RESERVED_FIELDS or keyword.iskeyword(typename):
        return None
    fields = _field_names(fields_node)
    if fields is None:
        return None
    return typename, fields


def _used_as_base(module: _cst.Module, name: str) -> bool:
    for node in _walk(module):
        if isinstance(node, _cst.ClassDef) and any(
            isinstance(b, _cst.Name) and b.id == name for b in node.bases
        ):
            return True
    return False


def _derived_class_nodes(module: _cst.Module, name: str) -> list[Any]:
    """ClassDef de todas las subclases (directas o transitivas) de ``name``."""
    classes = [n for n in _walk(module) if isinstance(n, _cst.ClassDef)]
    derived = {name}
    changed = True
    while changed:
        changed = False
        for cls in classes:
            if cls.name in derived:
                continue
            if any(isinstance(b, _cst.Name) and b.id in derived for b in cls.bases):
                derived.add(cls.name)
                changed = True
    return [cls for cls in classes if cls.name in derived and cls.name != name]


def _subclass_overrides_construction(module: _cst.Module, name: str) -> bool:
    """True si alguna subclase (directa o transitiva) define __init__/__new__.

    CPython construye un namedtuple en __new__ y un __init__ de usuario que
    llama a super().__init__(args) falla con TypeError (object.__init__). No
    lo modelamos: esos modulos conservan el camino de siempre (fail closed).
    """
    for cls in _derived_class_nodes(module, name):
        for stmt in cls.body:
            if isinstance(stmt, _cst.FuncDef) and stmt.name in {"__init__", "__new__"}:
                return True
    return False


def desugar_namedtuple_bases(module: _cst.Module) -> _cst.Module:
    """Reescribe in situ las asignaciones namedtuple que sirven de base."""
    if not isinstance(module, _cst.Module):
        return module
    fn_names, mod_names = _namedtuple_callees(module)
    if not fn_names and not mod_names:
        return module
    # Import local to avoid a cycle: piton.parser -> piton.lower -> here.
    from piton.parser import parse

    for index, stmt in enumerate(module.body):
        matched = _match_namedtuple_assign(stmt, fn_names, mod_names)
        if matched is None:
            continue
        typename, fields = matched
        if _binding_count(module, typename) != 1:
            continue
        if not _used_as_base(module, typename):
            continue
        if _subclass_overrides_construction(module, typename):
            continue
        generated = parse(_generated_source(typename, fields))
        if len(generated.body) != 1 or not isinstance(generated.body[0], _cst.ClassDef):
            raise AssertionError("NAMEDTUPLE_BASE_V1: generated source is not one class")
        class_node = generated.body[0]
        class_node.line = stmt.line
        class_node.col = stmt.col
        module.body[index] = class_node
        for cls in _derived_class_nodes(module, typename):
            own = {s.name for s in cls.body if isinstance(s, _cst.FuncDef)}
            missing = {"__getattr__", "__repr__"} - own
            if not missing:
                continue
            members = parse(_subclass_member_source(cls.name, fields)).body[0].body
            cls.body.extend(
                s for s in members
                if isinstance(s, _cst.FuncDef) and s.name in missing
            )
    return module
