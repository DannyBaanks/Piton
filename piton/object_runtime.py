"""Modelo de objetos dinámicos de Pitón para el runtime futuro.

Es una capa de bootstrap verificable en Python; no se presenta como runtime
nativo hasta que el backend x86 la consuma directamente.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator


class RuntimeObjectError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RuntimeType:
    name: str


class RuntimeObject:
    __slots__ = ("type", "refcount", "alive")

    def __init__(self, type_: RuntimeType):
        self.type = type_
        self.refcount = 1
        self.alive = True

    def incref(self) -> "RuntimeObject":
        if not self.alive:
            raise RuntimeObjectError("incref on deallocated object")
        self.refcount += 1
        return self

    def decref(self) -> None:
        if not self.alive:
            raise RuntimeObjectError("double decref")
        self.refcount -= 1
        if self.refcount < 0:
            raise RuntimeObjectError("negative reference count")
        if self.refcount == 0:
            self.alive = False


class RuntimeValue(RuntimeObject):
    __slots__ = ("value",)

    def __init__(self, type_name: str, value: Any):
        super().__init__(RuntimeType(type_name))
        self.value = value


def rt_none() -> RuntimeValue:
    return RuntimeValue("NoneType", None)


def rt_bool(value: Any) -> RuntimeValue:
    return RuntimeValue("bool", bool(value))


def rt_int(value: Any) -> RuntimeValue:
    return RuntimeValue("int", int(value))


def rt_float(value: Any) -> RuntimeValue:
    return RuntimeValue("float", float(value))


def rt_str(value: Any) -> RuntimeValue:
    return RuntimeValue("str", str(value))


def rt_bytes(value: Any) -> RuntimeValue:
    return RuntimeValue("bytes", bytes(value))


class RuntimeSequence(RuntimeObject):
    __slots__ = ("items",)

    def __init__(self, type_name: str, items: list[RuntimeObject] | tuple[RuntimeObject, ...]):
        super().__init__(RuntimeType(type_name))
        self.items = list(items)


class RuntimeList(RuntimeSequence):
    def __init__(self, items: list[RuntimeObject] | None = None):
        super().__init__("list", items or [])


class RuntimeTuple(RuntimeSequence):
    def __init__(self, items: tuple[RuntimeObject, ...] = ()):
        super().__init__("tuple", items)
        self.items = tuple(items)


class RuntimeDict(RuntimeObject):
    __slots__ = ("items",)

    def __init__(self, items: dict[Any, RuntimeObject] | None = None):
        super().__init__(RuntimeType("dict"))
        self.items = dict(items or {})


class RuntimeSet(RuntimeObject):
    __slots__ = ("items",)

    def __init__(self, items: set[Any] | None = None):
        super().__init__(RuntimeType("set"))
        self.items = set(items or set())


def runtime_truthy(obj: RuntimeObject) -> bool:
    if isinstance(obj, RuntimeValue):
        if obj.type.name == "NoneType":
            return False
        if obj.type.name in {"bool", "int", "float"}:
            return bool(obj.value)
        if obj.type.name in {"str", "bytes"}:
            return bool(obj.value)
    if isinstance(obj, (RuntimeList, RuntimeTuple, RuntimeDict, RuntimeSet)):
        return bool(obj.items)
    return True


def runtime_equal(left: RuntimeObject, right: RuntimeObject) -> bool:
    if left.type != right.type:
        return False
    if isinstance(left, RuntimeValue) and isinstance(right, RuntimeValue):
        return left.value == right.value
    if isinstance(left, RuntimeSequence) and isinstance(right, RuntimeSequence):
        return len(left.items) == len(right.items) and all(runtime_equal(a, b) for a, b in zip(left.items, right.items))
    if isinstance(left, RuntimeDict) and isinstance(right, RuntimeDict):
        return left.items == right.items
    if isinstance(left, RuntimeSet) and isinstance(right, RuntimeSet):
        return left.items == right.items
    return left is right


def runtime_hash(obj: RuntimeObject) -> int:
    if isinstance(obj, RuntimeValue):
        return hash(obj.value)
    if isinstance(obj, RuntimeTuple):
        return hash(tuple(runtime_hash(item) for item in obj.items))
    if isinstance(obj, (RuntimeList, RuntimeDict, RuntimeSet)):
        raise TypeError(f"unhashable type: '{obj.type.name}'")
    return id(obj)


def runtime_iter(obj: RuntimeObject) -> Iterator[RuntimeObject]:
    if isinstance(obj, RuntimeSequence):
        return iter(obj.items)
    if isinstance(obj, RuntimeDict):
        return (rt_str(key) if isinstance(key, str) else rt_int(key) for key in obj.items)
    if isinstance(obj, RuntimeSet):
        return (rt_str(value) if isinstance(value, str) else rt_int(value) for value in obj.items)
    raise TypeError(f"'{obj.type.name}' object is not iterable")


def runtime_slice(obj: RuntimeObject, start: int | None, stop: int | None, step: int | None = None) -> RuntimeObject:
    if not isinstance(obj, RuntimeSequence):
        raise TypeError(f"'{obj.type.name}' object is not subscriptable")
    sliced = obj.items[slice(start, stop, step)]
    return RuntimeTuple(tuple(sliced)) if obj.type.name == "tuple" else RuntimeList(list(sliced))
