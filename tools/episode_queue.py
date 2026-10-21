"""Select one complete source-upload day for parallel episode preparation.

The script only plans work. It does not download, encode, or publish media.
"""
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
    # SubsPlease dates are ISO strings. Group by the source-local calendar date.
    try:
        parsed = date.fromisoformat(raw[:10])
        return parsed.isoformat()
    except (TypeError, ValueError):
        raise ValueError(f"Invalid source release_date: {raw!r}")


def select_oldest_upload_day(
    manifest: list[dict], state: dict, cutoff_date: str | None = None
) -> tuple[str, list[dict]]:
    if not isinstance(manifest, list) or not manifest:
        return "", []
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
            "episode": int(row.get("episode") or 0),
        })
    if not normalized:
        return "", []
    oldest = min(row["release_date"] for row in normalized)
    same_day = [row for row in normalized if row["release_date"] == oldest]
    same_day.sort(key=lambda row: (row["title"].casefold(), row["episode"], row["identity"]))
    return oldest, same_day


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
    day, entries = select_oldest_upload_day(manifest, state, cutoff_date=cutoff)
    matrix = {"include": entries}
    has_work = "true" if entries else "false"
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as output:
            output.write(f"has_work={has_work}\n")
            output.write(f"release_date={day}\n")
            output.write("matrix=" + json.dumps(matrix, ensure_ascii=False, separators=(",", ":")) + "\n")
    else:
        print(json.dumps({"has_work": has_work, "release_date": day, "matrix": matrix},
                         ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
