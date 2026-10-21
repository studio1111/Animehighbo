"""Select and gate authorized episodes without cross-series failure coupling."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from datetime import date


def _day(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Every archive entry must have a source release_date before processing.")
    try:
        parsed = date.fromisoformat(raw[:10])
        return parsed.isoformat()
    except (TypeError, ValueError):
        raise ValueError(f"Invalid source release_date: {raw!r}")


def episode_identity(entry: dict) -> str:
    """Return a stable identity based on an immutable source URI or explicit identity."""
    explicit = str(entry.get("identity") or "").strip()
    if explicit:
        return explicit
    uri = str(entry.get("uri") or entry.get("url") or "").strip()
    if not uri:
        raise ValueError("Episode identity requires a source URI or explicit identity.")
    return hashlib.sha256(("archive:" + uri).encode("utf-8")).hexdigest()


def series_identity(entry: dict) -> str | None:
    """Return a conservative series/season key; ambiguous identity is held."""
    title = str(entry.get("series_title") or "").strip()
    season = entry.get("season")
    if not title:
        raw_title = str(entry.get("title") or "").strip()
        # Only strip an explicit episode suffix; do not infer a series from arbitrary text.
        match = re.match(r"^(.*?)\s+-\s+(?:episode\s*)?\d{1,4}(?:\.\d+)?(?:\s|$|\()", raw_title, re.I)
        if match:
            title = match.group(1).strip(" -–—")
        else:
            return None
    if season in (None, ""):
        raw_title = str(entry.get("title") or "")
        season_match = re.search(r"(?i)\bS(\d{1,2})\s*E\d{1,4}\b", raw_title)
        if season_match:
            season = int(season_match.group(1))
        else:
            # Season one is accepted only when the manifest explicitly provides a clean
            # series title and episode number, rather than trying to parse a vague title.
            if not str(entry.get("series_title") or "").strip():
                return None
            season = 1
    try:
        season_number = int(season)
    except (TypeError, ValueError):
        return None
    if season_number < 1 or not title:
        return None
    normalized_title = re.sub(r"\s+", " ", title).casefold()
    return f"{normalized_title}::season-{season_number}"


def _is_complete(record: object) -> bool:
    return isinstance(record, dict) and {"1080p", "720p", "480p"}.issubset(
        set(record.get("complete_qualities", []))
    )


def eligible_prepared_episodes(entries: list[dict], state: dict) -> list[dict]:
    """Select prepared episodes whose earlier known episodes in the same season are complete.

    A failed or ambiguous episode is never published, but only blocks later episodes in
    its own series. Different series are evaluated independently.
    """
    if not isinstance(entries, list) or not entries:
        return []
    published = state.get("published", {}) if isinstance(state, dict) else {}
    if not isinstance(published, dict):
        published = {}

    parsed: list[tuple[dict, str, str, int]] = []
    counts: dict[tuple[str, int], int] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            number = int(entry.get("episode") or 0)
        except (TypeError, ValueError):
            continue
        series = series_identity(entry)
        if number < 1 or series is None:
            continue
        try:
            identity = episode_identity(entry)
        except ValueError:
            continue
        parsed.append((entry, identity, series, number))
        counts[(series, number)] = counts.get((series, number), 0) + 1

    eligible: list[dict] = []
    for entry, identity, series, number in parsed:
        status = str(entry.get("status") or "prepared").casefold()
        if status not in {"prepared", "ready", "complete"}:
            continue
        if _is_complete(published.get(identity)):
            continue
        # Duplicate entries for the same series/episode are ambiguous and held.
        if counts[(series, number)] != 1:
            continue
        blocked = False
        for earlier, earlier_identity, earlier_series, earlier_number in parsed:
            if earlier_series != series or earlier_number >= number:
                continue
            if not _is_complete(published.get(earlier_identity)):
                blocked = True
                break
        if not blocked:
            eligible.append(entry)
    return sorted(
        eligible,
        key=lambda entry: (
            (series_identity(entry) or "").casefold(),
            int(entry.get("episode") or 0),
            episode_identity(entry),
        ),
    )


def select_next_upload_date(
    manifest: list[dict], state: dict, cutoff_date: str | None = None
) -> tuple[str | None, list[dict]]:
    if not isinstance(manifest, list) or not manifest:
        return None, []
    cutoff = date.fromisoformat(cutoff_date) if cutoff_date else None
    published = state.get("published", {}) if isinstance(state, dict) else {}
    normalized = []
    for index, row in enumerate(manifest):
        if not isinstance(row, dict):
            raise ValueError(f"Archive entry {index + 1} must be an object.")
        uri = str(row.get("uri") or row.get("url") or "").strip()
        title = str(row.get("title") or "").strip()
        day = _day(row.get("release_date"))
        if not uri or not title or len(day) != 10:
            raise ValueError(f"Archive entry {index + 1} requires title, URI and ISO release_date.")
        try:
            episode_number = int(row.get("episode") or 0)
        except (TypeError, ValueError):
            episode_number = 0
        if episode_number < 1:
            raise ValueError(f"Archive entry {index + 1} requires an episode number for metadata.")
        if cutoff and date.fromisoformat(day) < cutoff:
            raise ValueError(
                f"Archive entry {index + 1} predates configured archive cutoff {cutoff.isoformat()}: {title}"
            )
        identity = hashlib.sha256(("archive:" + uri).encode("utf-8")).hexdigest()
        record = published.get(identity, {}) if isinstance(published, dict) else {}
        if _is_complete(record):
            continue
        normalized.append({
            "identity": identity,
            "title": title,
            "url": uri,
            "release_date": day,
            "episode": episode_number,
        })
    if not normalized:
        return None, []
    oldest_upload_date = min(row["release_date"] for row in normalized)
    selected = [row for row in normalized if row["release_date"] == oldest_upload_date]
    selected.sort(key=lambda row: (row["title"].casefold(), row["identity"]))
    return oldest_upload_date, selected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="data/archive_manifest.json")
    parser.add_argument("--state", default="data/state.json")
    parser.add_argument("--github-output", default="")
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    config_path = Path("config.json")
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    cutoff = str(config.get("archive_cutoff_date") or "2026-09-01")
    state_path = Path(args.state)
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"published": {}}
    upload_date, entries = select_next_upload_date(manifest, state, cutoff_date=cutoff)
    batch_key = f"upload-date-{upload_date}" if upload_date else ""
    matrix = {"include": entries}
    has_work = "true" if entries else "false"
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as output:
            output.write(f"has_work={has_work}\n")
            output.write(f"release_date={upload_date if upload_date is not None else ''}\n")
            output.write(f"batch_key={batch_key}\n")
            output.write("matrix=" + json.dumps(matrix, ensure_ascii=False, separators=(",", ":")) + "\n")
    else:
        print(json.dumps({
            "has_work": has_work,
            "release_date": upload_date,
            "batch_key": batch_key,
            "matrix": matrix,
        }, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
