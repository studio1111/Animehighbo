"""Select and gate authorized episodes without cross-series failure coupling."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from datetime import date, datetime, timezone


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
            # A season-one default is allowed only for a title with an explicit
            # conventional "Series - NN" episode suffix, or explicit series metadata.
            explicit_episode_suffix = re.search(
                r"^(.*?)\s+-\s+\d{1,4}(?:\.\d+)?(?=\s*(?:\(|\[|$))",
                raw_title,
                re.I,
            )
            if not str(entry.get("series_title") or "").strip() and not explicit_episode_suffix:
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




def prioritize_locked_episode(
    entries: list[dict],
    state: dict,
    lock_path: str | Path = "data/publisher.lock",
) -> list[dict]:
    """Resume an interrupted episode before allowing unrelated Telegram posts."""
    path = Path(lock_path)
    if not path.exists():
        return entries
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError("Publisher lock is malformed; refusing to queue unrelated episodes.") from exc
    if not isinstance(lock, dict):
        raise RuntimeError("Publisher lock is malformed; refusing to queue unrelated episodes.")
    identity = str(lock.get("identity") or "").strip()
    if not identity:
        raise RuntimeError("Publisher lock has no episode identity; refusing to queue unrelated episodes.")

    matching = [entry for entry in entries if episode_identity(entry) == identity]
    if matching:
        # A retained lock means the previous episode may have a partial poster thread.
        # Retry it first so the three files stay together and the lock can be released.
        return matching + [entry for entry in entries if episode_identity(entry) != identity]

    published = state.get("published", {}) if isinstance(state, dict) else {}
    if _is_complete(published.get(identity) if isinstance(published, dict) else None):
        # The next publisher will verify completion and remove this stale lock.
        return entries

    raise RuntimeError(
        f"Publisher lock is held by incomplete episode {identity}, but that episode "
        "is not eligible in the current archive queue. Refusing to start unrelated publishers."
    )


def select_next_upload_date(
    manifest: list[dict],
    state: dict,
    cutoff_date: str | None = None,
    target_series: str | None = None,
) -> tuple[str | None, list[dict]]:
    """Select the oldest eligible upload date while respecting per-series episode order."""
    if not isinstance(manifest, list) or not manifest:
        return None, []
    cutoff = date.fromisoformat(cutoff_date) if cutoff_date else None
    published = state.get("published", {}) if isinstance(state, dict) else {}
    if not isinstance(published, dict):
        published = {}
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
        record = published.get(identity, {})
        if _is_complete(record):
            continue

        series_title = str(row.get("series_title") or "").strip()
        season = row.get("season")
        if not series_title:
            raw = re.sub(r"(?i)^\s*\[SubsPlease\]\s*", "", title)
            raw = re.sub(r"(?i)\s+\(?\d{3,4}p\)?\s*$", "", raw)
            raw = re.sub(r"\s*\[[^\]]*\]\s*$", "", raw).strip()
            match = re.match(
                r"^(.*?)\s+-\s+(\d{1,4}(?:\.\d+)?)(?=\s*(?:\(|\[|$))",
                raw,
                re.I,
            )
            if match:
                series_title = match.group(1).strip(" -–—")
            else:
                season_match = re.search(r"(?i)^(.*?)\s+S(\d{1,2})\s*E\d{1,4}\b", raw)
                if season_match:
                    series_title = season_match.group(1).strip(" -–—")
                    season = int(season_match.group(2))
        if season in (None, ""):
            season = 1 if series_title else None
        try:
            season_number = int(season) if season is not None else 0
        except (TypeError, ValueError):
            season_number = 0
        if not series_title or season_number < 1:
            # Ambiguous identity is held rather than guessed.
            continue

        series_key = series_identity({
            "series_title": series_title,
            "season": season_number,
        })
        if series_key is None:
            continue
        normalized.append({
            "identity": identity,
            "title": title,
            "url": uri,
            "release_date": day,
            "episode": episode_number,
            "series_title": series_title,
            "season": season_number,
            "series_key": series_key,
        })

    if not normalized:
        return None, []
    counts: dict[tuple[str, int, int], int] = {}
    for row in normalized:
        key = (row["series_title"].casefold(), row["season"], row["episode"])
        counts[key] = counts.get(key, 0) + 1

    eligible = []
    for row in normalized:
        series_key = (row["series_title"].casefold(), row["season"])
        episode_key = (*series_key, row["episode"])
        if counts[episode_key] != 1:
            continue
        blocked = False
        for earlier in normalized:
            earlier_series = (earlier["series_title"].casefold(), earlier["season"])
            if earlier_series != series_key or earlier["episode"] >= row["episode"]:
                continue
            if not _is_complete(published.get(earlier["identity"], {})):
                blocked = True
                break
        if not blocked:
            eligible.append(row)

    if target_series:
        target = re.sub(r"\s+", " ", target_series).strip().casefold()
        eligible = [row for row in eligible if row["series_key"] == target]
    if not eligible:
        return None, []
    # Upload date is a stable priority, not a global batch barrier. Return every
    # currently eligible episode so one failed anime cannot hold unrelated series.
    oldest_upload_date = min(row["release_date"] for row in eligible)
    eligible.sort(key=lambda row: (row["release_date"], row["title"].casefold(), row["identity"]))
    return oldest_upload_date, eligible


def active_quota_cooldown(path: str | Path, now: datetime | None = None) -> str | None:
    """Return a future Gemini quota reset timestamp, if one is recorded."""
    cooldown_path = Path(path)
    if not cooldown_path.exists():
        return None
    try:
        payload = json.loads(cooldown_path.read_text(encoding="utf-8"))
        raw = str(payload.get("cooldown_until") or "").strip()
        if not raw:
            return None
        until = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        if until > current:
            return until.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        # Remove an expired cooldown marker so later runs cannot mistake a stale
        # quota incident for an active block and operators can see it has cleared.
        try:
            cooldown_path.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="data/archive_manifest.json")
    parser.add_argument("--state", default="data/state.json")
    parser.add_argument("--github-output", default="")
    parser.add_argument("--quota-cooldown", default="data/gemini_quota_cooldown.json")
    parser.add_argument("--publisher-lock", default="data/publisher.lock")
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    config_path = Path("config.json")
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    cutoff = str(config.get("archive_cutoff_date") or "2026-09-01")
    state_path = Path(args.state)
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"published": {}}
    target_series = os.environ.get("ANIMEHIGHBO_TARGET_SERIES", "").strip()
    cooldown_until = active_quota_cooldown(args.quota_cooldown)
    if cooldown_until:
        upload_date, entries = None, []
        print(f"Gemini free-tier quota cooldown active until {cooldown_until}; skipping episode dispatch to avoid wasting retries.")
    else:
        upload_date, entries = select_next_upload_date(
            manifest, state, cutoff_date=cutoff, target_series=target_series or None
        )
    # A retained lock represents an interrupted three-quality thread. Resume it
    # before selecting unrelated episodes, otherwise every publisher can wait forever.
    if entries or not cooldown_until:
        entries = prioritize_locked_episode(entries, state, args.publisher_lock)
    # Launch no more than five independent episode workers per dispatcher run.
    # Episode order remains gated per series by select_next_upload_date.
    entries = entries[:5]
    batch_key = f"upload-date-{upload_date}" if upload_date else ""
    matrix = {"include": entries}
    has_work = "true" if entries else "false"
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as output:
            output.write(f"has_work={has_work}\n")
            output.write(f"release_date={upload_date if upload_date is not None else ''}\n")
            output.write(f"batch_key={batch_key}\n")
            output.write(f"quota_cooldown_until={cooldown_until or ''}\n")
            output.write("matrix=" + json.dumps(matrix, ensure_ascii=False, separators=(",", ":")) + "\n")
    else:
        print(json.dumps({
            "has_work": has_work,
            "release_date": upload_date,
            "batch_key": batch_key,
            "quota_cooldown_until": cooldown_until,
            "matrix": matrix,
        }, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
