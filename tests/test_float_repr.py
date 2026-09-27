"""FLOAT_REPR_V1 — float -> text, differential against the CPython 3.12.4 oracle.

piton/float_repr.h is the SINGLE source of truth for both backends:

    piton/native_runtime.c   #include "float_repr.h"      -> Windows PE
    piton/linux_x86.py       splices the same file into the freestanding
                             ELF C compiled -nostdlib -ffreestanding -> Linux

These tests compile that exact file (no re-implementation) and diff its output
against repr(). Everything is seeded, so a failure is always reproducible.

What used to be wrong and is now pinned by these tests:
  * Linux rendered at most 12 fractional digits and NEVER used scientific
    notation, so 0.1 + 0.2 came out as "0.300000000000" and 2**54 as
    "18014398509481984.0".
  * Windows used "%.15g", so 12345.6789 came out as "12345.678900000001".
  * Both dropped the sign of -0.0 on the Linux path.
"""
from __future__ import annotations

import math
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from piton.native_differential import compare_native_to_cpython

ROOT = Path(__file__).resolve().parents[1]
FLOAT_REPR_H = ROOT / "piton" / "float_repr.h"
NATIVE_RUNTIME_C = ROOT / "piton" / "native_runtime.c"

GCC = shutil.which("gcc")
NM = shutil.which("nm")
needs_gcc = unittest.skipUnless(GCC, "gcc is required for the C differential")
needs_gcc_and_nm = unittest.skipUnless(
    GCC and NM, "gcc + nm are required for the freestanding link check"
)


def _bits(x: float) -> int:
    return struct.unpack("<Q", struct.pack("<d", x))[0]


def _from_bits(b: int) -> float:
    return struct.unpack("<d", struct.pack("<Q", b))[0]


def repr_cases(random_count: int = 50000, seed: int = 20260927) -> list[float]:
    """Values chosen to stress every branch of the renderer.

    Deliberately includes the structured families (power-of-10 binades,
    power-of-2 binade edges, the whole subnormal sweep) because random
    doubles almost never land on the boundaries where the rounding interval
    actually matters.
    """
    out: list[float] = []
    rng = random.Random(seed)

    # Uniform random bit patterns (NaN/inf payloads included on purpose).
    seen = 0
    while seen < random_count:
        b = rng.getrandbits(64)
        out.append(_from_bits(b))
        seen += 1

    # Every power of ten, at and around its boundaries.
    for e in range(-330, 309):
        try:
            base = float(f"1e{e}")
        except OverflowError:
            continue
        if base == 0.0:
            continue
        for d in (1, 1.5, 9, 9.999999999, 1.0000001, 3.14159, 2.71828, 5,
                  9.999999999999998):
            y = base * d
            if math.isfinite(y) and y != 0.0:
                out.append(y)

    # Every binade edge, plus its two neighbours (the interval endpoints).
    for k in range(-1080, 1024):
        base = math.ldexp(1.0, k)
        if not math.isfinite(base) or base == 0.0:
            continue
        out.append(base)
        out.append(math.nextafter(base, math.inf))
        out.append(math.nextafter(base, -math.inf))

    # First 4096 bit patterns = the whole subnormal range worth sampling.
    for i in range(4096):
        out.append(_from_bits(i))

    # Hand-picked decimals, including the historical failures.
    for s in (
        "0.1", "0.2", "0.3", "1.1", "2.675", "100.1", "123.456", "1e-4",
        "1e-5", "0.123456789012345", "1.7976931348623157e308",
        "2.2250738585072014e-308", "4.9406564584124654e-324",
        "1e-323", "1e-322", "1e16", "1e15", "2.016703144176289e+16",
        "9007199254740992.0", "18014398509481984.0", "12345.6789",
    ):
        out.append(float(s))

    out += [0.0, -0.0, float("inf"), float("-inf"), float("nan")]
    # A NaN with a payload bit set (still renders as "nan", never "-nan").
    out.append(_from_bits(0xFFF8000000000000))
    return out


