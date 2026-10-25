"""Fail-closed validation for per-entry redistribution rights evidence.

A boolean flag alone is not a durable rights record. Every item entering a media
download/publication path must also carry a reference to the permission/license
record and the date that record was reviewed.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date
import re
from urllib.parse import urlparse


_PLACEHOLDER_EVIDENCE = {
    "n/a",
    "na",
    "none",
    "no",
    "yes",
    "true",
    "false",
    "approved",
    "licensed",
    "verified",
}
_RECORD_REFERENCE_PREFIXES = (
    "license-record:",
    "rights-record:",
    "contract-record:",
    "permission-record:",
)


def _is_evidence_reference(value: str) -> bool:
    """Accept an HTTPS document reference or a clearly typed durable record ID."""
    if not value or value.casefold() in _PLACEHOLDER_EVIDENCE:
        return False
    if value.casefold().startswith("https://"):
        parsed = urlparse(value)
        return bool(
            parsed.scheme == "https"
            and parsed.hostname
            and not parsed.username
            and not parsed.password
        )
    for prefix in _RECORD_REFERENCE_PREFIXES:
        if value.casefold().startswith(prefix):
            identifier = value[len(prefix):].strip()
            return bool(identifier and len(identifier) >= 3 and not any(ch.isspace() for ch in identifier))
    return False


def has_redistribution_authorization(
    entry: object,
    *,
    today: date | None = None,
) -> bool:
    """Return True only for an explicit, evidence-backed rights-review record.

    authorization_evidence should point to a written license, rights-holder
    permission, contract, or an auditable internal record. This function validates
    the presence and date of that record; it cannot independently determine legal
    rights from a URL or a flag.
    """
    if not isinstance(entry, Mapping):
        return False
    if entry.get("authorized_for_redistribution") is not True:
        return False

    evidence = str(entry.get("authorization_evidence") or "").strip()
    if not _is_evidence_reference(evidence):
        return False

    raw_review_date = str(entry.get("authorization_reviewed_at") or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_review_date):
        return False
    try:
        reviewed_date = date.fromisoformat(raw_review_date)
    except (TypeError, ValueError):
        return False

    current_date = today or date.today()
    return reviewed_date <= current_date
