import unittest
from datetime import date

from tools.redistribution_rights import has_redistribution_authorization


class RedistributionRightsTests(unittest.TestCase):
    def test_accepts_an_explicit_rights_record_with_evidence_and_review_date(self):
        entry = {
            "authorized_for_redistribution": True,
            "authorization_evidence": "https://rights.example.org/licenses/animehighbo",
            "authorization_reviewed_at": "2026-10-24",
        }
        self.assertTrue(has_redistribution_authorization(entry, today=date(2026, 10, 25)))

    def test_rejects_legacy_boolean_without_evidence(self):
        entry = {
            "authorized_for_redistribution": True,
            "authorization_reviewed_at": "2026-10-24",
        }
        self.assertFalse(has_redistribution_authorization(entry, today=date(2026, 10, 25)))

    def test_rejects_missing_or_invalid_review_date(self):
        for reviewed_at in ("", "yesterday", "2026-02-31"):
            with self.subTest(reviewed_at=reviewed_at):
                self.assertFalse(has_redistribution_authorization({
                    "authorized_for_redistribution": True,
                    "authorization_evidence": "https://rights.example.org/license",
                    "authorization_reviewed_at": reviewed_at,
                }, today=date(2026, 10, 25)))

    def test_rejects_future_review_date(self):
        self.assertFalse(has_redistribution_authorization({
            "authorized_for_redistribution": True,
            "authorization_evidence": "https://rights.example.org/license",
            "authorization_reviewed_at": "2026-10-26",
        }, today=date(2026, 10, 25)))

    def test_rejects_false_flag_even_when_evidence_is_present(self):
        self.assertFalse(has_redistribution_authorization({
            "authorized_for_redistribution": False,
            "authorization_evidence": "https://rights.example.org/license",
            "authorization_reviewed_at": "2026-10-24",
        }, today=date(2026, 10, 25)))

    def test_rejects_non_object_entries(self):
        for entry in (None, [], "true", 42):
            with self.subTest(entry=entry):
                self.assertFalse(has_redistribution_authorization(entry, today=date(2026, 10, 25)))


if __name__ == "__main__":
    unittest.main()
