"""Fail-closed validation for per-entry redistribution rights evidence.

A boolean flag alone is not a durable rights record. Every item entering a media
download/publication path must also carry a reference to the permission/license
record and the date that record was reviewed.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date


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
    if not evidence or evidence.casefold() in _PLACEHOLDER_EVIDENCE:
        return False

    raw_review_date = str(entry.get("authorization_reviewed_at") or "").strip()
    try:
        reviewed_date = date.fromisoformat(raw_review_date)
    except (TypeError, ValueError):
        return False

    current_date = today or date.today()
    return reviewed_date <= current_date
