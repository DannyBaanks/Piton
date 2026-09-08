from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest

from piton.final_dashboard import build_dashboard
from piton.native_evidence import inspect_windows_pe, load_windows_evidence
from piton.x86 import NativeBuildError, compile_native


ROOT = Path(__file__).resolve().parents[1]


class NativeSubsetEvidenceTests(unittest.TestCase):
    def test_pe_inspection_proves_x86_64_without_python_import(self) -> None:
        with tempfile.TemporaryDirectory(prefix="piton-evidence-") as directory:
            executable = compile_native('imprimir("evidence")\n', Path(directory) / "program.exe")
            inspection = inspect_windows_pe(executable)
            self.assertTrue(inspection["x86_64"])
            self.assertEqual(inspection["machine"], "0x8664")
            self.assertEqual(inspection["python_imports"], [])
            self.assertFalse(inspection["python_marker_in_image"])
            self.assertTrue(inspection["imports"])

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


if __name__ == "__main__":
    unittest.main()
