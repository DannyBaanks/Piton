"""Generator tests: determinism, validity and mandatory canaries."""

import hashlib
import unittest

from autopiton.generator import ProgramFamily, ProgramGenerator


def digest(programs):
    payload = "\n---\n".join(program.code for program in programs)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class GeneratorTests(unittest.TestCase):
    def test_same_seed_same_programs(self):
        first = ProgramGenerator(seed=42).generate(count=100)
        second = ProgramGenerator(seed=42).generate(count=100)
        self.assertEqual(len(first), 100)
        self.assertEqual(digest(first), digest(second))

    def test_different_seed_changes_programs(self):
        first = ProgramGenerator(seed=42).generate(count=50)
        second = ProgramGenerator(seed=43).generate(count=50)
        self.assertNotEqual(digest(first), digest(second))

    def test_generated_programs_compile_and_keep_metadata(self):
        programs = ProgramGenerator(seed=7).generate(count=100)
        for program in programs:
            compile(program.code, "<generated>", "exec")
            self.assertTrue(program.is_valid, program.description)
            self.assertTrue(program.code.strip())
            self.assertTrue(program.code_hash)
            self.assertGreaterEqual(program.complexity, 1)

    def test_all_families_are_reachable(self):
        programs = ProgramGenerator(seed=42).generate(count=200)
        families = {program.family for program in programs}
        self.assertEqual(families, set(ProgramFamily) - {ProgramFamily.SYSTEMATIC})

    def test_systematic_corpus_is_deterministic_and_complete(self):
        from autopiton.generator import SystematicGrid

        generator = ProgramGenerator(seed=7)
        cells = SystematicGrid.cells()
        programs = generator.systematic_corpus()
        self.assertEqual(len(programs), len(cells) * len(SystematicGrid.SURFACES))
        self.assertEqual(
            [p.description for p in programs],
            [p.description for p in ProgramGenerator(seed=999).systematic_corpus()],
        )
        self.assertEqual(
            {p.family for p in programs}, {ProgramFamily.SYSTEMATIC}
        )
        surfaces_seen = {p.description.split("/")[1].split("]")[0] for p in programs}
        self.assertEqual(surfaces_seen, set(SystematicGrid.SURFACES))
        for program in programs:
            compile(program.code, "<systematic>", "exec")
            self.assertTrue(program.is_valid)
            self.assertIn("print(", program.code)

    def test_generate_family_systematic_routes_to_grid(self):
        programs = ProgramGenerator(seed=1).generate(ProgramFamily.SYSTEMATIC, 5)
        self.assertEqual(len(programs), 5)
        self.assertTrue(all(p.family is ProgramFamily.SYSTEMATIC for p in programs))

    def test_family_generation_honours_the_requested_family(self):
        generator = ProgramGenerator(seed=3)
        programs = generator.generate(ProgramFamily.LOOPS, 10)
        self.assertEqual(len(programs), 10)
        self.assertTrue(all(program.family is ProgramFamily.LOOPS for program in programs))
        for program in programs:
            compile(program.code, "<generated>", "exec")

    def test_canaries_are_the_ten_mandatory_cases(self):
        canaries = ProgramGenerator().generate_canary_cases()
        self.assertEqual(len(canaries), 10)
        self.assertEqual(
            [canary.description.split(":")[0] for canary in canaries],
            [f"Canary {letter}" for letter in "ABCDEFGHIJ"],
        )
        for canary in canaries:
            compile(canary.code, "<canary>", "exec")
            self.assertTrue(canary.code.strip())
        self.assertEqual(
            sum(1 for canary in canaries if not canary.is_valid),
            4,
        )


if __name__ == "__main__":
    unittest.main()
