"""Float timestamp parity for the Linux native time and date paths."""
from __future__ import annotations

from piton.native_differential import compare_native_to_cpython


def _assert_matches(source: str, monkeypatch) -> None:
    # The freestanding backend currently uses UTC for localtime; match the
    # oracle's timezone so these fixed timestamps compare byte for byte.
    monkeypatch.setenv("TZ", "UTC")
    result = compare_native_to_cpython(source)
    assert result.native.returncode == result.oracle.returncode, (
        f"returncode mismatch\nnative stderr: {result.native.stderr!r}\n"
        f"oracle stderr: {result.oracle.stderr!r}"
    )
    assert result.native.stdout == result.oracle.stdout, (
        f"stdout mismatch\nnative stderr: {result.native.stderr!r}\n"
        f"oracle stderr: {result.oracle.stderr!r}"
    )
    assert result.native.stderr == result.oracle.stderr


def test_float_timestamps_flow_through_time_and_date(monkeypatch):
    _assert_matches(
        "importar time\n"
        "desde datetime importar date\n"
        "local = time.localtime(1.9)\n"
        "utc = time.gmtime(-1.9)\n"
        "imprimir(local[0], local[1], local[2], local[3], local[4], local[5])\n"
        "imprimir(utc[0], utc[1], utc[2], utc[3], utc[4], utc[5])\n"
        "imprimir(time.ctime(-1.9))\n"
        "imprimir(time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(1.9)))\n"
        "imprimir(date.fromtimestamp(-1))\n"
        "imprimir(date.fromtimestamp(-1.9))\n",
        monkeypatch,
    )


def test_invalid_float_timestamps_raise_cpython_exception_types(monkeypatch):
    _assert_matches(
        "importar time\n"
        "intentar:\n"
        "    time.gmtime(float('nan'))\n"
        "excepto ValueError:\n"
        "    imprimir('nan:ValueError')\n"
        "sino:\n"
        "    imprimir('nan:NO_ERROR')\n"
        "intentar:\n"
        "    time.localtime(float('inf'))\n"
        "excepto OverflowError:\n"
        "    imprimir('inf:OverflowError')\n"
        "sino:\n"
        "    imprimir('inf:NO_ERROR')\n"
        "intentar:\n"
        "    time.ctime(-1e100)\n"
        "excepto OverflowError:\n"
        "    imprimir('negative:OverflowError')\n"
        "sino:\n"
        "    imprimir('negative:NO_ERROR')\n"
        "intentar:\n"
        "    time.gmtime(100000000000000000)\n"
        "excepto OSError:\n"
        "    imprimir('calendar-range:OSError')\n"
        "sino:\n"
        "    imprimir('calendar-range:NO_ERROR')\n",
        monkeypatch,
    )


def test_non_timestamp_and_non_struct_time_arguments_fail_closed(monkeypatch):
    _assert_matches(
        "importar time\n"
        "intentar:\n"
        "    time.localtime('1')\n"
        "excepto TypeError:\n"
        "    imprimir('localtime:TypeError')\n"
        "sino:\n"
        "    imprimir('localtime:NO_ERROR')\n"
        "intentar:\n"
        "    time.strftime('%Y', 1.5)\n"
        "excepto TypeError:\n"
        "    imprimir('strftime:TypeError')\n"
        "sino:\n"
        "    imprimir('strftime:NO_ERROR')\n",
        monkeypatch,
    )


def test_datetime_datetime_fromtimestamp_matches_cpython(monkeypatch):
    _assert_matches(
        "desde datetime importar datetime\n"
        "imprimir(datetime.fromtimestamp(1.5))\n",
        monkeypatch,
    )
