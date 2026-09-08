"""Protocolo de objetos bootstrap: clases, MRO, binding y descriptors."""
from __future__ import annotations

from types import MethodType
from typing import Any


class PitonClass:
    def __init__(self, name: str, bases: tuple["PitonClass", ...] = (), namespace: dict[str, Any] | None = None):
        self.name = name
        self.bases = bases
        self.namespace = dict(namespace or {})
        self.__mro__ = self._c3()

    def _c3(self) -> tuple["PitonClass", ...]:
        sequences = [list(base.__mro__) for base in self.bases] + [list(self.bases)]
        result = [self]
        while any(sequences):
            candidate = next((seq[0] for seq in sequences if seq and all(seq[0] not in other[1:] for other in sequences)), None)
            if candidate is None:
                raise TypeError(f"cannot create consistent MRO for {self.name}")
            result.append(candidate)
            for sequence in sequences:
                if sequence and sequence[0] is candidate:
                    sequence.pop(0)
        return tuple(result)

    def lookup(self, name: str) -> Any:
        for cls in self.__mro__:
            if name in cls.namespace:
                return cls.namespace[name]
        raise AttributeError(name)

    def __call__(self, *args: Any, **kwargs: Any) -> "PitonInstance":
        instance = PitonInstance(self)
        try:
            initializer = self.lookup("__init__")
        except AttributeError:
            initializer = None
        if initializer is not None:
            _bind(initializer, instance)(*args, **kwargs)
        return instance


class PitonInstance:
    def __init__(self, cls: PitonClass):
        object.__setattr__(self, "__dict__", {})
        object.__setattr__(self, "cls", cls)

    def __getattr__(self, name: str) -> Any:
        try:
            class_value = self.cls.lookup(name)
        except AttributeError:
            if "__getattr__" in self.cls.namespace:
                return _bind(self.cls.lookup("__getattr__"), self)(name)
            raise
        if hasattr(class_value, "__get__"):
            return class_value.__get__(self, self.cls)
        if name in self.__dict__:
            return self.__dict__[name]
        return _bind(class_value, self)

    def __setattr__(self, name: str, value: Any) -> None:
        try:
            descriptor = self.cls.lookup(name)
        except AttributeError:
            descriptor = None
        if descriptor is not None and hasattr(descriptor, "__set__"):
            descriptor.__set__(self, value)
        else:
            self.__dict__[name] = value


class PitonSuper:
    def __init__(self, owner: PitonClass, instance: PitonInstance):
        self.owner = owner
        self.instance = instance

    def __getattr__(self, name: str) -> Any:
        mro = self.instance.cls.__mro__
        index = mro.index(self.owner)
        for cls in mro[index + 1:]:
            if name in cls.namespace:
                return _bind(cls.namespace[name], self.instance)
        raise AttributeError(name)


def _bind(value: Any, instance: PitonInstance) -> Any:
    if isinstance(value, (staticmethod, classmethod, property)):
        return value.__get__(instance, instance.cls)
    if callable(value):
        return MethodType(value, instance)
    return value


class Property:
    def __init__(self, getter=None, setter=None):
        self.getter = getter
        self.setter = setter

    def __get__(self, instance, owner):
        if instance is None:
            return self
        if self.getter is None:
            raise AttributeError("unreadable property")
        return self.getter(instance)

    def __set__(self, instance, value):
        if self.setter is None:
            raise AttributeError("can't set property")
        self.setter(instance, value)


class Descriptor:
    def __init__(self, name: str):
        self.name = name
        self.values: dict[int, Any] = {}

    def __get__(self, instance, owner):
        if instance is None:
            return self
        return self.values.get(id(instance))

    def __set__(self, instance, value):
        self.values[id(instance)] = value