def _compile(source: str, extra: list[str], out_name: str, workdir: Path,
             werror: bool = True) -> Path:
    src = workdir / f"{out_name}.c"
    exe = workdir / out_name
    src.write_text(source, encoding="utf-8")
    # -Werror applies to the harness and to float_repr.h only; native_runtime.c
    # carries pre-existing -Wunused-parameter warnings that are out of scope here.
    cmd = [GCC, "-std=c11", "-O2", "-Wall", "-Wextra"]
    if werror:
        cmd.append("-Werror")
    cmd += ["-I", str(ROOT / "piton")]
    cmd += [str(src)]
    cmd += extra
    cmd += ["-o", str(exe)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode:
        raise AssertionError(f"compile failed:\n{proc.stdout}\n{proc.stderr}")
    return exe


def _run_hex(exe: Path, values: list[float], newline: bytes = b"\n") -> list[str]:
    payload = "".join(f"{_bits(v):016x}\n" for v in values).encode()
    proc = subprocess.run([str(exe)], input=payload, capture_output=True, timeout=300)
    if proc.returncode:
        raise AssertionError(f"harness exit {proc.returncode}: {proc.stderr[:400]!r}")
    text = proc.stdout.replace(b"\r\n", b"\n").decode()
    lines = text.split("\n")
    assert lines and lines[-1] == "", "harness must end with a newline"
    lines.pop()
    assert len(lines) == len(values), (len(lines), len(values))
    return lines


_HEADER_HARNESS = r"""
#include <stdio.h>
#include "float_repr.h"
int main(void) {
    char line[64];
    char out[PITON_REPR_MAX];
    while (fgets(line, sizeof line, stdin)) {
        unsigned long long bits = 0;
        int n;
        if (sscanf(line, "%llx", &bits) != 1) continue;
        n = piton_repr_double(out, bits);
        fwrite(out, 1, (size_t)n, stdout);
        fputc('\n', stdout);
    }
    return 0;
}
"""

# Calls the REAL printer that the Windows PE backend links against.
_RUNTIME_HARNESS = r"""
#include <stdio.h>
#include <string.h>
void piton_print_float(double value);
int main(void) {
    char line[64];
    while (fgets(line, sizeof line, stdin)) {
        unsigned long long bits = 0;
        double d;
        if (sscanf(line, "%llx", &bits) != 1) continue;
        memcpy(&d, &bits, 8);
        piton_print_float(d);
    }
    return 0;
}
"""


class FloatReprHeaderDifferential(unittest.TestCase):
    """The shipped header must reproduce CPython repr() byte for byte."""

    @needs_gcc
    def test_header_matches_cpython_repr(self):
        values = repr_cases()
        with tempfile.TemporaryDirectory(prefix="piton-float-repr-") as directory:
            exe = _compile(_HEADER_HARNESS, [], "repr_harness", Path(directory))
            got = _run_hex(exe, values)
        want = [repr(v) for v in values]
        mismatches = [(w, g) for w, g in zip(want, got) if w != g]
        self.assertEqual(
            mismatches, [],
            f"{len(mismatches)}/{len(values)} mismatched, first: {mismatches[:10]}",
        )

    @needs_gcc
    def test_historical_failures_render_correctly(self):
        """Values the old %.15g / 12-digit printers got wrong."""
        cases = {
            0.1 + 0.2: "0.30000000000000004",
            12345.6789: "12345.6789",
            -0.0: "-0.0",
            float(1 << 54): "1.8014398509481984e+16",
            float(10 ** 18): "1e+18",
            1e-12: "1e-12",
            float("inf"): "inf",
            float("-inf"): "-inf",
            float("nan"): "nan",
        }
        values = list(cases)
        with tempfile.TemporaryDirectory(prefix="piton-float-repr-") as directory:
            exe = _compile(_HEADER_HARNESS, [], "repr_harness", Path(directory))
            got = _run_hex(exe, values)
        for value, expected in cases.items():
            idx = values.index(value)
            self.assertEqual(got[idx], expected, f"repr({value!r})")

    @needs_gcc_and_nm
    def test_header_links_freestanding_without_libgcc(self):
        """-nostdlib must not pull in __udivdi3/__moddi3 helpers.

        The Linux backend compiles with -nostdlib -ffreestanding, so any
        64-bit division the renderer needs has to lower to a native `div`.
        """
        source = r"""
#include "float_repr.h"
int piton_repr_probe(char *out, unsigned long long b) { return piton_repr_double(out, b); }
"""
        with tempfile.TemporaryDirectory(prefix="piton-float-repr-") as directory:
            workdir = Path(directory)
            src = workdir / "free.c"
            obj = workdir / "free.o"
            src.write_text(source, encoding="utf-8")
            proc = subprocess.run(
                [GCC, "-std=c11", "-O2", "-ffreestanding", "-fno-stack-protector",
                 "-nostdlib", "-I", str(ROOT / "piton"),
                 "-c", str(src), "-o", str(obj)],
                capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            undefined = subprocess.run(
                [NM, "-u", str(obj)], capture_output=True, text=True, check=False
            ).stdout.strip()
        self.assertEqual(undefined, "", f"undefined symbols: {undefined}")

    def test_header_exists_and_declares_the_contract(self):
        text = FLOAT_REPR_H.read_text(encoding="utf-8")
        self.assertIn("#define PITON_REPR_MAX", text)
        self.assertIn("static int piton_repr_double(", text)
        self.assertIn("#ifndef PITON_FLOAT_REPR_H", text)


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux-only splice check")
class FloatReprLinuxSplice(unittest.TestCase):
    def test_header_is_spliced_before_the_runtime(self):
        from piton import linux_x86

        self.assertGreater(len(linux_x86._FLOAT_REPR_C), 1000)
        combined = linux_x86._FLOAT_REPR_C + linux_x86._RICH_FREESTANDING_C
        define = combined.find("#define PITON_REPR_MAX")
        printers = combined.find("static void piton_print_float_bits")
        self.assertNotEqual(define, -1, "header missing from the freestanding C")
        self.assertNotEqual(printers, -1, "float printer missing from the runtime")
        self.assertLess(define, printers, "header must precede the runtime printers")
        self.assertNotIn("%.15g", linux_x86._RICH_FREESTANDING_C)
        self.assertNotIn("1000000000000.0", linux_x86._RICH_FREESTANDING_C)


@needs_gcc
class NativeRuntimePrintFloatDifferential(unittest.TestCase):
    """piton_print_float is the entry point the Windows PE backend calls."""

    def test_piton_print_float_matches_cpython_repr(self):
        values = repr_cases(random_count=4000)
        with tempfile.TemporaryDirectory(prefix="piton-float-repr-") as directory:
            exe = _compile(
                _RUNTIME_HARNESS,
                [str(NATIVE_RUNTIME_C), "-lm"],
                "runtime_harness", Path(directory), werror=False,
            )
            got = _run_hex(exe, values)
        want = [repr(v) for v in values]
        mismatches = [(w, g) for w, g in zip(want, got) if w != g]
        self.assertEqual(
            mismatches, [],
            f"{len(mismatches)}/{len(values)} mismatched, first: {mismatches[:10]}",
        )


# Float arithmetic available on BOTH backends is + - * and literals only
# (the Linux emitter has no / or **; PITON literals have no exponent syntax
# yet), so the end-to-end batch sticks to that common subset.
_END_TO_END = [
    "0.1 + 0.2",
    "1.5 * 0.1",
    "1000000000.0 * 1000000000.0",
    "9007199254740992.0 * 2.0",
    "123456789.0 * 123456789.0",
    "0.000001 * 0.000001",
    "0.1",
    "2.675",
    "100.1",
    "0.0001",
    "12345.6789",
    "0.123456789012345",
    "-0.0",
    "0.0",
    "-1.5",
]


class FloatReprEndToEnd(unittest.TestCase):
    """imprimir()/str() of a float must equal CPython on the host backend."""

    def test_imprimir_matches_cpython(self):
        source = "".join(f"imprimir({expr})\n" for expr in _END_TO_END)
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)

    def test_str_builtin_matches_cpython(self):
        source = "".join(
            f'imprimir(str({expr}))\n' for expr in ("0.1", "12345.6789", "-0.0",
                                                     "0.1 + 0.2")
        )
        result = compare_native_to_cpython(source)
        self.assertTrue(result.equivalent, result)


if __name__ == "__main__":
    unittest.main()
