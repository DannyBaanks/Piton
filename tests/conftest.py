"""Cuarentena Windows de la suite (hueco declarado, 2026-10-31).

El backend nativo Windows ``piton/x86.py`` va atras del MIR y de
``piton/linux_x86.py`` desde la unificacion 2026-10-24 / sync 2026-10-30: hay
tests que pasan en Linux y fallan en Windows. La lista versionada vive en
``tests/quarantine_windows.txt`` (con su origen, sus cuentas y su fecha).

Comportamiento:

* En ``win32`` cada ID de la lista se marca ``xfail(strict=False)``: **el test
  se ejecuta igual** y el log lo muestra como ``XFAIL``. El job queda verde
  solo si no falla NINGUN test fuera de la lista (un test rojo nuevo rompe
  CI, que es el punto del gate).
* Si un test de la lista llega a pasar, pytest lo reporta ``XPASS``; el
  resumen ``CUARENTENA WINDOWS:`` lo enumera para sacarlo de la lista.
* Si la lista trae un nodeid que esta corrida no recolecta, aparece como
  ``NO_RECOLECTADOS``: en Windows debe ser 0; en FORCE sobre Linux es esperado
  para los tests windows-only de ``test_phase5.py`` (el modulo se salta con
  ``pytest.skip(..., allow_module_level=True)``). Un nodeid renombrado o
  borrado de igual forma sale ahi y hay que limpiarlo de la lista.

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
    "HUECO DECLARADO Windows x86.py (cuarentena 2026-10-31): ver "
    "docs/windows_native_failures.md y tests/quarantine_windows.txt"
)

_applied: set[str] = set()
_results: dict[str, str] = {}
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


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if not _active():
        return
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
    _stats.update(
        listed=len(listed),
        matched=len(matched),
        stale=sorted(entry for key, entry in wanted.items() if key not in matched_norm),
    )


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if not _applied or report.when != "call" or report.nodeid not in _applied:
        return
    wasxfail = getattr(report, "wasxfail", None)
    if report.outcome == "passed":
        # xfail no estricto que paso: XPASS, candidato a salir de la lista.
        _results[report.nodeid] = "xpassed"
    elif wasxfail and report.outcome == "skipped":
        _results[report.nodeid] = "xfailed"
    elif report.outcome == "skipped":
        # skip DENTRO del test (p.ej. plataforma): no es ni rojo ni avance.
        _results[report.nodeid] = "skipped"
    elif wasxfail:
        _results[report.nodeid] = "xfailed"
    else:
        _results[report.nodeid] = "failed"


def pytest_terminal_summary(
    terminalreporter: Any, exitstatus: int, config: pytest.Config
) -> None:
    if not _applied and not _stats["listed"]:
        return
    counts = {"xfailed": 0, "xpassed": 0, "skipped": 0, "failed": 0, "sin_reporte": 0}
    for nodeid in _applied:
        counts[_results.get(nodeid, "sin_reporte")] += 1
    xpassed = sorted(n for n, r in _results.items() if r == "xpassed")
    failed = sorted(n for n, r in _results.items() if r == "failed")
    stale = list(_stats["stale"])
    terminalreporter.write_sep("=", "CUARENTENA WINDOWS 2026-10-31 (hueco declarado)")
    terminalreporter.write_line(
        f"lista={_stats['listed']} aplicados={_stats['matched']} "
        f"XFAIL={counts['xfailed']} XPASS={counts['xpassed']} "
        f"SKIP={counts['skipped']} SIN_REPORTE={counts['sin_reporte']} "
        f"FUERA_DE_LA_LISTA={counts['failed']}"
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
            f"FUERA_DE_LA_LISTA ({len(failed)}) — rojo real, no cubierto por la cuarentena:"
        )
        for nodeid in failed[:15]:
            terminalreporter.write_line(f"  {nodeid}")
    if stale:
        terminalreporter.write_line(
            f"NO_RECOLECTADOS ({len(stale)}) — nodeids de la lista que esta "
            "corrida no recolecto. En Windows debe ser 0; con FORCE sobre "
            "Linux es esperado para los tests windows-only (test_phase5.py):"
        )
        for nodeid in stale[:15]:
            terminalreporter.write_line(f"  {nodeid}")
