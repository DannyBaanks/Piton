from __future__ import annotations

import unittest

from piton.final_dashboard import build_dashboard, render_markdown


class Phase14Dashboard(unittest.TestCase):
    def test_dashboard_is_complete_and_fail_closed(self):
        dashboard = build_dashboard()
        names = {feature["name"] for feature in dashboard["features"]}
        self.assertEqual(len(names), 18)
        self.assertIn("dynamic_code", names)
        self.assertIn("dynamic_runtime", names)
        self.assertIn("ffi", names)
        self.assertFalse(dashboard["release_ready"])
        self.assertFalse(dashboard["full_parity"])
        self.assertFalse(dashboard["native_subset_ready"])

    def test_dashboard_has_no_hidden_native_claims(self):
        dashboard = build_dashboard()
        states = {gate["name"]: gate["state"] for gate in dashboard["gates"]}
        self.assertEqual(states["NATIVE_RUNTIME"], "PASS")
        self.assertEqual(states["DYNAMIC_RUNTIME_V1"], "PASS")
        self.assertEqual(states["CLEAN_MACHINE_EXECUTION"], "PASS")
        markdown = render_markdown(dashboard)
        self.assertIn("NOT_DEMONSTRATED", markdown)
        self.assertIn("NATIVE_OBJECT_PROTOCOL", markdown)
        self.assertEqual(states["FULL_PARITY"], "PARTIAL")

    def test_native_subset_milestone_requires_a_passing_receipt(self):
        from piton.native_evidence import EVIDENCE_GATES
        receipt = {
            "schema": "piton-native-subset-evidence-v1",
            "native_subset_1_0": "PASS",
            "gates": {gate: "PASS" for gate in EVIDENCE_GATES},
        }
        with self.assertRaisesRegex(TypeError, "load_windows_evidence"):
            build_dashboard(receipt)
        receipt["gates"].pop(next(iter(EVIDENCE_GATES)))
        with self.assertRaisesRegex(TypeError, "load_windows_evidence"):
            build_dashboard(receipt)


if __name__ == "__main__":
    unittest.main()
