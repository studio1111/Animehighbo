import unittest
from datetime import date

from tools.redistribution_rights import has_redistribution_authorization


class RedistributionRightsTests(unittest.TestCase):
    def test_accepts_evidence_and_review_date_without_legacy_boolean(self):
        entry = {
            "authorization_evidence": "https://rights.example.org/licenses/animehighbo",
            "authorization_reviewed_at": "2026-10-24",
        }
        self.assertTrue(has_redistribution_authorization(entry, today=date(2026, 10, 25)))

    def test_legacy_boolean_without_evidence_is_rejected(self):
        entry = {
            "authorized_for_redistribution": True,
            "authorization_reviewed_at": "2026-10-24",
        }
        self.assertFalse(has_redistribution_authorization(entry, today=date(2026, 10, 25)))

    def test_legacy_boolean_does_not_override_missing_evidence(self):
        for flag in (True, False):
            with self.subTest(flag=flag):
                self.assertFalse(has_redistribution_authorization({
                    "authorized_for_redistribution": flag,
                    "authorization_reviewed_at": "2026-10-24",
                }, today=date(2026, 10, 25)))

    def test_rejects_untyped_or_malformed_evidence_references(self):
        for evidence in ("made-up", "approved", "http://rights.example.org/license",
                         "https://user:secret@rights.example.org/license"):
            with self.subTest(evidence=evidence):
                self.assertFalse(has_redistribution_authorization({
                    "authorization_evidence": evidence,
                    "authorization_reviewed_at": "2026-10-24",
                }, today=date(2026, 10, 25)))

    def test_accepts_a_typed_internal_rights_record_id(self):
        self.assertTrue(has_redistribution_authorization({
            "authorization_evidence": "rights-record:ABC123",
            "authorization_reviewed_at": "2026-10-24",
        }, today=date(2026, 10, 25)))

    def test_rejects_missing_or_invalid_review_date(self):
        for reviewed_at in ("", "yesterday", "2026-02-31"):
            with self.subTest(reviewed_at=reviewed_at):
                self.assertFalse(has_redistribution_authorization({
                    "authorization_evidence": "https://rights.example.org/license",
                    "authorization_reviewed_at": reviewed_at,
                }, today=date(2026, 10, 25)))

    def test_rejects_future_review_date(self):
        self.assertFalse(has_redistribution_authorization({
            "authorization_evidence": "https://rights.example.org/license",
            "authorization_reviewed_at": "2026-10-26",
        }, today=date(2026, 10, 25)))

    def test_rejects_non_object_entries(self):
        for entry in (None, [], "true", 42):
            with self.subTest(entry=entry):
                self.assertFalse(has_redistribution_authorization(entry, today=date(2026, 10, 25)))


if __name__ == "__main__":
    unittest.main()
