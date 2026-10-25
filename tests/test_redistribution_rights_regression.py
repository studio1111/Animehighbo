import unittest
from datetime import date

from tools.redistribution_rights import has_redistribution_authorization


class RedistributionRightsRegressionTests(unittest.TestCase):
    def test_missing_evidence_is_rejected_without_crashing(self):
        self.assertFalse(has_redistribution_authorization({}))

    def test_untyped_non_url_evidence_is_rejected_without_crashing(self):
        self.assertFalse(has_redistribution_authorization({
            "authorization_evidence": "permission confirmed",
            "authorization_reviewed_at": date.today().isoformat(),
        }))

    def test_invalid_review_date_is_rejected_without_crashing(self):
        self.assertFalse(has_redistribution_authorization({
            "authorization_evidence": "rights-record:animehighbo-global",
            "authorization_reviewed_at": "not-a-date",
        }))

    def test_valid_durable_record_reference_is_accepted(self):
        self.assertTrue(has_redistribution_authorization({
            "authorization_evidence": "rights-record:animehighbo-global",
            "authorization_reviewed_at": date.today().isoformat(),
        }))


if __name__ == "__main__":
    unittest.main()
