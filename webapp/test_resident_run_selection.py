from __future__ import annotations

import unittest

import server


class ResidentRunSelectionUiTest(unittest.TestCase):
    def test_recent_run_selection_drives_the_resident_package_panel(self) -> None:
        app_js = (server.STATIC_ROOT / "app.js").read_text(encoding="utf-8")

        self.assertIn("selectedScheduleRunId", app_js)
        self.assertIn("selectedResidentRun", app_js)
        self.assertIn("selectResidentRun", app_js)
        self.assertIn("data-select-resident-run", app_js)


if __name__ == "__main__":
    unittest.main()
