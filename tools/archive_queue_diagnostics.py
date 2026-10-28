"""Read-only diagnostics for archive manifest queue blockers.

This tool never downloads media, changes state, or dispatches publishing jobs.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path


QUALITY_RE = re.compile(r"\s*\(?((?:1080|720|480)p)\)?\s*$", re.I)
EPISODE_RE = re.compile(r"^(.*?)\s+-\s+(\d{1,4}(?:\.\d+)?)(?=\s*(?:\(|\[|$))", re.I)


def episode_key(row: dict) -> tuple[str, int, int] | None:
    title = str(row.get("series_title") or "").strip()
    season = row.get("season", 1)
    raw_title = str(row.get("title") or "").strip()
    match = EPISODE_RE.match(raw_title)
    if not title and match:
        title = match.group(1).strip(" -–—")
    if not title:
        title = raw_title
    try:
        episode = int(row.get("episode") or (match.group(2) if match else 0))
        season = int(season or 1)
    except (TypeError, ValueError):
        return None
    if not title or episode < 1 or season < 1:
        return None
    return (re.sub(r"\s+", " ", title).casefold(), season, episode)


def quality(row: dict) -> str | None:
    explicit = str(row.get("quality") or "").strip().lower()
    if explicit in {"1080p", "720p", "480p"}:
        return explicit
    match = QUALITY_RE.search(str(row.get("title") or ""))
    return match.group(1).lower() if match else None


def build_report(manifest: list, state: dict) -> dict:
    groups: dict[tuple[str, int, int], list[dict]] = defaultdict(list)
    invalid_rows = 0
    for row in manifest:
        if not isinstance(row, dict) or episode_key(row) is None:
            invalid_rows += 1
            continue
        groups[episode_key(row)].append(row)

    published = state.get("published", {}) if isinstance(state, dict) else {}
    if not isinstance(published, dict):
        published = {}
    published_by_title: dict[str, set[str]] = defaultdict(set)
    for record in published.values():
        if not isinstance(record, dict):
            continue
        title = re.sub(r"\s+", " ", str(record.get("title") or "").strip()).casefold()
        q = str(record.get("quality") or "").strip().lower()
        if title and q in {"1080p", "720p", "480p"}:
            published_by_title[title].add(q)

    episodes = []
    duplicate_groups = 0
    missing_quality_groups = 0
    for (series, season, number), rows in sorted(groups.items()):
        qualities = [quality(row) for row in rows]
        present = {q for q in qualities if q}
        duplicate_quality = len([q for q in qualities if q]) != len(present)
        if len(rows) > 1 and duplicate_quality:
            duplicate_groups += 1
        missing = sorted({"1080p", "720p", "480p"} - present)
        if missing:
            missing_quality_groups += 1
        episodes.append({
            "series": series,
            "season": season,
            "episode": number,
            "manifest_rows": len(rows),
            "qualities_found": sorted(present, key=lambda q: {"1080p": 0, "720p": 1, "480p": 2}[q]),
            "missing_qualities": missing,
            "duplicate_quality_rows": duplicate_quality,
        })

    return {
        "mode": "read_only_diagnostics",
        "manifest_rows": len(manifest),
        "valid_episode_groups": len(groups),
        "invalid_rows": invalid_rows,
        "groups_with_missing_qualities": missing_quality_groups,
        "groups_with_duplicate_quality_rows": duplicate_groups,
        "published_state_records": len(published),
        "episodes": episodes,
        "notes": [
            "This report does not dispatch workers or change publication state.",
            "Published state is summarized separately because its records may not share manifest URI identities.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="data/archive_manifest.json")
    parser.add_argument("--state", default="data/state.json")
    parser.add_argument("--output", default="-")
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    state = json.loads(Path(args.state).read_text(encoding="utf-8"))
    report = build_report(manifest, state)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output == "-":
        print(payload, end="")
    else:
        Path(args.output).write_text(payload, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
