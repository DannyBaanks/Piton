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
        from autopiton.generator import NON_RANDOM_FAMILIES

        programs = ProgramGenerator(seed=42).generate(count=200)
        families = {program.family for program in programs}
        self.assertEqual(families, set(ProgramFamily) - set(NON_RANDOM_FAMILIES))

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

    def test_string_kinds_are_on_the_grid_and_compile(self):
        from autopiton.generator import SystematicGrid

        str_cells = [cell for cell in SystematicGrid.cells() if cell[0].endswith("str")]
        self.assertEqual({cell[0] for cell in str_cells}, {"arith_str", "compare_str"})
        # Concat needs two strings, repeat a string and an int: the operand
        # pair is part of the cell, so the grid cannot reuse one tuple for both.
        self.assertEqual(
            [(cell[1], cell[2]) for cell in str_cells if cell[0] == "arith_str"],
            [("+", SystematicGrid.STR_OPERANDS), ("*", SystematicGrid.STR_REPEAT_OPERANDS)],
        )

        programs = ProgramGenerator(seed=7).systematic_corpus()
        str_programs = [
            program
            for program in programs
            if program.description.split("[")[1].split("/")[0].endswith("str")
        ]
        self.assertEqual(len(str_programs), len(str_cells) * len(SystematicGrid.SURFACES))
        self.assertEqual(
            {p.description.split("/")[1].split("]")[0] for p in str_programs},
            set(SystematicGrid.SURFACES),
        )
        for program in str_programs:
            compile(program.code, "<str>", "exec")
            self.assertTrue(program.is_valid, program.description)

    def test_new_grid_cells_are_appended_so_truncation_stays_a_prefix(self):
        full = ProgramGenerator(seed=1).systematic_corpus()
        # 58 is the size of the grid as measured on 2026-10-30: checking the
        # prefix at that boundary is what keeps the old run replayable.
        for limit in (5, 20, 58, len(full)):
            truncated = ProgramGenerator(seed=1).systematic_corpus(limit=limit)
            self.assertEqual(
                [program.code for program in truncated],
                [program.code for program in full[:limit]],
            )

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

    def test_grid_prefix_is_frozen_at_74(self):
        """The 74 programs of the V0 grid must never change hash.

        Archived runs are replayed by truncating the grid, so if the prefix
        moves, an old run silently stops describing what it claims to.
        """
        from autopiton.generator import SystematicGrid

        programs = ProgramGenerator(seed=1).systematic_corpus(limit=74)
        self.assertEqual(len(programs), 74)
        self.assertEqual(
            digest(programs),
            "a77fabdc0f34e26dbe4cafdadf021d554db1ba04ceca82ad8f13bdec110ff9ab",
        )
        # 74 is the old grid exactly: 37 cells, each on both surfaces.
        self.assertEqual(len(SystematicGrid.cells()) * 2, len(
            ProgramGenerator(seed=1).systematic_corpus()
        ))

    def test_boundary_cells_are_appended_and_probe_four_shapes(self):
        """Every boundary kind is probed with int (control), float, bool, str.

        The V0 finding is that a bool/float *result* comes back as int when it
        crosses a callable boundary, while the same expression printed inline
        is EQUIV. Without the per-boundary int control, a boundary that breaks
        everything is indistinguishable from one that breaks nothing.
        """
        from autopiton.generator import SystematicGrid

        cells = SystematicGrid.cells()
        base = [cell for cell in cells if not cell[0].startswith("boundary_")]
        boundary = [cell for cell in cells if cell[0].startswith("boundary_")]
        # Appended, never inserted: the V0 prefix stays at the front.
        self.assertEqual(
            [cell for cell in cells[: len(base)]],
            base,
        )
        self.assertEqual(len(base), 37)
        self.assertEqual(len(boundary), len(SystematicGrid.BOUNDARY_KINDS) * 4)

        shapes = {
            "arith_int": SystematicGrid.INT_OPERANDS,
            "arith_float": SystematicGrid.FLOAT_OPERANDS,
            "compare_bool": SystematicGrid.BOOL_OPERANDS,
            "arith_str": SystematicGrid.STR_OPERANDS,
        }
        for kind in SystematicGrid.BOUNDARY_KINDS:
            per_kind = [cell for cell in boundary if cell[0] == kind]
            self.assertEqual(
                {(op, operands) for _name, op, operands in per_kind},
                {(op, operands) for _name, op, operands in SystematicGrid.BOUNDARY_SHAPES},
                kind,
            )
            self.assertEqual(len(per_kind), 4, kind)
        # The operand pairs are exactly the four value shapes.
        self.assertEqual(
            {tuple(operands) for _n, _op, operands in SystematicGrid.BOUNDARY_SHAPES},
            {tuple(v) for v in shapes.values()},
        )

    def test_boundary_programs_compile_and_bind_the_crossed_value(self):
        """Each boundary program compiles, binds ``_b``, and prints it."""
        from autopiton.generator import SystematicGrid

        programs = ProgramGenerator(seed=5).systematic_corpus()
        boundary_programs = [
            p for p in programs if p.description.split("[")[1].startswith("boundary_")
        ]
        self.assertTrue(boundary_programs)
        self.assertEqual(
            len(boundary_programs),
            len(SystematicGrid.BOUNDARY_KINDS) * 4 * len(SystematicGrid.SURFACES),
        )
        for program in boundary_programs:
            compile(program.code, "<boundary>", "exec")
            self.assertTrue(program.is_valid, program.description)
            self.assertIn("_b", program.code)
            self.assertIn("print(", program.code)

    def test_boundary_surface_only_wraps_the_crossed_value_in_a_lambda(self):
        """``direct`` and ``lambda`` differ by the outer lambda, nothing else.

        The outer layer asks whether the defect compounds across two crossings
        or is decided by the first one, so the prologue must be byte-identical
        between the two surfaces of a cell.
        """
        from autopiton.generator import SystematicGrid

        programs = ProgramGenerator(seed=5).systematic_corpus()
        by_key = {}
        for program in programs:
            kind, surface = program.description.split("[")[1].split("]")
            kind = kind.split("/")[0]
            surface = kind and program.description.split("/")[1].split("]")[0]
            key = program.description.split("] ")[1]
            kind_name = program.description.split("[")[1].split("/")[0]
            if not kind_name.startswith("boundary_"):
                continue
            by_key.setdefault((kind_name, key), {})[surface] = program

        for (kind_name, key), surfaces in by_key.items():
            self.assertEqual(set(surfaces), set(SystematicGrid.SURFACES), key)
            direct = surfaces["direct"].code
            wrapped = surfaces["lambda"].code
            self.assertTrue(direct.endswith("print(_b)\n"), direct)
            self.assertTrue(wrapped.endswith("_v = lambda: _b\nprint(_v())\n"), wrapped)
            # Identical up to the surface wrapper.
            self.assertEqual(
                direct[: -len("print(_b)\n")],
                wrapped[: -len("_v = lambda: _b\nprint(_v())\n")],
                kind_name,
            )

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
