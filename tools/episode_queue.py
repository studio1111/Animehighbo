"""Select the oldest incomplete source-upload date, ignoring episode numbers for scheduling."""
from __future__ import annotations

import argparse
import hashlib
import json
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
        if {"1080p", "720p", "480p"}.issubset(set(record.get("complete_qualities", []))):
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
    # Source upload date is the sole scheduling priority. Episode numbers are metadata
    # only, so delayed premieres or delayed episodes are not reordered.
    oldest_upload_date = min(row["release_date"] for row in normalized)
    selected = [row for row in normalized if row["release_date"] == oldest_upload_date]
    selected.sort(key=lambda row: (row["title"].casefold(), row["identity"]))
    return oldest_upload_date, selected



def _normalized_series_title(row: dict) -> str:
    """Return a conservative series key, preferring explicit catalog metadata."""
    import re
    from urllib.parse import unquote, urlparse

    explicit = str(row.get("series_title") or row.get("anime_title") or "").strip()
    title = explicit or str(row.get("title") or "").strip()
    if not title:
        return ""
    # Remove common release labels only when no explicit series title is available.
    if not explicit:
        title = unquote(title)
        title = re.sub(r"(?i)^\s*\[SubsPlease\]\s*", "", title)
        title = re.sub(r"(?i)\.(?:mkv|mp4|webm|mov)$", "", title)
        title = re.sub(r"(?i)\s*[-–]\s*(?:episode\s*)?\d{1,4}(?:\.\d+)?\s*(?:\([^)]*\)|\[[^]]*\])?\s*$", "", title)
        title = re.sub(r"(?i)\s*\(?(?:480|720|1080|1440|2160)p\)?\s*$", "", title)
        title = re.sub(r"\[[^]]*\]", " ", title)
    title = re.sub(r"\s+", " ", title).strip(" -–|:")
    return title.casefold()


def series_identity(row: dict) -> str | None:
    """Identify a series/season without merging different seasons together.

    Legacy catalog entries without season metadata are treated as season 1 for
    backward compatibility. Explicit non-numeric season values remain distinct.
    """
    title = _normalized_series_title(row)
    if not title:
        return None
    raw_season = row.get("season_number", row.get("season", 1))
    if raw_season is None or str(raw_season).strip() == "":
        return None
    season = str(raw_season).strip().casefold()
    return f"{title}::season:{season}"


def _episode_number(row: dict) -> int | None:
    try:
        number = int(row.get("episode") or row.get("episode_number") or 0)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _archive_identity(row: dict) -> str | None:
    import hashlib
    uri = str(row.get("uri") or row.get("url") or "").strip()
    if not uri:
        return None
    return hashlib.sha256(("archive:" + uri).encode("utf-8")).hexdigest()


def _is_fully_published(row: dict, published: dict) -> bool:
    identity = _archive_identity(row)
    record = published.get(identity, {}) if identity else {}
    complete = set(record.get("complete_qualities", [])) if isinstance(record, dict) else set()
    return {"1080p", "720p", "480p"}.issubset(complete)


def eligible_prepared_episodes(
    manifest: list[dict], state: dict, ready_ids: set[str] | None = None
) -> list[dict]:
    """Return prepared episodes whose known predecessors in the same season are complete.

    A malformed or duplicate series/episode identity is held. Readiness is
    explicit: either the row has `prepared: true` or its archive URI identity is
    included in `ready_ids`. Published records are never returned as new work.
    """
    if not isinstance(manifest, list):
        return []
    state = state if isinstance(state, dict) else {}
    published = state.get("published", {})
    published = published if isinstance(published, dict) else {}
    ready_ids = ready_ids or set()

    normalized = []
    counts = {}
    for row in manifest:
        if not isinstance(row, dict):
            continue
        series = series_identity(row)
        episode = _episode_number(row)
        identity = _archive_identity(row)
        if series is None or episode is None or identity is None:
            continue
        key = (series, episode)
        counts[key] = counts.get(key, 0) + 1
        normalized.append((row, series, episode, identity, key))

    blocked_keys = {key for key, count in counts.items() if count > 1}
    by_series = {}
    for row, series, episode, identity, key in normalized:
        by_series.setdefault(series, []).append((row, episode, identity, key))

    eligible = []
    for row, series, episode, identity, key in normalized:
        if key in blocked_keys or _is_fully_published(row, published):
            continue
        ready = row.get("prepared") is True or identity in ready_ids
        if not ready:
            continue
        predecessors = [
            (prior_episode, prior_identity, prior_key)
            for _prior_row, prior_episode, prior_identity, prior_key in by_series[series]
            if prior_episode < episode
        ]
        if any(
            prior_key in blocked_keys
            or not _is_fully_published({"uri": next(
                str(candidate.get("uri") or candidate.get("url") or "")
                for candidate, candidate_series, candidate_episode, candidate_identity, candidate_key in normalized
                if candidate_series == series and candidate_episode == prior_episode and candidate_key == prior_key
            )}, published)
            for prior_episode, _prior_identity, prior_key in predecessors
        ):
            continue
        eligible.append(dict(row))
    eligible.sort(key=lambda row: (
        (series_identity(row) or ""),
        _episode_number(row) or 0,
        str(row.get("release_date") or ""),
        str(row.get("uri") or row.get("url") or ""),
    ))
    return eligible



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
