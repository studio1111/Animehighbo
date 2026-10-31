"""Regression tests for bounded archive/RSS monitor recovery dispatch."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ArchiveMonitorDispatchTests(unittest.TestCase):
    def test_catalog_passes_recovery_permission_only_for_non_dispatch_runs(self):
        workflow = (ROOT / ".github/workflows/build-archive-catalog.yml").read_text(encoding="utf-8")
        self.assertIn(
            '--field "allow_rebuild=${{ github.event_name != \'workflow_dispatch\' }}"',
            workflow,
        )

    def test_monitor_has_explicit_opt_in_for_dispatch_recovery(self):
        workflow = (ROOT / ".github/workflows/subsplease-release-monitor.yml").read_text(encoding="utf-8")
        self.assertIn("allow_rebuild:", workflow)
        self.assertIn("default: false", workflow)
        self.assertIn("type: boolean", workflow)
        self.assertIn(
            "if: github.event_name != 'workflow_dispatch' || inputs.allow_rebuild",
            workflow,
        )

    def test_catalog_recovery_is_bounded_after_the_followup_scan(self):
        catalog = (ROOT / ".github/workflows/build-archive-catalog.yml").read_text(encoding="utf-8")
        monitor = (ROOT / ".github/workflows/subsplease-release-monitor.yml").read_text(encoding="utf-8")
        self.assertIn("schedule:", catalog)
        self.assertIn("cron: '*/30 * * * *'", catalog)
        self.assertIn("missing_from_manifest", monitor)
        self.assertIn("allow_rebuild", catalog)
        self.assertIn("default: false", monitor)


if __name__ == "__main__":
    unittest.main()
