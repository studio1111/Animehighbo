"""Build a metadata-only, cross-source October episode coverage report.

Combines AniList seasonal title metadata with archive manifest, candidate and RSS
metadata. This report is for completeness review only; it never adds media sources,
grants redistribution rights, or dispatches publishing workers.
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

def normalize_series(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = re.sub(r"\([^)]*\b(?:1080|720|480)p\b[^)]*\)", " ", text)
    text = re.sub(r"\s+-\s+(?:episode\s*)?\d{1,4}(?:\.\d+)?\s*$", " ", text, flags=re.I)
    text = re.sub(r"\b(?:episode|ep)\.?\s*\d{1,4}\b", " ", text, flags=re.I)
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def _title_aliases(row: dict, *, seasonal: bool = False) -> set[str]:
    if seasonal:
        title = row.get("title") or {}
        values = [row.get("title_english"), row.get("title_romaji"), row.get("title_native"), title.get("english"), title.get("romaji"), title.get("native")]
        values.extend(row.get("synonyms") or [])
    else:
        values = [row.get("series_title"), row.get("title")]
    return {key for value in values if (key := normalize_series(value))}


def _in_window(row: dict, cutoff: str, today: str) -> bool:
    released = str(row.get("release_date") or "")[:10]
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", released) and cutoff <= released <= today)


def _episode_identity(row: dict) -> tuple[str, str]:
    key = normalize_series(row.get("series_title") or row.get("title"))
    episode = str(row.get("episode") or "").strip()
    return key, episode


def build_coverage_report(
    seasonal_report: dict,
    manifest: list[dict],
    candidates: list[dict],
    release_report: dict,
    today: date,
    cutoff: str = "2026-10-01",
) -> dict:
    today_text = today.isoformat()
    seasonal_rows = [
        row for row in seasonal_report.get("titles", [])
        if isinstance(row, dict)
        and row.get("season") == "FALL"
        and row.get("season_year") == 2026
    ]
    manifest_rows = [
        row for row in manifest if isinstance(row, dict) and _in_window(row, cutoff, today_text)
    ]
    candidate_rows = [
        row for row in candidates if isinstance(row, dict) and _in_window(row, cutoff, today_text)
    ]
    rss_rows = [
        row for row in release_report.get("releases", [])
        if isinstance(row, dict) and _in_window(row, cutoff, today_text)
    ]

    # Index source rows against every known seasonal alias. Quality variants are
    # deduplicated by normalized series + episode number, not raw row counts.
    source_groups = {
        "manifest": manifest_rows,
        "candidate_report": candidate_rows,
        "rss": rss_rows,
    }
    seasonal_entries = []
    seasonal_alias_index: dict[str, str] = {}
    for row in seasonal_rows:
        aliases = _title_aliases(row, seasonal=True)
        canonical = next(iter(sorted(aliases)), f"anilist:{row.get('anilist_id')}")
        for alias in aliases:
            seasonal_alias_index.setdefault(alias, canonical)
        seasonal_entries.append({
            "anilist_id": row.get("anilist_id"),
            "title_english": row.get("title_english"),
            "title_romaji": row.get("title_romaji"),
            "title_native": row.get("title_native"),
            "season": row.get("season"),
            "season_year": row.get("season_year"),
            "format": row.get("format"),
            "status": row.get("status"),
            "anilist_url": row.get("anilist_url"),
            "source_coverage": {},
        })

    # Build one row per title across AniList and all metadata sources, including
    # source-only titles not represented by the current seasonal AniList query.
    union: dict[str, dict] = {}
    for index, row in enumerate(seasonal_entries):
        aliases = _title_aliases(seasonal_rows[index], seasonal=True)
        canonical = next(iter(sorted(aliases)), f"anilist:{row.get('anilist_id')}")
        entry = {**row, "seasonal_title": True, "_aliases": aliases}
        union[canonical] = entry

    for source_name, rows in source_groups.items():
        for row in rows:
            aliases = _title_aliases(row)
            if not aliases:
                continue
            matched = next((seasonal_alias_index[alias] for alias in aliases if alias in seasonal_alias_index), None)
            if matched is not None:
                union[matched]["_aliases"].update(aliases)
            else:
                canonical = sorted(aliases)[0]
                if canonical not in union:
                    union[canonical] = {
                        "anilist_id": None,
                        "title_english": row.get("series_title") or row.get("title"),
                        "title_romaji": None,
                        "title_native": None,
                        "season": None,
                        "season_year": None,
                        "format": None,
                        "status": None,
                        "anilist_url": None,
                        "seasonal_title": False,
                        "source_coverage": {},
                        "_aliases": aliases,
                    }
                union[canonical]["_aliases"].update(aliases)

    for entry in union.values():
        aliases = entry.pop("_aliases")
        coverage = {}
        for source_name, rows in source_groups.items():
            matches = [row for row in rows if _title_aliases(row) & aliases]
            distinct = {}
            for row in matches:
                identity = _episode_identity(row)
                if identity[0] and identity[1]:
                    distinct.setdefault(identity, row)
            coverage[source_name] = {
                "distinct_episodes": len(distinct),
                "episodes": [
                    {
                        "episode": row.get("episode"),
                        "release_date": str(row.get("release_date") or "")[:10],
                        **({"quality": row.get("quality")} if row.get("quality") else {}),
                        **({"in_archive_manifest": row.get("in_archive_manifest") is True}
                           if source_name == "rss" else {}),
                    }
                    for _, row in sorted(distinct.items(), key=lambda item: (
                        str(item[1].get("release_date") or ""), str(item[1].get("episode") or "")
                    ))
                ],
            }
        entry["source_coverage"] = coverage
        # An RSS entry is not a missing episode merely because it exists: the
        # manifest is the queue source, and RSS rows already carry an explicit
        # in_archive_manifest comparison. Only unmatched RSS episodes require
        # reconciliation. AniList titles with no manifest entries are not proof
        # of a gap because seasonal metadata is not a complete airing schedule.
        unmatched_rss = [
            episode for episode in coverage["rss"]["episodes"]
            if episode.get("in_archive_manifest") is False
        ]
        coverage["rss"]["unmatched_episodes"] = [
            {key: episode.get(key) for key in ("episode", "release_date", "quality")}
            for episode in unmatched_rss
        ]
        entry["review_required"] = bool(unmatched_rss)
    titles = sorted(union.values(), key=lambda row: (
        not row.get("seasonal_title", False),
        (row.get("title_english") or row.get("title_romaji") or "").casefold(),
    ))
    seasonal_count = sum(1 for row in titles if row.get("seasonal_title"))
    return {
        "generated_on": today_text,
        "cutoff_date": cutoff,
        "through_date": today_text,
        "sources": {
            "seasonal_titles": "AniList seasonal discovery report",
            "manifest": "archive_manifest.json",
            "candidates": "not used; archive_manifest.json is the automatic queue source",
            "rss": "subsplease_release_report.json",
        },
        "metadata_only": True,
        "summary": {
            "titles_total": len(titles),
            "seasonal_titles": seasonal_count,
            "source_only_titles": len(titles) - seasonal_count,
            "titles_with_manifest_episodes": sum(1 for row in titles if row["source_coverage"]["manifest"]["distinct_episodes"]),
            "titles_with_candidate_episodes": sum(1 for row in titles if row["source_coverage"]["candidate_report"]["distinct_episodes"]),
            "titles_with_rss_episodes": sum(1 for row in titles if row["source_coverage"]["rss"]["distinct_episodes"]),
            "titles_with_unmatched_rss_episodes": sum(1 for row in titles if row["source_coverage"]["rss"]["unmatched_episodes"]),
            "distinct_manifest_episodes": sum(row["source_coverage"]["manifest"]["distinct_episodes"] for row in titles),
            "distinct_candidate_episodes": sum(row["source_coverage"]["candidate_report"]["distinct_episodes"] for row in titles),
            "distinct_rss_episodes": sum(row["source_coverage"]["rss"]["distinct_episodes"] for row in titles),
            "distinct_unmatched_rss_episodes": sum(len(row["source_coverage"]["rss"]["unmatched_episodes"]) for row in titles),
            "titles_needing_review": sum(1 for row in titles if row["review_required"]),
        },
        "limitations": [
            "AniList identifies seasonal titles, not a complete episode schedule or every episode aired in the date window.",
            "RSS is a partial release feed; absence from RSS does not prove an episode was not released.",
            "Title matching is normalized exact-alias matching; alternate spellings may remain unmatched.",
            "Raw rows are deduplicated by normalized title and episode number, so multiple quality encodes do not inflate episode counts.",
            "RSS is a partial release feed; episodes missing from the manifest need source reconciliation.",
            "Only Fall 2026 AniList titles are treated as seasonal October titles; ongoing shows found in source metadata are included as source-only titles.",
        ],
        "policy": "This is a coverage report only; it does not publish messages or modify the queue.",
        "titles": titles,
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    paths = {
        "seasonal": root / "data" / "seasonal_discovery_report.json",
        "manifest": root / "data" / "archive_manifest.json",
        "rss": root / "data" / "subsplease_release_report.json",
    }
    output = root / "data" / "october_episode_coverage_report.json"
    try:
        data = {key: json.loads(path.read_text(encoding="utf-8")) for key, path in paths.items()}
        if not isinstance(data["manifest"], list):
            raise ValueError("Archive manifest must be a JSON list")
        report = build_coverage_report(
            data["seasonal"], data["manifest"], [], data["rss"], date.today()
        )
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report["summary"], ensure_ascii=False))
        print(f"Wrote {output}")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Episode coverage report failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
