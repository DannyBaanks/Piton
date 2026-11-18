"""PARIDAD/ANTI-MENTIRA del corpus ``formato``.

Tres capas:
1. ``comparar(codigo)`` ejecuta CPython 3.12 y PITON nativo, y solo declara
   ``EQUIV`` con exit code igual y stdout byte a byte igual, o con la misma
   excepcion en ambos lados.
2. El test del test demuestra que una salida distinta, una excepcion distinta
   o una salida vacia no pueden convertirse en un falso verde.
3. Los 15 ejemplos numerados de ``corpus_t1/formato/`` se miden sin descartar
   ninguno y se informa el conteo real.

La ruta nativa es la del corpus primario: ``_stage_impl`` adapta y prepara el
programa, y ``compile_native_linux`` genera el ejecutable que se mide.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from native_linux import compile_native_linux, require_linux_native  # noqa: E402
from tools.parity_corpus_v4 import _stage_impl  # noqa: E402


EQUIV = "EQUIV"
DIVERGENT = "DIVERGENT"
BUILD_FAIL = "BUILD_FAIL"

_EXCEPCION_RE = re.compile(
    rb"(?m)^([A-Za-z_][A-Za-z_0-9_]*)(?::[ \t]?([^\r\n]*))?$"
)


@dataclass(frozen=True)
class _Ejecucion:
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    build_error: str = ""
    timeout: bool = False


@dataclass(frozen=True)
class _Excepcion:
    tipo: bytes
    mensaje: bytes


class Resultado(str):
    """Veredicto compatible con ``str`` que conserva la evidencia exacta."""

    def __new__(cls, veredicto: str, detalle: str = "") -> "Resultado":
        instancia = super().__new__(cls, veredicto)
        instancia.veredicto = veredicto
        instancia.detalle = detalle
        return instancia


def _ejecutar_cpython(codigo: str) -> _Ejecucion:
    try:
        proceso = subprocess.run(
            [sys.executable, "-c", codigo],
            capture_output=True,
            timeout=60,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        return _Ejecucion(
            None,
            exc.stdout or b"",
            exc.stderr or b"",
            timeout=True,
        )
    return _Ejecucion(proceso.returncode, proceso.stdout, proceso.stderr)


def _ejecutar_piton(codigo: str) -> _Ejecucion:
    require_linux_native()
    try:
        with tempfile.TemporaryDirectory(prefix="piton-parity-formato-") as directorio:
            base = Path(directorio)
            fuente = base / "programa.py"
            fuente.write_text(codigo, encoding="utf-8")

            registro, fuente_piton = _stage_impl(str(fuente))
            etapa = registro.get("stage_fail")
            if etapa:
                detalle = registro.get("detail", "")
                return _Ejecucion(
                    None,
                    b"",
                    b"",
                    build_error=f"stage={etapa}; detail={detalle!r}",
                )
            if fuente_piton is None:
                return _Ejecucion(
                    None,
                    b"",
                    b"",
                    build_error="stage no produjo fuente PITON",
                )

            ejecutable = base / "programa"
            compile_native_linux(fuente_piton, ejecutable)
            try:
                proceso = subprocess.run(
                    [str(ejecutable)],
                    capture_output=True,
                    timeout=60,
                    cwd=base,
                    stdin=subprocess.DEVNULL,
                )
            except subprocess.TimeoutExpired as exc:
                return _Ejecucion(
                    None,
                    exc.stdout or b"",
                    exc.stderr or b"",
                    timeout=True,
                )
            return _Ejecucion(
                proceso.returncode,
                proceso.stdout,
                proceso.stderr,
            )
    except Exception as exc:
        return _Ejecucion(
            None,
            b"",
            b"",
            build_error=f"{type(exc).__name__}: {exc}",
        )


def _excepcion(stderr: bytes) -> _Excepcion | None:
    coincidencias = list(_EXCEPCION_RE.finditer(stderr))
    if not coincidencias:
        return None
    coincidencia = coincidencias[-1]
    mensaje = coincidencia.group(2)
    return _Excepcion(
        coincidencia.group(1),
        b"" if mensaje is None else mensaje,
    )


def _diff_exacto(
    cpython: _Ejecucion,
    piton: _Ejecucion,
    motivo: str,
) -> str:
    return (
        f"{motivo}; "
        f"CPython.stdout={cpython.stdout!r}; "
        f"PITON.stdout={piton.stdout!r}; "
        f"CPython.exit_code={cpython.exit_code!r}; "
        f"PITON.exit_code={piton.exit_code!r}; "
        f"CPython.stderr={cpython.stderr!r}; "
        f"PITON.stderr={piton.stderr!r}"
    )


def _decidir(cpython: _Ejecucion, piton: _Ejecucion) -> Resultado:
    if cpython.timeout or piton.timeout:
        return Resultado(
            DIVERGENT,
            _diff_exacto(cpython, piton, "timeout en uno de los runtimes"),
        )
    if cpython.exit_code is None or piton.exit_code is None:
        return Resultado(
            DIVERGENT,
            _diff_exacto(cpython, piton, "exit code no disponible"),
        )
    if cpython.exit_code != piton.exit_code:
        return Resultado(
            DIVERGENT,
            _diff_exacto(cpython, piton, "exit code distinto"),
        )
    if cpython.stdout != piton.stdout:
        return Resultado(
            DIVERGENT,
            _diff_exacto(cpython, piton, "stdout distinto byte a byte"),
        )

    if cpython.exit_code != 0:
        excepcion_cpython = _excepcion(cpython.stderr)
        excepcion_piton = _excepcion(piton.stderr)
        if excepcion_cpython is None or excepcion_piton is None:
            return Resultado(
                DIVERGENT,
                _diff_exacto(cpython, piton, "fallo sin excepcion identificable"),
            )
        if excepcion_cpython != excepcion_piton:
            return Resultado(
                DIVERGENT,
                _diff_exacto(cpython, piton, "excepcion distinta"),
            )
        return Resultado(EQUIV)

    if cpython.stdout == b"":
        return Resultado(
            DIVERGENT,
            _diff_exacto(cpython, piton, "stdout vacio no se acepta como EQUIV"),
        )
    return Resultado(EQUIV)


def comparar_detallado(codigo: str) -> Resultado:
    """Ejecuta ambos runtimes y devuelve veredicto y evidencia exacta."""
    cpython = _ejecutar_cpython(codigo)
    piton = _ejecutar_piton(codigo)
    if piton.build_error:
        return Resultado(
            BUILD_FAIL,
            f"PITON no compiló: {piton.build_error}",
        )
    return _decidir(cpython, piton)


def comparar(codigo: str) -> Resultado:
    """Devuelve ``EQUIV``, ``DIVERGENT`` o ``BUILD_FAIL`` sin normalizar stdout."""
    return comparar_detallado(codigo)


# ---------------------------------------------------------------------------
# CAPA 2: test del test
# ---------------------------------------------------------------------------


def _fijar_ejecuciones(monkeypatch, cpython: _Ejecucion, piton: _Ejecucion) -> None:
    monkeypatch.setattr(sys.modules[__name__], "_ejecutar_cpython", lambda _codigo: cpython)
    monkeypatch.setattr(sys.modules[__name__], "_ejecutar_piton", lambda _codigo: piton)


def test_capa2_caza_stdout_distinto(monkeypatch) -> None:
    cpython = _Ejecucion(0, b"resultado real\n", b"")
    piton = _Ejecucion(0, b"resultado inventado\n", b"")
    _fijar_ejecuciones(monkeypatch, cpython, piton)

    resultado = comparar("print('resultado real')")

    assert resultado.veredicto == DIVERGENT
    assert repr(cpython.stdout) in resultado.detalle
    assert repr(piton.stdout) in resultado.detalle


def test_capa2_misma_excepcion_es_equivalente(monkeypatch) -> None:
    cpython = _Ejecucion(
        1,
        b"",
        b"Traceback (most recent call last):\nValueError: boom\n",
    )
    piton = _Ejecucion(1, b"", b"ValueError: boom\n")
    _fijar_ejecuciones(monkeypatch, cpython, piton)

    resultado = comparar("raise ValueError('boom')")

    assert resultado.veredicto == EQUIV


def test_capa2_piton_vacio_y_cpython_con_salida_no_es_equivalente(monkeypatch) -> None:
    cpython = _Ejecucion(0, b"hay salida\n", b"")
    piton = _Ejecucion(0, b"", b"")
    _fijar_ejecuciones(monkeypatch, cpython, piton)

    resultado = comparar("print('hay salida')")

    assert resultado.veredicto != EQUIV
    assert resultado.veredicto == DIVERGENT


def test_capa2_ambos_vacios_tampoco_son_equivalentes(monkeypatch) -> None:
    cpython = _Ejecucion(0, b"", b"")
    piton = _Ejecucion(0, b"", b"")
    _fijar_ejecuciones(monkeypatch, cpython, piton)

    resultado = comparar("pass")

    assert resultado.veredicto != EQUIV
    assert resultado.veredicto == DIVERGENT


# ---------------------------------------------------------------------------
# CAPA 3: corpus formato, 15 ejemplos con denominador fijo
# ---------------------------------------------------------------------------

EJEMPLOS = (
    "01.py",
    "02.py",
    "03.py",
    "04.py",
    "05.py",
    "06.py",
    "07.py",
    "08.py",
    "09.py",
    "10.py",
    "11.py",
    "12.py",
    "13.py",
    "14.py",
    "15.py",
)
TOTAL_EJEMPLOS = 15
CORPUS_FORMATO = REPO / "corpus_t1" / "formato"


def test_capa3_formato_15_ejemplos_conteo_real() -> None:
    resultados: list[tuple[str, Resultado]] = []

    for nombre in EJEMPLOS:
        ruta = CORPUS_FORMATO / nombre
        if not ruta.is_file():
            resultado = Resultado(BUILD_FAIL, f"falta {ruta}")
        else:
            codigo = ruta.read_text(encoding="utf-8")
            resultado = comparar(codigo)
        resultados.append((nombre, resultado))

    equiv = sum(1 for _nombre, resultado in resultados if resultado.veredicto == EQUIV)
    divergent = sum(
        1 for _nombre, resultado in resultados if resultado.veredicto == DIVERGENT
    )
    build_fail = sum(
        1 for _nombre, resultado in resultados if resultado.veredicto == BUILD_FAIL
    )
    procesados = sum(1 for _nombre, _resultado in resultados)

    print("\n=== PARIDAD FORMATO ===")
    for nombre, resultado in resultados:
        print(f"{resultado.veredicto}: {nombre}")
        if resultado.veredicto != EQUIV:
            print(f"  {resultado.detalle}")
    print(
        f"TOTAL={procesados} EQUIV={equiv} "
        f"DIVERGENT={divergent} BUILD_FAIL={build_fail}"
    )

    fallos = [
        f"{nombre}: {resultado.veredicto}: {resultado.detalle}"
        for nombre, resultado in resultados
        if resultado.veredicto != EQUIV
    ]
    assert procesados == TOTAL_EJEMPLOS, fallos
    assert not fallos, "\n".join(fallos)
    assert equiv == TOTAL_EJEMPLOS, fallos
