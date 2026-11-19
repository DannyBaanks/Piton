"""CPython 3.12 argument-binding TypeError messages.

Pure string builders shared by the MIR lowerer and the Linux backend, so a
direct call, a keyword call, a constructor and a method all report the same
text CPython does. The C twin of these helpers lives in the runtime string of
``piton/linux_x86.py`` (``piton_arity_*``); keep the two in step.

Order of checks, observed on CPython 3.12.3 (the binder follows it):
keyword errors (unexpected / multiple values, in call order) -> too many
positional -> missing positional -> missing keyword-only.
"""

from __future__ import annotations


def join_quoted_names(names: list[str] | tuple[str, ...]) -> str:
    """'a' | 'a' and 'b' | 'a', 'b', and 'c' (CPython's list shape)."""
    quoted = [f"'{name}'" for name in names]
    if len(quoted) <= 1:
        return "".join(quoted)
    if len(quoted) == 2:
        return f"{quoted[0]} and {quoted[1]}"
    return ", ".join(quoted[:-1]) + ", and " + quoted[-1]


def missing_positional(qualname: str, names: list[str] | tuple[str, ...]) -> str:
    count = len(names)
    noun = "argument" if count == 1 else "arguments"
    return (
        f"{qualname}() missing {count} required positional {noun}: "
        f"{join_quoted_names(names)}"
    )


def missing_keyword_only(qualname: str, names: list[str] | tuple[str, ...]) -> str:
    count = len(names)
    noun = "argument" if count == 1 else "arguments"
    return (
        f"{qualname}() missing {count} required keyword-only {noun}: "
        f"{join_quoted_names(names)}"
    )


def unexpected_keyword(qualname: str, name: str) -> str:
    return f"{qualname}() got an unexpected keyword argument '{name}'"


def multiple_values(qualname: str, name: str) -> str:
    return f"{qualname}() got multiple values for argument '{name}'"


def positional_only_as_keyword(qualname: str, names: list[str] | tuple[str, ...]) -> str:
    return (
        f"{qualname}() got some positional-only arguments passed as keyword "
        f"arguments: '{', '.join(names)}'"
    )


def too_many_positional(
    qualname: str,
    positional_n: int,
    required_n: int,
    has_defaults: bool,
    given: int,
    kwonly_given: int = 0,
) -> str:
    """``positional_n``: positional parameters (posonly + regular).
    ``required_n``: those without a default. ``given``: positional args passed
    (``self`` included for methods). ``kwonly_given``: keyword-only params bound
    by keyword in this call."""
    if has_defaults:
        signature = f"from {required_n} to {positional_n}"
        plural = True
    else:
        signature = str(positional_n)
        plural = positional_n != 1
    takes = f"{qualname}() takes {signature} positional argument{'s' if plural else ''}"
    if kwonly_given:
        given_text = (
            f"{given} positional argument{'s' if given != 1 else ''} "
            f"(and {kwonly_given} keyword-only argument{'s' if kwonly_given != 1 else ''})"
        )
        return f"{takes} but {given_text} were given"
    return f"{takes} but {given} {'was' if given == 1 else 'were'} given"
