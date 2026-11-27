"""Ola 25 (`w25/tierb-stdlib`) — estado MEDIDO de `corpus_v4/tutorial/stdlib.py`.

Este fichero no "arregla" nada: MIDE y FIJA el estado de la pagina `stdlib` del
tutorial, que es el unico script DIVERGENT de tier B (10/11 EQUIV). Sirve de
regresion para dos cosas opuestas:

1. que los otros 51 ejemplos sigan siendo byte a byte identicos a CPython 3.12
   (si uno deja de serlo, el test dice CUAL, no solo "algo cambio");
2. que el ejemplo divergente (`help(os)`) no se marque como paridad: PITON
   imprime ayuda nativa basada en metadata disponible, mientras CPython genera
   una pagina pydoc con el docstring, el inventario y el entorno del host.

Evidencia completa (comando + salida cruda) en
`docs/TIER_B_STD_LIB_HELP_GAP_2026-11-27.md`.
"""
from __future__ import annotations

import functools
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CORPUS_PAGE = ROOT / "corpus_v4" / "tutorial" / "stdlib.py"


def _load_corpus_module():
    """Carga `tools/parity_corpus_v4.py` (no es un paquete) para reusar SU
    adaptador Python->PITON y SU regla de comparacion: un ejemplo en un
    try/except BaseException + print('EXC')."""
    spec = importlib.util.spec_from_file_location(
        "w25_parity_corpus_v4", ROOT / "tools" / "parity_corpus_v4.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PC4 = _load_corpus_module()

from piton.linux_x86 import compile_native_linux  # noqa: E402  (tras ROOT en sys.path)


def _page_source() -> str:
    return CORPUS_PAGE.read_text(encoding="utf-8")


def _example_sources() -> list[str]:
    """Fuente Python de cada ejemplo del tutorial, en orden (1..N).

    Reproduce la particion del arnes del corpus: `try:` abre ejemplo, y el
    ejemplo termina en su `except BaseException`. El estado entre ejemplos es el
    de la sesion REPL, asi que se miden sobre la pagina ENTERA, no de uno en uno.
    """
    sources: list[str] = []
    current: list[str] | None = None
    for line in _page_source().splitlines():
        if line.startswith("try:"):
            current = []
        elif current is not None:
            if line.startswith("except BaseException"):
                sources.append("\n".join(current))
                current = None
            else:
                current.append(line)
    return sources


def _instrumented_page() -> str:
    """Inserte `print('@@N')` antes de cada ejemplo (sin tocar la semantica)."""
    lines: list[str] = []
    index = 0
    for line in _page_source().splitlines():
        if line == "try:":
            index += 1
            lines.append(f"print('@@{index}')")
        lines.append(line)
    return "\n".join(lines) + "\n"


def _segments(raw: bytes) -> dict[int, list[str]]:
    result: dict[int, list[str]] = {}
    key: int | None = None
    for line in raw.decode("utf-8", "replace").splitlines():
        if line.startswith("@@") and line[2:].isdigit():
            key = int(line[2:])
            result.setdefault(key, [])
        elif key is not None:
            result[key].append(line)
    return result


def _native_run(source: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory(prefix="w25-tierb-unit-") as directory:
        executable = compile_native_linux(source, Path(directory) / "p")
        return subprocess.run(
            [str(executable)], capture_output=True, timeout=120,
            cwd=directory, stdin=subprocess.DEVNULL,
        )


@functools.lru_cache(maxsize=1)
def _page_measurement() -> dict:
    """Mide la pagina completa nativo vs CPython (oraculo) con la regla del corpus.

    Un ejemplo que el lado nativo nunca alcanza (programa detenido antes) NO
    cuenta como igual: se marca NO_EJECUTADO (ver la guarda de
    tools/tier_b_divergence_map.py).
    """
    source = _instrumented_page()
    adapted = PC4.python_to_piton(source)
    with tempfile.TemporaryDirectory(prefix="w25-tierb-page-") as directory:
        executable = compile_native_linux(adapted, Path(directory) / "p")
        native = subprocess.run(
            [str(executable)], capture_output=True, timeout=180,
            cwd=directory, stdin=subprocess.DEVNULL,
        )
        oracle = subprocess.run(
            [sys.executable, "-c", source], capture_output=True, timeout=180,
            cwd=directory, stdin=subprocess.DEVNULL,
        )
    native_segments = _segments(native.stdout)
    oracle_segments = _segments(oracle.stdout)
    examples = _example_sources()
    divergent: list[dict] = []
    for index in range(1, len(examples) + 1):
        # `or []` convertiria un segmento VACIO (que si es igualdad: los dos
        # lados no imprimen nada) en "no ejecutado": se distingue con `is None`.
        piton = native_segments.get(index)
        if piton is None:
            piton = ["NO_EJECUTADO (el programa nativo se detuvo antes)"]
        cpython = oracle_segments.get(index, [])
        if piton != cpython:
            divergent.append({
                "chunk": index,
                "source": examples[index - 1].strip(),
                "piton": piton,
                "cpython": cpython,
            })
    return {"examples": len(examples), "divergent": divergent,
            "native_rc": native.returncode, "oracle_rc": oracle.returncode}


class TierBStdlibPageV1(unittest.TestCase):
    """La pagina `stdlib`: 51 de 52 ejemplos byte a byte identicos."""

    def test_page_has_52_examples_and_both_sides_finish(self):
        measurement = _page_measurement()
        self.assertEqual(measurement["examples"], 52)
        self.assertEqual(measurement["native_rc"], measurement["oracle_rc"])
        self.assertEqual(measurement["native_rc"], 0)

    def test_the_only_divergent_example_is_help_os(self):
        measurement = _page_measurement()
        divergent = measurement["divergent"]
        self.assertEqual(
            [item["chunk"] for item in divergent], [7],
            "se esperaba EXACTAMENTE el ejemplo 7 (`help(os)`) como divergente; "
            f"obtenido: {[(i['chunk'], i['source']) for i in divergent]}",
        )
        self.assertEqual(divergent[0]["source"], "help(os)")
        self.assertEqual(divergent[0]["piton"][0], "Help on module os:")
        self.assertEqual(divergent[0]["cpython"][0], "Help on module os:")
        self.assertNotEqual(divergent[0]["piton"], divergent[0]["cpython"])
        self.assertGreater(len(divergent[0]["cpython"]), 1000)

    def test_every_other_example_is_byte_identical(self):
        measurement = _page_measurement()
        for item in measurement["divergent"]:
            if item["chunk"] != 7:
                self.fail(f"ejemplo {item['chunk']} ({item['source']}) diverge: "
                          f"piton={item['piton'][:5]} cpython={item['cpython'][:5]}")


class HelpOsNotClosableV1(unittest.TestCase):
    """Por que `help(os)` NO es paridad completa aunque help() ya exista.

    La salida esperada NO es una constante. CPython imprime el docstring y el
    inventario completos del modulo `os`, además de su pagina FILE con la ruta
    de la stdlib instalada. PITON conserva únicamente la metadata real que sus
    fuentes modelan; sustituirla por un golden file no sería paridad.
    """

    def _oracle_help_os(self, extra_env: dict[str, str] | None = None) -> str:
        environment = dict(os.environ)
        environment.update(extra_env or {})
        process = subprocess.run(
            [sys.executable, "-c", "import os\nhelp(os)\n"],
            capture_output=True, timeout=180, env=environment,
            stdin=subprocess.DEVNULL,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        return process.stdout.decode("utf-8", "replace")

    def test_oracle_output_is_environment_dependent_not_a_constant(self):
        base = self._oracle_help_os()
        with_sentinel = self._oracle_help_os({"PITON_W25_SENTINEL": "w25"})
        self.assertNotEqual(
            base, with_sentinel,
            "la pagina de ayuda de `os` imprime os.environ: NO es un texto fijo, "
            "asi que ningun golden file puede ser honesto",
        )
        differing = [
            (a, b) for a, b in zip(base.splitlines(), with_sentinel.splitlines())
            if a != b
        ]
        self.assertTrue(differing)
        self.assertIn("environ = environ({", differing[0][1])

    def test_oracle_output_needs_the_cpython_os_docstring_and_stdlib_path(self):
        output = self._oracle_help_os()
        self.assertTrue(output.startswith("Help on module os:\n"))
        # primera linea del docstring de CPython (os.__doc__)
        self.assertIn("OS routines for NT or Posix", output)
        # seccion FILE: la ruta de la stdlib instalada en este equipo
        stdlib_dir = os.path.dirname(os.__file__)
        self.assertIn(f"    {stdlib_dir}/os.py", output)
        self.assertGreater(len(output.splitlines()), 1000)

    def test_native_help_runs_but_keeps_the_documented_os_gap(self):
        native = _native_run(
            "intentar:\n"
            "    importar os\n"
            "    help (os )\n"
            "excepto BaseException como e :\n"
            "    imprimir ('EXC',type (e ).__name__ )\n"
        )
        self.assertEqual(native.returncode, 0, native.stderr)
        self.assertTrue(native.stdout.startswith(b"Help on module os:\n"), native.stdout)
        self.assertNotIn(b"EXC", native.stdout)
        self.assertNotIn(b"OS routines for NT or Posix", native.stdout)

    def test_bundled_piton_stdlib_has_no_module_docstrings(self):
        """Lo que habria que fabricar: la stdlib bundled de PITON no tiene
        docstrings que un `help()` genuino pudiera imprimir."""
        documents = 0
        for path in (ROOT / "piton" / "stdlib_piton").glob("*.piton"):
            if '"""' in path.read_text(encoding="utf-8", errors="replace"):
                documents += 1
        self.assertEqual(documents, 0)


class KnownGapsV1(unittest.TestCase):
    """Brechas REALES del backend que el corpus NO ve.

    `dir(os)` (ejemplo 6) y `vars()` (page inputoutput, ejemplo 39) son EQUIV en
    el corpus solo porque su valor de retorno se descarta: una expresion como
    enunciado no imprime nada en CPython. En cuanto se CONSUME el valor, divergen.
    Son `expectedFailure`: si alguno empieza a pasar, la brecha se cerro y hay
    que quitar el marcador (y actualizar el documento).
    """

    def assert_parity(self, piton_src: str, py_src: str) -> None:
        native = _native_run(piton_src)
        oracle = subprocess.run([sys.executable, "-c", py_src], capture_output=True,
                                timeout=120, stdin=subprocess.DEVNULL)
        self.assertEqual(oracle.returncode, native.returncode,
                         f"native stderr={native.stderr!r} oracle stderr={oracle.stderr!r}")
        self.assertEqual(oracle.stdout, native.stdout,
                         f"native stdout={native.stdout!r} oracle stdout={oracle.stdout!r}")

    @unittest.expectedFailure
    def test_dir_consumed_returns_the_attribute_names(self):
        # GAP_DIR_OS_V1: dir() ya ordena los atributos conocidos por PITON,
        # pero el modulo bundled de os no exporta el inventario completo de
        # CPython. `dir(os)` sigue siendo una brecha documentada, no paridad.
        self.assert_parity(
            "intentar:\n"
            "    importar os\n"
            "    imprimir (len (dir (os )))\n"
            "excepto BaseException:\n"
            "    imprimir ('EXC')\n",
            "try:\n"
            "    import os\n"
            "    print(len(dir(os)))\n"
            "except BaseException:\n"
            "    print('EXC')\n",
        )

    @unittest.expectedFailure
    def test_vars_consumed_returns_the_module_namespace(self):
        # GAP_VARS_V1: `vars()` devuelve el diccionario de PITON, no el modulo:
        # nombres distintos (os__path__var__sep, ...) y, en CPython, repr con
        # direcciones de heap (NO_DEMONSTRATED por construccion, igual que los
        # tracebacks de T14).
        self.assert_parity(
            "intentar:\n"
            "    imprimir (vars ())\n"
            "excepto BaseException:\n"
            "    imprimir ('EXC')\n",
            "try:\n"
            "    print(vars())\n"
            "except BaseException:\n"
            "    print('EXC')\n",
        )


if __name__ == "__main__":
    unittest.main()
