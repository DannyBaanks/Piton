"""Frames, binding de llamadas y control no local para el runtime bootstrap."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional


class CallBindingError(TypeError):
    pass


@dataclass(slots=True)
class Cell:
    value: Any = None


@dataclass(slots=True)
class Frame:
    name: str
    globals: dict[str, Any] = field(default_factory=dict)
    locals: dict[str, Any] = field(default_factory=dict)
    closure: dict[str, Cell] = field(default_factory=dict)
    parent: Optional["Frame"] = None

    def get(self, name: str) -> Any:
        if name in self.locals:
            return self.locals[name]
        if name in self.closure:
            return self.closure[name].value
        return self.globals[name]

    def set(self, name: str, value: Any) -> None:
        if name in self.closure:
            self.closure[name].value = value
        else:
            self.locals[name] = value


@dataclass(frozen=True, slots=True)
class Signature:
    positional: tuple[str, ...] = ()
    defaults: dict[str, Any] = field(default_factory=dict)
    keyword_only: tuple[str, ...] = ()
    keyword_defaults: dict[str, Any] = field(default_factory=dict)
    vararg: Optional[str] = None
    kwarg: Optional[str] = None


@dataclass(frozen=True, slots=True)
class PitonArgVector:
    """Ordered positional values passed through the generic call ABI."""

    values: tuple[Any, ...] = ()


@dataclass(frozen=True, slots=True)
class PitonKwVector:
    """Ordered keyword values passed through the generic call ABI."""

    items: tuple[tuple[str, Any], ...] = ()


def bind_call_vectors(
    name: str,
    signature: Signature,
    positional: PitonArgVector,
    keywords: PitonKwVector,
) -> dict[str, Any]:
    """Bind generic call vectors before entering a native or bootstrap frame."""
    kwargs: dict[str, Any] = {}
    for key, value in keywords.items:
        if key in kwargs:
            raise CallBindingError(f"{name}() got multiple values for keyword argument: {key}")
        kwargs[key] = value
    args = positional.values
    if len(args) > len(signature.positional) and signature.vararg is None:
        raise CallBindingError(
            f"{name}() takes {len(signature.positional)} positional arguments but {len(args)} were given"
        )
    values: dict[str, Any] = {}
    for index, parameter in enumerate(signature.positional):
        if index < len(args):
            if parameter in kwargs:
                raise CallBindingError(f"{name}() got multiple values for argument: {parameter}")
            values[parameter] = args[index]
        elif parameter in kwargs:
            values[parameter] = kwargs.pop(parameter)
        elif parameter in signature.defaults:
            values[parameter] = signature.defaults[parameter]
        else:
            raise CallBindingError(f"{name}() missing required argument: {parameter}")
    if signature.vararg:
        values[signature.vararg] = tuple(args[len(signature.positional):])
    for parameter in signature.keyword_only:
        if parameter in kwargs:
            values[parameter] = kwargs.pop(parameter)
        elif parameter in signature.keyword_defaults:
            values[parameter] = signature.keyword_defaults[parameter]
        else:
            raise CallBindingError(f"{name}() missing keyword-only argument: {parameter}")
    if kwargs and signature.kwarg is None:
        unexpected = next(iter(kwargs))
        raise CallBindingError(f"{name}() got an unexpected keyword argument: {unexpected}")
    if signature.kwarg:
        values[signature.kwarg] = dict(kwargs)
    return values


class PitonFunction:
    def __init__(self, name: str, signature: Signature, body: Callable[[Frame], Any], closure: dict[str, Cell] | None = None):
        self.name = name
        self.signature = signature
        self.body = body
        self.closure = dict(closure or {})

    def bind(self, args: tuple[Any, ...], kwargs: dict[str, Any], globals_: dict[str, Any] | None = None) -> Frame:
        values = bind_call_vectors(
            self.name,
            self.signature,
            PitonArgVector(tuple(args)),
            PitonKwVector(tuple(kwargs.items())),
        )
        return Frame(self.name, globals=globals_ or {}, locals=values, closure=dict(self.closure))

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        frame = self.bind(args, dict(kwargs))
        return self.body(frame)


def make_closure(name: str, signature: Signature, body: Callable[[Frame], Any], frame: Frame, captured: tuple[str, ...]) -> PitonFunction:
    return PitonFunction(name, signature, body, {name: frame.closure.get(name, Cell(frame.locals[name])) for name in captured})


class PitonRaised(Exception):
    def __init__(self, value: Any, cause: Optional[BaseException] = None):
        super().__init__(str(value))
        self.value = value
        self.cause = cause


def raise_piton(value: Any, cause: Optional[BaseException] = None) -> None:
    raise PitonRaised(value, cause)


def with_runtime(manager: Any, body: Callable[[Any], Any]) -> Any:
    entered = manager.__enter__()
    try:
        result = body(entered)
    except BaseException as error:
        if manager.__exit__(type(error), error, error.__traceback__):
            return None
        raise
    else:
        manager.__exit__(None, None, None)
        return result


class PitonGenerator:
    def __init__(self, iterator: Iterator[Any]):
        self.iterator = iterator

    def __iter__(self) -> "PitonGenerator":
        return self

    def __next__(self) -> Any:
        return next(self.iterator)

    @classmethod
    def from_iterable(cls, iterable: Any) -> "PitonGenerator":
        return cls(iter(iterable))
