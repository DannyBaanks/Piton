from __future__ import annotations

import unittest

from piton.final_dashboard import build_dashboard, render_markdown


class Phase14Dashboard(unittest.TestCase):
    def test_dashboard_is_complete_and_fail_closed(self):
        dashboard = build_dashboard()
        names = {feature["name"] for feature in dashboard["features"]}
        self.assertEqual(len(names), 17)
        self.assertIn("dynamic_code", names)
        self.assertIn("ffi", names)
        self.assertFalse(dashboard["release_ready"])
        self.assertFalse(dashboard["full_parity"])

    def test_dashboard_has_no_hidden_native_claims(self):
        dashboard = build_dashboard()
        states = {gate["name"]: gate["state"] for gate in dashboard["gates"]}
        self.assertEqual(states["NATIVE_RUNTIME"], "PARTIAL")
        self.assertEqual(states["CLEAN_MACHINE_EXECUTION"], "PASS")
        markdown = render_markdown(dashboard)
        self.assertIn("NOT_DEMONSTRATED", markdown)
        self.assertIn("NATIVE_OBJECT_PROTOCOL", markdown)


if __name__ == "__main__":
    unittest.main()
