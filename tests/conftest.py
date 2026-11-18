"""Cuarentena Windows de la suite (hueco declarado, actualizada 2026-11-18).

El backend nativo Windows ``piton/x86.py`` va atras del MIR y de
``piton/linux_x86.py`` desde la unificacion 2026-10-24 / sync 2026-10-30: hay
tests que pasan en Linux y fallan en Windows. La lista versionada vive en
``tests/quarantine_windows.txt`` (con su origen, sus cuentas y su fecha).

Comportamiento:

* En ``win32`` cada ID de la lista se marca ``xfail(strict=False)``: **el test
  se ejecuta igual** y el log lo muestra como ``XFAIL``. El job queda verde
  solo si no falla NINGUN test fuera de la lista y ninguna entrada rompe
  setup/teardown (un test rojo nuevo rompe CI, que es el punto del gate).
* Si un test de la lista llega a pasar, pytest lo reporta ``XPASS``; el
  resumen ``CUARENTENA WINDOWS:`` lo enumera para sacarlo de la lista.
* Si la lista trae un nodeid que esta corrida no recolecta, aparece como
  ``NO_RECOLECTADOS``: en Windows debe ser 0; en FORCE sobre Linux es esperado
  para los tests windows-only de ``test_phase5.py`` (el modulo se salta con
  ``pytest.skip(..., allow_module_level=True)``). Un nodeid renombrado o
  borrado de igual forma sale ahi y hay que limpiarlo de la lista.

El tracker de ``FUERA_DE_LA_LISTA`` no depende del marker ``xfail``: cuenta
fallos y errores reales de setup, call y teardown, y tambien errores de
coleccion. La cuarentena solo absorbe un fallo de ``call``; un error de
fixture o de teardown sigue siendo un error real y hace fallar la sesion.

Variables de entorno (solo para verificacion local, no las usa CI):

* ``PITON_QUARANTINE_FORCE=1`` aplica la lista aunque no estes en Windows
  (sirve para probar la maquinaria en Linux).
* ``PITON_QUARANTINE_DISABLE=1`` la desactiva por completo.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

QUARANTINE_FILE = Path(__file__).with_name("quarantine_windows.txt")
REASON = (
    "HUECO DECLARADO Windows x86.py (cuarentena 2026-11-18): ver "
    "docs/windows_native_failures.md y tests/quarantine_windows.txt"
)

_applied: set[str] = set()
_quarantined: set[str] = set()
_own_strict_xfail: set[str] = set()
_results: dict[str, str] = {}
_outside_failures: dict[str, set[str]] = {}
_collection_errors: dict[str, str] = {}
_quarantine_errors: dict[str, set[str]] = {}
_strict_xpasses: set[str] = set()
_stats: dict[str, Any] = {"listed": 0, "matched": 0, "stale": []}


def _active() -> bool:
    if os.environ.get("PITON_QUARANTINE_DISABLE") == "1":
        return False
    if os.environ.get("PITON_QUARANTINE_FORCE") == "1":
        return True
    return sys.platform == "win32"


def load_ids() -> list[str]:
    if not QUARANTINE_FILE.exists():
        return []
    ids: list[str] = []
    for line in QUARANTINE_FILE.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        ids.append(entry)
    return ids


def _norm(nodeid: str) -> str:
    """nodeid comparable: pytest usa `/` en Linux y puede dar `\\` en Windows."""
    return nodeid.replace("\\", "/").lower()


def _is_strict_xfail(marker: pytest.Mark | None) -> bool:
    if marker is None:
        return False
    strict = marker.kwargs.get("strict")
    if strict is None and marker.args:
        strict = marker.args[0]
    return strict is True


def pytest_sessionstart(session: pytest.Session) -> None:
    _applied.clear()
    _quarantined.clear()
    _own_strict_xfail.clear()
    _results.clear()
    _outside_failures.clear()
    _collection_errors.clear()
    _quarantine_errors.clear()
    _strict_xpasses.clear()
    _stats.update(listed=0, matched=0, stale=[])


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if not _active():
        return
    for item in items:
        if _is_strict_xfail(item.get_closest_marker("xfail")):
            _own_strict_xfail.add(_norm(item.nodeid))
    listed = load_ids()
    if not listed:
        return
    wanted = {_norm(entry): entry for entry in listed}
    matched: set[str] = set()
    matched_norm: set[str] = set()
    for item in items:
        key = _norm(item.nodeid)
        if key in wanted:
            matched.add(item.nodeid)
            matched_norm.add(key)
            item.add_marker(pytest.mark.xfail(reason=REASON, strict=False))
    _applied.update(matched)
    _quarantined.update(matched_norm)
    _stats.update(
        listed=len(listed),
        matched=len(matched),
        stale=sorted(entry for key, entry in wanted.items() if key not in matched_norm),
    )


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    key = _norm(report.nodeid)
    quarantined = key in _quarantined
    was_xfail = getattr(report, "wasxfail", None)

    if report.outcome == "failed":
        if key in _own_strict_xfail and "XPASS(strict)" in str(report.longrepr):
            _strict_xpasses.add(report.nodeid)
            return
        if quarantined:
            _quarantine_errors.setdefault(report.nodeid, set()).add(report.when)
            if report.when == "call":
                _results[report.nodeid] = "failed"
            else:
                _results.setdefault(report.nodeid, "error")
            return
        _outside_failures.setdefault(report.nodeid, set()).add(report.when)
        return

    if report.outcome == "skipped":
        if was_xfail and report.when != "call":
            # pytest 9 xfail also masks exceptions raised in setup/teardown.
            # The quarantine covers a known call failure, not a broken fixture.
            if quarantined:
                _quarantine_errors.setdefault(report.nodeid, set()).add(report.when)
                _results.setdefault(report.nodeid, "error")
            else:
                _outside_failures.setdefault(report.nodeid, set()).add(report.when)
            return
        if quarantined:
            if report.when == "call":
                if was_xfail:
                    _results[report.nodeid] = "xfailed"
                else:
                    _results[report.nodeid] = "skipped"
            else:
                _results.setdefault(report.nodeid, "skipped")
        return

    if report.outcome == "passed" and quarantined and was_xfail:
        _results[report.nodeid] = "xpassed"


def pytest_collectreport(report: pytest.CollectReport) -> None:
    if report.failed:
        detail = str(report.longrepr).strip().splitlines()
        _collection_errors[report.nodeid] = detail[-1] if detail else "collection error"


def pytest_sessionfinish(session: pytest.Session, exitstatus: int | pytest.ExitCode) -> None:
    if (
        _outside_failures
        or _collection_errors
        or _quarantine_errors
        or _strict_xpasses
    ):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(
    terminalreporter: Any, exitstatus: int, config: pytest.Config
) -> None:
    if not (
        _applied
        or _stats["listed"]
        or _outside_failures
        or _collection_errors
        or _quarantine_errors
        or _strict_xpasses
    ):
        return
    counts = {"xfailed": 0, "xpassed": 0, "skipped": 0, "failed": 0, "error": 0, "sin_reporte": 0}
    for nodeid in _applied:
        counts[_results.get(nodeid, "sin_reporte")] += 1
    xpassed = sorted(n for n, r in _results.items() if r == "xpassed")
    failed = sorted(n for n, r in _results.items() if r == "failed")
    stale = list(_stats["stale"])
    outside_count = len(_outside_failures) + len(_collection_errors)
    terminalreporter.write_sep("=", "CUARENTENA WINDOWS 2026-11-18 (hueco declarado)")
    terminalreporter.write_line(
        f"lista={_stats['listed']} aplicados={_stats['matched']} "
        f"XFAIL={counts['xfailed']} XPASS={counts['xpassed']} "
        f"SKIP={counts['skipped']} ERROR={counts['error']} "
        f"SIN_REPORTE={counts['sin_reporte']} "
        f"FUERA_DE_LA_LISTA={outside_count} "
        f"ERRORES_CUARENTENA={len(_quarantine_errors)} "
        f"COLECCION={len(_collection_errors)} "
        f"XPASS_ESTRICTO={len(_strict_xpasses)}"
    )
    terminalreporter.write_line(
        "docs: docs/windows_native_failures.md | lista: tests/quarantine_windows.txt"
    )
    if xpassed:
        terminalreporter.write_line(
            f"XPASS ({len(xpassed)}) — candidatos a SALIR de la lista:"
        )
        for nodeid in xpassed[:15]:
            terminalreporter.write_line(f"  {nodeid}")
        if len(xpassed) > 15:
            terminalreporter.write_line(f"  ... y {len(xpassed) - 15} mas")
    if failed:
        terminalreporter.write_line(
            f"ERRORES EN LA LISTA ({len(failed)}) — el fallo no fue absorbido por xfail:"
        )
        for nodeid in failed[:15]:
            terminalreporter.write_line(f"  {nodeid}")
    if _outside_failures or _collection_errors:
        terminalreporter.write_line(
            f"FUERA DE LA LISTA ({outside_count}) — rojo real, no cubierto por la cuarentena:"
        )
        for nodeid, phases in sorted(_outside_failures.items())[:15]:
            terminalreporter.write_line(f"  {nodeid} [{'/'.join(sorted(phases))}]")
        for nodeid, detail in sorted(_collection_errors.items())[:15]:
            terminalreporter.write_line(f"  {nodeid} [collection] {detail}")
    if _quarantine_errors:
        terminalreporter.write_line(
            f"ERRORES EN CUARENTENA ({len(_quarantine_errors)}) — setup/teardown no son el hueco declarado:"
        )
        for nodeid, phases in sorted(_quarantine_errors.items())[:15]:
            terminalreporter.write_line(f"  {nodeid} [{'/'.join(sorted(phases))}]")
    if _strict_xpasses:
        terminalreporter.write_line(
            f"XPASS ESTRICTO ({len(_strict_xpasses)}) — marker propio, pytest lo mantiene rojo:"
        )
        for nodeid in sorted(_strict_xpasses)[:15]:
            terminalreporter.write_line(f"  {nodeid}")
    if stale:
        terminalreporter.write_line(
            f"NO_RECOLECTADOS ({len(stale)}) — nodeids de la lista que esta "
            "corrida no recolecto. En Windows debe ser 0; con FORCE sobre "
            "Linux es esperado para los tests windows-only (test_phase5.py):"
        )
        for nodeid in stale[:15]:
            terminalreporter.write_line(f"  {nodeid}")
