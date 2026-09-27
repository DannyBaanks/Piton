from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest

from piton.final_dashboard import build_dashboard
from piton.native_evidence import inspect_windows_pe, load_windows_evidence
from piton.x86 import NativeBuildError, Win64NasmEmitter, compile_native


ROOT = Path(__file__).resolve().parents[1]


class NativeSubsetEvidenceTests(unittest.TestCase):
    @unittest.skipIf(not sys.platform.startswith("win32"), "PE inspection requires Windows (NASM + MinGW)")
    def test_pe_inspection_proves_x86_64_without_python_import(self) -> None:
        with tempfile.TemporaryDirectory(prefix="piton-evidence-") as directory:
            executable = compile_native('imprimir("evidence")\n', Path(directory) / "program.exe")
            inspection = inspect_windows_pe(executable)
            self.assertTrue(inspection["x86_64"])
            self.assertEqual(inspection["machine"], "0x8664")
            self.assertEqual(inspection["python_imports"], [])
            self.assertFalse(inspection["python_marker_in_image"])
            self.assertTrue(inspection["imports"])

    @unittest.skipIf(not sys.platform.startswith("win32"), "PE backend requires Windows (NASM + MinGW)")
    def test_cli_writes_passing_native_subset_receipt(self) -> None:
        with tempfile.TemporaryDirectory(prefix="piton-evidence-") as directory:
            root = Path(directory)
            executable = root / "hola.exe"
            report = root / "receipt.json"
            completed = subprocess.run(
                [
                    sys.executable, "-m", "piton", "compilar",
                    str(ROOT / "examples" / "01_hola.piton"),
                    "--backend=x86", "--output", str(executable),
                    "--evidencia", str(report),
                ],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("PITON_NATIVE_SUBSET_1_0 = PASS", completed.stdout)
            receipt = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(receipt["native_subset_1_0"], "PASS")
            self.assertEqual(receipt["windows_clean_machine_execution"], "NOT_DEMONSTRATED")
            self.assertTrue(all(state == "PASS" for state in receipt["gates"].values()))
            verified = load_windows_evidence(report)
            self.assertEqual(verified.receipt, receipt)
            self.assertTrue(build_dashboard(verified)["native_subset_ready"])

            executable.write_bytes(executable.read_bytes() + b"tampered")
            with self.assertRaisesRegex(NativeBuildError, "SHA-256 mismatch"):
                load_windows_evidence(report)

    def test_inspection_rejects_non_pe_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="piton-evidence-") as directory:
            path = Path(directory) / "not-pe"
            path.write_bytes(b"not a PE")
            with self.assertRaisesRegex(NativeBuildError, "not a PE"):
                inspect_windows_pe(path)


class NasmEmitterFloatEmissionTests(unittest.TestCase):
    """Runs on every host: this is assembler text, not a linked binary.

    NASM has no literal for inf/nan, so emitting __float64__(inf) aborts the
    whole build with `error: expecting floating-point number` before anything
    can be linked -- a fail-closed, but a fail that blocks legitimate programs
    such as `imprimir(1e309)`.
    """

    def test_finite_float_keeps_the_roundtrip_literal(self) -> None:
        # repr() is shortest round-trip and NASM parses it back bit-identical
        # (verified 19/19, subnormals included), so finite floats stay as-is.
        emitter = Win64NasmEmitter()
        emitter._load_operand(1e308, "rax")
        self.assertEqual(emitter.lines, ["    mov rax, __float64__(1e+308)"])

    def test_non_finite_float_is_emitted_as_ieee754_bits(self) -> None:
        for value, bits in (
            (float("inf"), 0x7FF0000000000000),
            (float("-inf"), 0xFFF0000000000000),
        ):
            with self.subTest(value=value):
                emitter = Win64NasmEmitter()
                emitter._load_operand(value, "rax")
                self.assertEqual(emitter.lines, [f"    mov rax, 0x{bits:016X}"])

        emitter = Win64NasmEmitter()
        emitter._load_operand(float("nan"), "rax")
        self.assertNotIn("__float64__", emitter.lines[0])
        self.assertRegex(emitter.lines[0], r"^    mov rax, 0x[0-9A-F]{16}$")


if __name__ == "__main__":
    unittest.main()
