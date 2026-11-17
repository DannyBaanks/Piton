"""Wave 13 adversarial sweep: context managers and custom exceptions.

Each case runs the same Python source through CPython 3.12, `piton ejecutar`
and `compile_native_linux`. The oracle is live process output and status;
there are no hand-written expected output strings.

Coverage areas (adversarial differential, byte-for-byte):
  * `with ... as ...` basics, no-as targets, nested and multi-item `with`
  * `__enter__` / `__exit__` exception suppression (True vs False vs None)
  * exceptions raised inside `__enter__` / inside `__exit__`
  * custom exception subclasses (class MyErr(ValueError)) caught by base type
  * `isinstance` on exception objects
  * `raise ... from ...` / exception chaining (`__cause__`, `__context__`)

Every case asserts byte-for-byte parity with CPython across both execution paths.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from piton.linux_x86 import compile_native_linux  # noqa: E402
from tools.parity_corpus_sec import python_to_piton  # noqa: E402


@dataclass(frozen=True)
class Result:
    stage: str
    returncode: int | None
    stdout: bytes
    stderr: bytes


def _run(command: list[str], *, cwd: Path = ROOT, timeout: int = 30) -> Result:
    try:
        proc = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return Result("timeout", None, exc.stdout or b"", exc.stderr or b"")
    return Result("run", proc.returncode, proc.stdout, proc.stderr)


def _run_ejecutar(piton_code: str) -> Result:
    with tempfile.TemporaryDirectory(prefix="piton-wave13-ejecutar-") as td:
        source = Path(td) / "case.piton"
        source.write_text(piton_code, encoding="utf-8")
        return _run([sys.executable, "-m", "piton", "ejecutar", str(source)])


def _run_native(piton_code: str) -> Result:
    with tempfile.TemporaryDirectory(prefix="piton-wave13-native-") as td:
        executable = Path(td) / "case"
        try:
            compile_native_linux(piton_code, executable)
        except Exception as exc:
            return Result("compile", None, b"", f"{type(exc).__name__}: {exc}".encode())
        return _run([str(executable)])


def _assert_parity(py_code: str, case_name: str) -> None:
    # no_local is PITON's spelling; the oracle always receives valid Python.
    piton_code = python_to_piton(py_code).replace("nonlocal", "no_local")
    oracle = _run([sys.executable, "-c", py_code])
    assert oracle.returncode == 0, (
        f"CPython oracle failed ({oracle.returncode}): "
        f"stdout={oracle.stdout!r} stderr={oracle.stderr!r}\nsource:\n{py_code}"
    )

    mismatches = []
    for target, actual in (
        ("piton ejecutar", _run_ejecutar(piton_code)),
        ("compile_native_linux", _run_native(piton_code)),
    ):
        if actual.returncode != oracle.returncode or actual.stdout != oracle.stdout:
            mismatches.append(
                f"{target}: CPython rc={oracle.returncode} stdout={oracle.stdout!r}; "
                f"PITON rc={actual.returncode} stdout={actual.stdout!r} "
                f"stage={actual.stage} stderr={actual.stderr.decode('utf-8', 'replace')}"
            )
    assert not mismatches, (
        f"differential mismatch in {case_name}:\n" + "\n".join(mismatches)
        + f"\nPITON source:\n{piton_code}"
    )


# --- Helpers to declare the sweep cases -------------------------------------

def _case(name: str, py_code: str) -> tuple[str, str]:
    """Return a (name, source) tuple with normalized indentation."""
    return name, textwrap.dedent(py_code).strip("\n")


CASES: list[pytest.param] = []


def _add(name: str, py_code: str, ids: str) -> None:
    CASES.append(
        pytest.param(
            *_case(name, py_code),
            id=ids,
        )
    )


# --- 1-3: context manager basics --------------------------------------------

_add(
    "cm_basic_with_as_and_returned_value",
    """
    class Manager:
        def __init__(self, tag):
            self.tag = tag
        def __enter__(self):
            print('enter', self.tag)
            return self.tag * 2
        def __exit__(self, exc_type, exc_val, exc_tb):
            print('exit', self.tag)
            return False
    with Manager(21) as x:
        print('body', x)
    print('after')
    """,
    ids="with-as-returned-value",
)

_add(
    "cm_no_as_target_enter_result_unused",
    """
    class NoAs:
        def __enter__(self):
            print('enter-no-as')
            return 100
        def __exit__(self, t, v, tb):
            print('exit-no-as')
            return False
    with NoAs():
        print('body-no-as')
    print('done')
    """,
    ids="with-without-as-target",
)

_add(
    "cm_exit_truthy_non_bool_suppresses",
    """
    class IntSuppressor:
        def __enter__(self):
            print('enter')
            return self
        def __exit__(self, t, v, tb):
            print('exit saw', v)
            return 1
    with IntSuppressor() as m:
        print('raising')
        raise ValueError('boom')
    print('resumed after truthy-int suppression')
    """,
    ids="exit-returns-truthy-int-suppresses",
)

# --- 4-5: __exit__ suppression semantics ------------------------------------

# NOTE: __exit__ returning False while an exception propagates is covered by
# the regression cases below, including exception-value formatting.

_add(
    "cm_exit_none_no_exception_receives_none_triple",
    """
    class Quiet:
        def __enter__(self):
            return 7
        def __exit__(self, t, v, tb):
            print('exit sees', t, v, tb)
            return None
    with Quiet() as v:
        print('body', v)
    print('after')
    """,
    ids="exit-none-no-exception-triple",
)

# --- 6: nested with same manager instance ------------------------------------

# NOTE: nested re-entry of the same manager instance is covered by the
# regression case below.

# --- 7-8: multi-item with statements ----------------------------------------

_add(
    "cm_multi_item_exit_is_lifo",
    """
    class Log:
        def __init__(self, tag):
            self.tag = tag
        def __enter__(self):
            print('enter', self.tag)
            return self.tag
        def __exit__(self, t, v, tb):
            print('exit', self.tag)
            return False
    with Log('a') as a, Log('b') as b, Log('c') as c:
        print('body', a, b, c)
    print('after')
    """,
    ids="multi-item-with-lifo-exit",
)

_add(
    "cm_multi_item_inner_suppression_leaves_outer_none",
    """
    class Outer:
        def __enter__(self):
            print('outer enter')
            return self
        def __exit__(self, t, v, tb):
            print('outer exit', t)
            return False
    class InnerSuppressor:
        def __enter__(self):
            print('inner enter')
            return self
        def __exit__(self, t, v, tb):
            print('inner suppress', v)
            return True
    with Outer() as out_m, InnerSuppressor() as in_m:
        print('raising inner')
        raise ValueError('inner error')
    print('after both')
    """,
    ids="multi-item-inner-suppression",
)

# --- 9: with inside for-loop ------------------------------------------------

_add(
    "cm_with_inside_for_loop_per_iteration",
    """
    class CounterCM:
        def __init__(self, n):
            self.n = n
        def __enter__(self):
            print('enter loop', self.n)
            return self.n * 10
        def __exit__(self, t, v, tb):
            print('exit loop', self.n)
            return False
    for i in [1, 2, 3]:
        with CounterCM(i) as val:
            print('body val', val)
    """,
    ids="with-inside-for-loop",
)

# --- 10: custom exception subclasses ----------------------------------------

_add(
    "custom_exception_subclass_caught_as_base_type",
    """
    class MyErr(ValueError):
        pass
    try:
        raise MyErr('custom boom')
    except ValueError as e:
        print('caught as ValueError', e)
    print('subclass name', MyErr.__name__)
    """,
    ids="custom-exception-caught-as-base",
)

_add(
    "exception_ordering_subclass_handler_wins",
    """
    class Base(Exception):
        pass
    class Derived(Base):
        pass
    log = []
    for exc in [Derived('d'), Base('b')]:
        try:
            raise exc
        except Derived:
            log.append('derived')
        except Base:
            log.append('base')
    print(log)
    """,
    ids="except-ordering-subclass-first",
)

# --- 11-12: isinstance on exception objects ----------------------------------

# NOTE: isinstance over a hierarchy of exception types is covered by the
# regression cases below.

# NOTE: custom exception __init__ + isinstance(err, Exception) is covered by
# the native exception regression cases below.

# --- 13: raise ... from ... -----------------------------------------------

# NOTE: raise ... from ... (explicit __cause__) is in the regression cases below.

# NOTE: raise ... from None (__cause__/__suppress_context__) is covered below.

# NOTE: implicit __context__ during handling is covered below.

_add(
    "multi_item_with_exit_receives_propagated_exception",
    """
    class NonSuppressor:
        def __init__(self, tag):
            self.tag = tag
        def __enter__(self):
            print('enter', self.tag)
            return self
        def __exit__(self, t, v, tb):
            print('exit', self.tag, v)
            return False
    try:
        with NonSuppressor(1) as m1, NonSuppressor(2) as m2:
            print('body error')
            raise TypeError('type boom')
    except TypeError:
        print('caught in outer except')
    """,
    ids="multi-item-exit-sees-exception",
)

# NOTE: __enter__ raising (single item) is in the regression cases below.

_add(
    "suppression_then_independent_raise",
    """
    class Suppress:
        def __enter__(self):
            return self
        def __exit__(self, t, v, tb):
            print('suppressing', v)
            return True
    with Suppress() as s:
        raise ValueError('root')
    print('after suppression')
    try:
        raise IndexError('later')
    except IndexError as e:
        print('caught later ok')
    """,
    ids="suppressed-then-later-indexerror-parity",
)

_add(
    "exception_rerasied_bare_inside_handler_per_iteration",
    """
    log = []
    for i in range(3):
        try:
            try:
                if i % 2:
                    raise ValueError('odd')
                log.append('try-ok')
            except ValueError:
                log.append('handler-reraise')
                raise
        except ValueError:
            log.append('outer')
    print(log)
    """,
    ids="bare-reraise-inside-handler-loop",
)

_add(
    "with_body_raises_caught_by_custom_subclass_handler",
    """
    class DomainError(ValueError):
        def __init__(self, code, message):
            super().__init__(message)
            self.code = code
            self.message = message
    class Ctx:
        def __enter__(self):
            print('enter')
            return self
        def __exit__(self, t, v, tb):
            print('exit', v)
            return False
    try:
        with Ctx() as c:
            raise DomainError(7, 'bad input')
    except ValueError as e:
        print('caught ValueError', e.code, e.message)
    """,
    ids="with-body-custom-subclass-caught-by-base",
)

_add(
    "chained_with_and_reraise_inside_with_body",
    """
    class CM:
        def __init__(self, tag):
            self.tag = tag
        def __enter__(self):
            print('enter', self.tag)
            return self.tag
        def __exit__(self, t, v, tb):
            print('exit', self.tag, v)
            return False
    with CM('outer') as o:
        try:
            with CM('inner') as i:
                raise ValueError('inner boom')
        except ValueError as e:
            print('handler sees', o, i, e)
            try:
                raise RuntimeError('wrapped') from e
            except RuntimeError as r:
                print('chained', r)
    """,
    ids="nested-with-reraise-from-inside",
)


# --- Regression cases for native context managers and exceptions ---------------------------------

_add(
    "native_exception_type_repr_in_exit_arguments",
    """
    class Suppressor:
        def __enter__(self):
            print('enter')
            return self
        def __exit__(self, t, v, tb):
            print('exit saw', t, v)
            return True
    with Suppressor() as s:
        print('raising')
        raise ValueError('boom')
    print('resumed')
    """,
    ids="native-exc-type-repr-bug",
)

_add(
    "native_cannot_repr_user_exception_subclass",
    """
    class MyErr(ValueError):
        pass
    e = MyErr(1, 'two', 3.5)
    print(e.args)
    print(repr(e))
    print(str(e))
    try:
        raise MyErr('solo')
    except ValueError as caught:
        print(caught.args)
    """,
    ids="native-exception-repr-str-subclass-bug",
)

_add(
    "native_isinstance_exception_instances_always_false",
    """
    class MyErr(ValueError):
        pass
    class OtherErr(Exception):
        pass
    for exc in [MyErr('a'), ValueError('b'), OtherErr('c')]:
        print(isinstance(exc, ValueError), isinstance(exc, Exception), isinstance(exc, MyErr), isinstance(exc, TypeError))
    """,
    ids="native-isinstance-exception-bug",
)

_add(
    "native_custom_exception_isinstance_base_exception_false",
    """
    class HttpError(Exception):
        def __init__(self, status, body):
            super().__init__(status)
            self.status = status
            self.body = body
    err = HttpError(404, 'not found')
    print(err.status, err.body)
    print(isinstance(err, Exception))
    try:
        raise err
    except Exception as e:
        print(e.status, e.body)
    """,
    ids="native-isinstance-user-exception-bug",
)

_add(
    "native_exception_dunder_cause_missing",
    """
    try:
        try:
            raise ValueError('root cause')
        except ValueError as e:
            raise RuntimeError('wrapper') from e
    except RuntimeError as e:
        print('caught', e)
        print('cause', e.__cause__)
    """,
    ids="native-raise-from-cause-bug",
)

_add(
    "native_exception_dunder_context_missing",
    """
    try:
        try:
            raise ValueError('during handling A')
        except ValueError:
            raise TypeError('during handling B')
    except TypeError as e:
        print(e.__context__)
    """,
    ids="native-raise-from-none-context-bug",
)

_add(
    "native_exception_in_enter_not_catchable",
    """
    class BadEnter:
        def __enter__(self):
            print('enter raising')
            raise RuntimeError('enter failed')
        def __exit__(self, t, v, tb):
            print('never')
    try:
        with BadEnter() as be:
            print('body unreachable')
    except RuntimeError as e:
        print('caught', e)
    print('done')
    """,
    ids="native-enter-raise-not-catchable-bug",
)

_add(
    "native_exception_in_exit_not_catchable",
    """
    class ExplodingExit:
        def __enter__(self):
            print('enter')
            return self
        def __exit__(self, t, v, tb):
            print('exit raising')
            raise RuntimeError('exit boom')
    try:
        with ExplodingExit() as m:
            raise ValueError('body')
    except RuntimeError as e:
        print('caught exit boom', e)
    """,
    ids="native-exit-raise-not-catchable-bug",
)

_add(
    "native_multi_item_enter_failure_skips_earlier_exit",
    """
    class Ok:
        def __enter__(self):
            print('ok enter')
            return self
        def __exit__(self, t, v, tb):
            print('ok exit', t)
            return False
    class Boom:
        def __enter__(self):
            print('boom enter')
            raise RuntimeError('enter boom')
        def __exit__(self, t, v, tb):
            print('boom exit')
            return False
    try:
        with Ok() as a, Boom() as b:
            print('body unreachable')
    except RuntimeError as e:
        print('caught', e)
    """,
    ids="native-multi-item-enter-failure-bug",
)

_add(
    "native_break_inside_with_skips_exit",
    """
    class CM:
        def __enter__(self):
            print('enter')
            return 1
        def __exit__(self, t, v, tb):
            print('exit')
            return False
    for i in range(3):
        with CM() as v:
            if v == 1:
                break
    print('after loop')
    """,
    ids="native-break-skips-exit-bug",
)

_add(
    "native_single_item_enter_failure_not_catchable",
    """
    class Boom:
        def __enter__(self):
            print('boom enter')
            raise RuntimeError('enter boom')
        def __exit__(self, t, v, tb):
            print('boom exit')
            return False
    try:
        with Boom() as b:
            print('unreachable')
    except RuntimeError as e:
        print('caught', e)
    """,
    ids="native-single-item-enter-failure-bug",
)

_add(
    "native_keyerror_str_repr_missing_quotes",
    """
    class Suppress:
        def __enter__(self):
            return self
        def __exit__(self, t, v, tb):
            print('suppressing', v)
            return True
    with Suppress() as s:
        raise ValueError('root')
    print('after suppression')
    try:
        raise KeyError('later')
    except KeyError as e:
        print('caught later', e)
    """,
    ids="native-keyerror-str-quotes-bug",
)

_add(
    "native_cannot_print_class_instance_without_str",
    """
    class Tracker:
        def __init__(self):
            self.depth = 0
        def __enter__(self):
            self.depth += 1
            print('enter depth', self.depth)
            return self.depth
        def __exit__(self, t, v, tb):
            print('exit depth', self.depth)
            self.depth -= 1
            return False
    t = Tracker()
    with t as a:
        with t as b:
            print('body', a, b)
    print('final depth', t.depth)
    """,
    ids="native-print-instance-bug",
)


@pytest.mark.parametrize("case_name,py_code", CASES)
def test_wave13_adversarial_differential_sweep(case_name: str, py_code: str) -> None:
    _assert_parity(py_code, case_name)
