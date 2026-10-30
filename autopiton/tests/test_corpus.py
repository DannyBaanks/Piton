"""Corpus persistence tests: dedup, signatures, JSONL runs, replay history."""

import json
import tempfile
import unittest
from pathlib import Path

from autopiton.comparator import Classification, ComparisonResult
from autopiton.corpus import CorpusManager, divergence_signature
from autopiton.cpython_oracle import CPythonResult
from autopiton.piton_runner import PitonResult, PitonStage


def fake_comparison(source, classification, *, cpython_type=None,
                    piton_stage=None, piton_executed=False):
    cpython = CPythonResult(source_hash="synthetic")
    if cpython_type:
        cpython.status = "exception"
        cpython.exit_code = 1
        cpython.exception_type = cpython_type
    else:
        cpython.status = "ok"
        cpython.exit_code = 0
    piton = PitonResult(source_code=source)
    if piton_executed:
        piton.executed = True
        piton.stage_reached = PitonStage.EXECUTE
        piton.exit_code = 0
        piton.stdout = ""
    if piton_stage is not None:
        piton.failed_stage = piton_stage
        piton.stage_reached = PitonStage.PARSE
    return ComparisonResult(
        source_code=source,
        cpython=cpython,
        piton=piton,
        classification=classification,
        reason="synthetic",
    )


class TestCorpusManager(CorpusManager):
    """Avoid the bwrap probe and git calls in unit tests."""

    def environment(self, extra=None):
        info = {
            "cpython_version": "test",
            "python_executable": "test",
            "platform": "test",
            "sandbox_backend": "bwrap",
            "bwrap": "/usr/bin/bwrap",
            "piton": {"repo": None, "commit": "test", "dirty": False},
        }
        if extra:
            info.update(extra)
        return info


class CorpusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.manager = TestCorpusManager(root=Path(self.tmp.name) / "autopiton")

    def tearDown(self):
        self.tmp.cleanup()

    def register(self, comparison, run_id="run_test", **kwargs):
        return self.manager.register(
            comparison,
            family=kwargs.pop("family", "test"),
            seed=kwargs.pop("seed", 0),
            description=kwargs.pop("description", ""),
            run_id=run_id,
            **kwargs,
        )

    def test_source_dedup_and_run_history(self):
        comparison = fake_comparison("print(1)\n", Classification.EQUIV, piton_executed=True)
        first = self.register(comparison, run_id="run_a")
        second = self.register(comparison, run_id="run_b")
        self.assertEqual(first, second)
        index = self.manager.load_index()
        self.assertEqual(len(index["cases"]), 1)
        self.assertEqual(index["cases"][first]["run_ids"], ["run_a", "run_b"])

    def test_signature_dedupes_unique_divergences(self):
        one = fake_comparison("print(zip(1, 2, 3))\n", Classification.UNSUPPORTED,
                              piton_stage=PitonStage.MIR)
        two = fake_comparison("print({**a})\n", Classification.UNSUPPORTED,
                              piton_stage=PitonStage.MIR)
        accepted = fake_comparison("A(1)\n", Classification.INVALID_ACCEPTED,
                                   cpython_type="TypeError", piton_executed=True)
        self.register(one, run_id="run_a")
        self.register(two, run_id="run_a")
        self.register(accepted, run_id="run_a")
        signatures = self.manager.load_index()["signatures"]
        self.assertEqual(len(signatures), 2)
        unsupported = [key for key in signatures if key.startswith("UNSUPPORTED")]
        self.assertEqual(len(unsupported), 1)
        self.assertEqual(len(signatures[unsupported[0]]), 2)

    def test_signature_contract(self):
        equiv = fake_comparison("print(1)\n", Classification.EQUIV, piton_executed=True)
        invalid = fake_comparison("raise\n", Classification.INVALID_REFERENCE,
                                  cpython_type="RuntimeError", piton_stage=PitonStage.MIR)
        unsupported = fake_comparison("x\n", Classification.UNSUPPORTED,
                                      piton_stage=PitonStage.MIR)
        self.assertIsNone(divergence_signature(equiv))
        self.assertIsNone(divergence_signature(invalid))
        self.assertIn("UNSUPPORTED", divergence_signature(unsupported))
        self.assertIn("MIR", divergence_signature(unsupported))

    def test_stdout_divergences_group_by_value_kind(self):
        def divergent(source, cpython_stdout, piton_stdout):
            comparison = fake_comparison(source, Classification.DIVERGENT,
                                         piton_executed=True)
            comparison.details = {
                "cpython_stdout": cpython_stdout,
                "piton_stdout": piton_stdout,
            }
            return comparison

        float_a = divergent("print(9 / 2)\n", "4.5\n", "4616752568008179712\n")
        float_b = divergent("print(-0.0)\n", "-0.0\n", "-9223372036854775808\n")
        bool_a = divergent("print(1 == 1)\n", "True\n", "1\n")
        bool_b = divergent("print(1 == 2)\n", "False\n", "0\n")

        float_sig = divergence_signature(float_a)
        self.assertIn("stdout:float->int", float_sig)
        self.assertEqual(float_sig, divergence_signature(float_b))
        bool_sig = divergence_signature(bool_a)
        self.assertIn("stdout:bool->int", bool_sig)
        self.assertNotEqual(float_sig, bool_sig)

        for case in (float_a, float_b, bool_a, bool_b):
            self.register(case)
        signatures = self.manager.load_index()["signatures"]
        self.assertEqual(len(signatures), 2)
        for bucket in signatures.values():
            self.assertEqual(len(bucket), 2)

    def test_stale_signature_bucket_is_migrated_on_reregistration(self):
        comparison = fake_comparison("print(9 / 2)\n", Classification.DIVERGENT,
                                     piton_executed=True)
        comparison.details = {
            "cpython_stdout": "4.5\n",
            "piton_stdout": "4616752568008179712\n",
        }
        current = divergence_signature(comparison)
        case_id = self.register(comparison, run_id="run_a")

        index = self.manager.load_index()
        stale = "DIVERGENT|cpython:ok:-|piton:EXECUTE:-|stdout"
        index["cases"][case_id]["signature"] = stale
        index["signatures"] = {stale: [case_id]}
        self.manager.save_index(index)

        self.register(comparison, run_id="run_b")
        index = self.manager.load_index()
        self.assertEqual(index["cases"][case_id]["signature"], current)
        self.assertNotIn(stale, index["signatures"])
        self.assertEqual(index["signatures"][current], [case_id])

    def test_case_artifacts_and_loader(self):
        source = "print(2)\n"
        comparison = fake_comparison(source, Classification.EQUIV, piton_executed=True)
        case_id = self.register(comparison)
        payload_path = self.manager.case_path(case_id)
        source_path = self.manager.cases_dir / f"{case_id}.py"
        self.assertTrue(payload_path.is_file())
        self.assertTrue(source_path.is_file())
        self.assertEqual(source_path.read_text(encoding="utf-8"), source + "\n")
        by_id = self.manager.load_case(case_id)
        by_path = self.manager.load_case(str(payload_path))
        self.assertEqual(by_id["source"], source)
        self.assertEqual(by_path["result"]["classification"], "EQUIV")

    def test_nondeterminism_is_flagged(self):
        source = "print(1)\n"
        first = fake_comparison(source, Classification.EQUIV, piton_executed=True)
        second = fake_comparison(source, Classification.DIVERGENT, piton_executed=True)
        case_id = self.register(first, run_id="run_a")
        self.register(second, run_id="run_b")
        entry = self.manager.load_index()["cases"][case_id]
        self.assertTrue(entry["nondeterministic"])
        self.assertEqual(set(entry["classifications"]), {"EQUIV", "DIVERGENT"})

    def test_run_writer_and_summary(self):
        writer = self.manager.create_run(mode="test", seed=42, config={"count": 2})
        writer.append({"index": 1, "classification": "EQUIV"})
        writer.append({"index": 2, "classification": "UNSUPPORTED"})
        summary = writer.finalize({
            "classification_counts": {"EQUIV": 1, "UNSUPPORTED": 1},
            "programs_requested": 2,
            "programs_executed": 2,
            "harness_errors": 0,
            "unique_divergences": 1,
            "unique_signatures": ["UNSUPPORTED|..."],
            "cases_registered": 2,
        })
        run = self.manager.load_run(writer.run_id)
        self.assertEqual(len(run["records"]), 2)
        self.assertEqual(run["meta"]["seed"], 42)
        self.assertEqual(run["summary"]["programs_executed"], 2)
        self.assertEqual(summary["duration_ms"] >= 0, True)
        self.assertTrue((Path(run["path"]) / "results.jsonl").is_file())

    def test_replay_history_and_minimization_record(self):
        comparison = fake_comparison("A(1)\n", Classification.INVALID_ACCEPTED,
                                     cpython_type="TypeError", piton_executed=True)
        case_id = self.register(comparison)
        self.manager.record_replay(
            case_id, "run_replay", comparison, command="replay " + case_id
        )
        path = self.manager.record_minimization(case_id, {
            "case_id": case_id,
            "minimized_case_id": "case-min",
            "attempts": 3,
            "accepted": 1,
            "stopped_reason": "done",
        })
        entry = self.manager.load_index()["cases"][case_id]
        self.assertEqual(len(entry["replay_history"]), 1)
        self.assertEqual(entry["replay_history"][0]["classification"], "INVALID_ACCEPTED")
        self.assertEqual(entry["minimization"]["minimized_case_id"], "case-min")
        self.assertTrue(path.is_file())
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["attempts"], 3)


if __name__ == "__main__":
    unittest.main()
