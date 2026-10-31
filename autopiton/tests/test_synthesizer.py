"""Synthesizer controls: written by rule, measured twice, never in-tree.

The baseline-vs-overlay measurement needs the sandbox; the unit tests here pin
the deterministic parts (candidate, mutation, case grammar) and prove that
``piton/`` is never written. The one integration test checks that the overlay
really reaches the compiler and that the run attributes it.
"""

import tempfile
import unittest
from pathlib import Path

from autopiton.generator import ProgramFamily
from autopiton.sandbox import sandbox_available
from autopiton.synthesizer import (
    ModuleCaseGenerator,
    ModuleSynthesizer,
    flip_first_comparison,
    module_functions,
    save_candidate,
    synthesize_module,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

SAMPLE_MODULE = '''"""A module with a docstring mentioning a <= b, which is prose."""

def pick(items):
    if len(items) > 2:
        return items[0]
    return None

def clamp(x, lo=0, hi=10):
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x

def _hidden(y):
    if y == 1:
        return y
    return 0
'''


class MaterializedStdlib:
    """A throwaway stdlib directory with one known module."""

    def __init__(self, name="sample", source=SAMPLE_MODULE):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        (self.dir / f"{name}.py").write_text(source, encoding="utf-8")

    def __enter__(self):
        return self.dir

    def __exit__(self, *exc):
        self._tmp.cleanup()


class CandidateTests(unittest.TestCase):
    def test_candidate_is_deterministic(self):
        first = synthesize_module("colorsys")
        second = synthesize_module("colorsys")
        self.assertEqual(first.piton_sha256, second.piton_sha256)
        self.assertEqual(first.piton_source, second.piton_source)
        payload = first.to_dict()
        self.assertEqual(payload["piton_sha256"], first.piton_sha256)
        self.assertEqual(payload["reference_sha256"], first.reference_sha256)

    def test_mutation_flips_one_comparison_per_public_function(self):
        with MaterializedStdlib() as stdlib:
            plain = synthesize_module("sample", stdlib_dir=stdlib)
            mutated = synthesize_module("sample", stdlib_dir=stdlib, mutate=True)
        self.assertNotEqual(plain.piton_sha256, mutated.piton_sha256)
        # pick() has a comparison, clamp() has two, _hidden is not public.
        self.assertIn("pick line", mutated.mutation)
        self.assertIn("clamp line", mutated.mutation)
        self.assertNotIn("_hidden", mutated.mutation)

    def test_mutation_never_touches_docstrings(self):
        with MaterializedStdlib() as stdlib:
            plain = synthesize_module("sample", stdlib_dir=stdlib)
            mutated = synthesize_module("sample", stdlib_dir=stdlib, mutate=True)
        self.assertIn("a <= b", plain.piton_source)
        self.assertIn("a <= b", mutated.piton_source)

    def test_flip_requires_a_comparison(self):
        with self.assertRaises(ValueError):
            flip_first_comparison("funcion f ( ):\n    devolver 1\n")

    def test_flip_reports_missing_function(self):
        with self.assertRaises(ValueError):
            flip_first_comparison("funcion f (x ):\n    si x <1 :\n        devolver 1\n",
                                  function="nope")

    def test_saving_a_candidate_stays_outside_the_compiler_tree(self):
        with MaterializedStdlib() as stdlib, tempfile.TemporaryDirectory() as target:
            candidate = synthesize_module("sample", stdlib_dir=stdlib)
            path = save_candidate(candidate, Path(target))
            self.assertTrue(path.is_file())
            self.assertEqual(path.parent, Path(target))
            self.assertEqual(path.read_text(encoding="utf-8"), candidate.piton_source)


class CaseGrammarTests(unittest.TestCase):
    def test_arity_enumeration_from_ast(self):
        functions = {fn.name: fn for fn in module_functions(SAMPLE_MODULE)}
        self.assertEqual(set(functions), {"pick", "clamp"})
        self.assertEqual(functions["pick"].required, 1)
        self.assertEqual(functions["pick"].optional, 0)
        self.assertEqual(functions["clamp"].required, 1)
        self.assertEqual(functions["clamp"].optional, 2)

    def test_case_generation_is_deterministic(self):
        with MaterializedStdlib() as stdlib:
            generator = ModuleCaseGenerator("sample", seed=7, stdlib_dir=stdlib)
            first = [program.code for program in generator.generate(6)]
            second = [
                program.code
                for program in ModuleCaseGenerator("sample", seed=7, stdlib_dir=stdlib).generate(6)
            ]
        self.assertEqual(first, second)
        # The bounded grammar repeats values on purpose; what must hold is
        # that every public function of the module gets exercised.
        self.assertTrue(any("pick(" in code for code in first))
        self.assertTrue(any("clamp(" in code for code in first))

    def test_cases_use_the_module_family_and_compile(self):
        with MaterializedStdlib() as stdlib:
            programs = ModuleCaseGenerator("sample", seed=7, stdlib_dir=stdlib).generate(4)
        self.assertTrue(programs)
        for program in programs:
            self.assertIs(program.family, ProgramFamily.MODULE)
            self.assertTrue(program.code.startswith("import sample"))
            compile(program.code, "<test>", "exec")

    def test_sequence_module_binds_a_list_to_a_variable(self):
        with MaterializedStdlib() as stdlib:
            programs = ModuleCaseGenerator("sample", seed=7, stdlib_dir=stdlib).generate(4)
        self.assertTrue(any("_l0 = [" in program.code for program in programs))


class NoWritesIntoCompilerTreeTests(unittest.TestCase):
    def snapshot(self):
        return {
            str(path): path.stat().st_mtime_ns
            for path in sorted((REPO_ROOT / "piton").rglob("*"))
            if path.is_file()
        }

    def test_synthesis_and_saving_leave_piton_untouched(self):
        before = self.snapshot()
        with MaterializedStdlib() as stdlib, tempfile.TemporaryDirectory() as target:
            candidate = synthesize_module("sample", stdlib_dir=stdlib, mutate=True)
            save_candidate(candidate, Path(target))
        self.assertEqual(before, self.snapshot())


@unittest.skipUnless(sandbox_available(), "bubblewrap is not available")
class SynthesisIntegration(unittest.TestCase):
    """The overlay must reach the compiler and the run must attribute it."""

    def test_colorsys_candidate_is_registered_and_measured(self):
        summary = ModuleSynthesizer(build_timeout=90).run("colorsys", count=2, seed=7)
        self.assertTrue(summary.sandbox_available)
        self.assertEqual(summary.programs, 2)
        self.assertEqual(sum(summary.baseline.values()), 2)
        self.assertEqual(sum(summary.with_overlay.values()), 2)
        if summary.baseline_equiv:
            self.skipTest("PITON already resolves colorsys without the candidate")
        # Every executed overlay case must carry the sha256 of the candidate,
        # which is the proof the compiler saw *this* source and nothing else.
        sha = summary.candidate["piton_sha256"]
        executed = [case for case in summary.cases if case.get("overlay_sha256")]
        self.assertTrue(executed, "the candidate never reached the compiler")
        for case in executed:
            self.assertEqual(case["overlay_sha256"], sha)


if __name__ == "__main__":
    unittest.main()
