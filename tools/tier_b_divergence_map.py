#!/usr/bin/env python3
"""tier_b_divergence_map - mapa POR EJEMPLO de los scripts DIVERGENT de tier B.

`parity_corpus_v4.py run --tier b` dice QUE scripts divergen, no POR QUE. Un
script es todo-o-nada: 11 ejemplos pueden estar perfectos y el duodécimo
tirar el script entero a DIVERGENT, sin que el reporte diga cuál.

Este instrumento responde a la pregunta util para cerrar la brecha:

  - reparte el script en sus ejemplos (el arnes del corpus mete cada ejemplo
    del tutorial en un try/except con print('EXC'));
  - numera cada ejemplo y hace VISIBLE la excepcion que el arnes oculta
    (print('EXC', e) en vez de print('EXC'));
  - corre la variante diagnostica de los dos lados (nativo Linux y CPython);
  - compara ejemplo por ejemplo con la REGLA HONESTA del corpus: una
    excepcion en los dos lados es EXC == EXC (equivale), porque asi es como
    el corpus cuenta; solo se marca divergente cuando un lado imprime y el
    otro no, o cuando las salidas difieren.

NO altera la metrica del corpus: es un instrumento de diagnostico. La fuente
de verdad del estado sigue siendo `parity_corpus_v4.py run`.

Uso:
  python3 tools/tier_b_divergence_map.py                    # los 3 divergentes
  python3 tools/tier_b_divergence_map.py --all              # los 11 de tier B
  python3 tools/tier_b_divergence_map.py --script stdlib2 --json salida.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CORPUS = REPO / "corpus_v4" / "tutorial"
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _load_v4():
    spec = importlib.util.spec_from_file_location(
        "pc4", REPO / "tools" / "parity_corpus_v4.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PC4 = _load_v4()


def instrument(src: str) -> tuple[str, int]:
    """Numera los ejemplos y hace visible la excepcion que el arnes oculta.

    El corpus mete cada ejemplo del tutorial en `try: ... except BaseException:
    print('EXC')`: el arnes traga la excepcion, asi que un DIVERGENT no dice
    si fallo el modulo que falta, el metodo que falta o un bug del backend.
    Aqui la excepcion sale con su tipo y su mensaje, y cada ejemplo lleva un
    marcador `@@N` para alinear las dos salidas.
    """
    src = re.sub(r"^( *)except BaseException:$", r"\1except BaseException as _e:", src, flags=re.M)
    src = re.sub(r"^( *)print\('EXC'\)$", r"\1print('EXC', _e)", src, flags=re.M)
    out: list[str] = []
    count = 0
    for line in src.splitlines():
        if line == "try:":
            count += 1
            out.append(f"print('@@{count}')")
        out.append(line)
    return "\n".join(out) + "\n", count


def _split_chunks(text: str) -> dict[int, list[str]]:
    chunks: dict[int, list[str]] = {}
    current: int | None = None
    for line in text.splitlines():
        marker = re.match(r"@@(\d+)$", line)
        if marker:
            current = int(marker.group(1))
            chunks.setdefault(current, [])
            continue
        if current is not None:
            chunks[current].append(line)
    return chunks


def _honest(lines: list[str]) -> list[str]:
    """La regla del corpus: cualquier excepcion es EXC (sin detalle)."""
    return ["EXC" if line.startswith("EXC") else line for line in lines]


def _source_of_chunk(diag_src: str, index: int) -> str:
    parts = re.split(r"^print\('@@(\d+)'\)$", diag_src, flags=re.M)
    bodies = {int(parts[i]): parts[i + 1] for i in range(1, len(parts) - 1, 2)}
    body = bodies.get(index, "")
    lines = [
        line.strip()
        for line in body.splitlines()
        if line.strip()
        and line.strip() != "try:"
        and "except BaseException" not in line
        and "print('EXC'" not in line
    ]
    return " | ".join(lines)


def run_script(name: str, timeout: float = 60.0) -> dict:
    """Compila la variante diagnostica, la corre contra CPython y mapea."""
    from piton.linux_x86 import compile_native_linux

    path = CORPUS / f"{name}.py"
    if not path.exists():
        return {"script": name, "error": f"missing {path}", "chunks": []}
    diag_src, chunk_count = instrument(path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="piton-divmap-src-") as src_dir:
        diag_path = Path(src_dir) / f"{name}.diag.py"
        diag_path.write_text(diag_src, encoding="utf-8")
        record, _piton_src = PC4._stage_impl(str(diag_path))
        if record["stage_fail"]:
            return {
                "script": name,
                "error": f"stage_fail {record['stage_fail']}: {record['detail']}",
                "chunks": [],
            }
        _record, piton_diag = PC4._stage_impl(str(diag_path))
        with tempfile.TemporaryDirectory(prefix="piton-divmap-") as directory:
            try:
                exe = compile_native_linux(piton_diag, Path(directory) / "p")
            except Exception as exc:  # noqa: BLE001 - el fallo se reporta, no se oculta
                return {
                    "script": name,
                    "error": f"build: {type(exc).__name__}: {exc}",
                    "chunks": [],
                }
            native = subprocess.run(
                [str(exe)], capture_output=True, timeout=timeout,
                cwd=directory, stdin=subprocess.DEVNULL,
            )
            oracle = subprocess.run(
                [sys.executable, "-c", diag_src], capture_output=True, timeout=timeout,
                cwd=directory, stdin=subprocess.DEVNULL,
            )
    native_chunks = _split_chunks(native.stdout.decode("utf-8", "replace"))
    oracle_chunks = _split_chunks(oracle.stdout.decode("utf-8", "replace"))
    divergent = []
    for index in sorted(oracle_chunks):
        cpy = _honest(oracle_chunks.get(index, []))
        pit = _honest(native_chunks.get(index, []))
        if cpy == pit:
            continue
        divergent.append(
            {
                "chunk": index,
                "source": _source_of_chunk(diag_src, index),
                "cpython": cpy,
                "piton": pit,
            }
        )
    return {
        "script": name,
        "chunks_total": chunk_count,
        "chunks_divergent": len(divergent),
        "native_rc": native.returncode,
        "oracle_rc": oracle.returncode,
        "chunks": divergent,
    }


def render(results: list[dict]) -> str:
    lines = [
        "# Mapa de divergencia por ejemplo (tier B)",
        "",
        "Regla: una excepcion en los dos lados es EXC == EXC (equivale), igual",
        "que en el corpus. Solo diverge cuando un lado imprime y el otro no, o",
        "cuando las salidas difieren.",
        "",
    ]
    for result in results:
        if result.get("error"):
            lines.append(f"## {result['script']} — ERROR: {result['error']}")
            lines.append("")
            continue
        lines.append(
            f"## {result['script']} — {result['chunks_divergent']} de "
            f"{result['chunks_total']} ejemplos divergentes"
        )
        lines.append("")
        lines.append("| # | codigo | CPython | PITON |")
        lines.append("|---|---|---|---|")
        for chunk in result["chunks"]:
            cpy = " / ".join(chunk["cpython"])[:90] or "(vacio)"
            pit = " / ".join(chunk["piton"])[:90] or "(vacio)"
            lines.append(
                f"| {chunk['chunk']} | `{chunk['source'][:70]}` | {cpy} | {pit} |"
            )
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", action="append", default=[],
                        help="script de tier B (repetible); por defecto los 3 divergentes")
    parser.add_argument("--all", action="store_true", help="mapear los 11 scripts de tier B")
    parser.add_argument("--json", help="escribir el mapa en JSON")
    parser.add_argument("--markdown", help="escribir el mapa en Markdown")
    args = parser.parse_args(argv)
    if args.all:
        names = sorted(p.stem for p in CORPUS.glob("*.py"))
    elif args.script:
        names = list(args.script)
    else:
        names = ["inputoutput", "stdlib", "stdlib2"]
    results = [run_script(name) for name in names]
    print(render(results))
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    if args.markdown:
        Path(args.markdown).write_text(render(results), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
