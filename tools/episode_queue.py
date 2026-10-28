"""Select episode candidates with manual denylist control and lock-free dispatch."""
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
    """Return a series/season key, falling back only when callers provide a title."""
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



def _manual_exclusion_key(entry: dict) -> str | None:
    """Normalize a series/title for exact manual denylist matching."""
    explicit = str(entry.get("series_title") or "").strip()
    if explicit:
        title = explicit
    else:
        identity = series_identity(entry)
        if identity:
            title = re.sub(r"::season-\d+$", "", identity)
        else:
            raw = str(entry.get("title") or "").strip()
            raw = re.sub(r"(?i)^\s*\[SubsPlease\]\s*", "", raw)
            raw = re.sub(r"(?i)\s+\(?\d{3,4}p\)?\s*$", "", raw)
            raw = re.sub(r"\s*\[[^\]]*\]\s*$", "", raw).strip()
            match = re.match(r"^(.*?)\s+-\s+\d{1,4}(?:\.\d+)?(?=\s*(?:\(|\[|$))", raw, re.I)
            title = match.group(1).strip(" -–—") if match else raw
    key = re.sub(r"\s+", " ", title).strip().casefold()
    return key or None


def filter_by_manual_exclusions(
    manifest: list[dict], exclusions: list[str]
) -> tuple[list[dict], dict[str, int]]:
    """Allow every valid titled entry by default; remove only explicitly excluded titles."""
    if not isinstance(exclusions, list) or not all(isinstance(item, str) for item in exclusions):
        raise ValueError("Manual exclusions must be a JSON list of strings.")
    denied = {re.sub(r"\s+", " ", item).strip().casefold() for item in exclusions if item.strip()}
    allowed: list[dict] = []
    counts = {"eligible": 0, "excluded": 0, "invalid": 0}
    for entry in manifest:
        if not isinstance(entry, dict):
            counts["invalid"] += 1
            continue
        key = _manual_exclusion_key(entry)
        if key is None:
            counts["invalid"] += 1
            continue
        if key in denied:
            counts["excluded"] += 1
            continue
        allowed.append(entry)
        counts["eligible"] += 1
    return allowed, counts

def _episode_dedupe_key(title: object, season: object, episode: object) -> tuple[str, int, int] | None:
    """Normalize series/season/episode independently of the release URI or date."""
    name = re.sub(r"\s+", " ", str(title or "")).strip()
    try:
        episode_number = int(episode)
        season_number = int(season or 1)
    except (TypeError, ValueError):
        return None
    if not name or episode_number < 1 or season_number < 1:
        return None
    season_match = re.search(r"\s+(?:S|Season\s*)(\d{1,2})$", name, re.I)
    if season_match:
        season_number = int(season_match.group(1))
        name = name[:season_match.start()].strip()
    name = re.sub(r"[–—−]", "-", name)
    name = re.sub(r"\s+", " ", name).strip().casefold()
    return (name, season_number, episode_number)


def _existing_posted_episode_keys(state: dict) -> set[tuple[str, int, int]]:
    """Return episodes already represented by a poster anchor, independent of source URI."""
    posters = state.get("posters", {}) if isinstance(state, dict) else {}
    keys: set[tuple[str, int, int]] = set()
    if isinstance(posters, dict):
        for poster in posters.values():
            if not isinstance(poster, dict):
                continue
            key = _episode_dedupe_key(poster.get("title"), poster.get("season"), poster.get("episode"))
            if key:
                keys.add(key)
    return keys


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
    existing_episode_keys = _existing_posted_episode_keys(state)

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
        series_title, _, season_text = series.rpartition("::season-")
        try:
            season_number = int(season_text)
        except ValueError:
            season_number = 1
        if _episode_dedupe_key(series_title, season_number, number) in existing_episode_keys:
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
    manifest: list[dict],
    state: dict,
    cutoff_date: str | None = None,
    target_series: str | None = None,
) -> tuple[str | None, list[dict]]:
    """Select eligible episodes in source-upload order while respecting per-series episode order.

    A series with any upload before the cutoff started in an earlier season and is
    skipped entirely, so the channel never receives an anime that is missing its start.
    """
    if not isinstance(manifest, list) or not manifest:
        return None, []
    # The archive catalog already applies the cutoff and intentionally backfills up to
    # three earlier episodes for short seasons. Re-applying a series-wide cutoff here
    # would blacklist those curated backfill episodes and every later episode in that series.
    published = state.get("published", {}) if isinstance(state, dict) else {}
    if not isinstance(published, dict):
        published = {}
    existing_episode_keys = _existing_posted_episode_keys(state)
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
        identity = hashlib.sha256(("archive:" + uri).encode("utf-8")).hexdigest()

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
                else:
                    # Keep unfamiliar release titles queueable for manual review.
                    # Use the cleaned release title as a fallback identity.
                    series_title = raw.strip()
        if season in (None, ""):
            season = 1 if series_title else None
        try:
            season_number = int(season) if season is not None else 0
        except (TypeError, ValueError):
            season_number = 0
        if not series_title or season_number < 1:
            # Entries without a usable fallback title or season cannot be queued safely.
            continue

        series_key = series_identity({
            "series_title": series_title,
            "season": season_number,
        })
        if series_key is None:
            continue
        if _is_complete(published.get(identity, {})):
            continue
        if _episode_dedupe_key(series_title, season_number, episode_number) in existing_episode_keys:
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
    # Source upload date is the publication order, like the source site. Each series
    # contributes only its next pending episode, so one anime never holds another.
    oldest_upload_date = min(row["release_date"] for row in eligible)
    eligible.sort(key=lambda row: (row["release_date"], row["title"].casefold(), row["identity"]))
    return oldest_upload_date, eligible


def empty_queue_reason(
    eligible_count: int,
    active_count: int,
    max_workers: int = 5,
    cooldown_until: str | None = None,
) -> str:
    """Explain why the dispatcher has no new worker to launch."""
    if cooldown_until:
        return (
            "Automatic dispatch is paused by the recorded translation quota cooldown "
            f"until {cooldown_until}."
        )
    if eligible_count <= 0:
        return (
            "No incomplete episode is eligible after completion, series-order, "
            "and retry-backoff checks."
        )
    if max_workers > 0 and active_count >= max_workers:
        return (
            f"Global worker capacity is full ({active_count}/{max_workers}); "
            "the scheduled dispatcher will retry."
        )
    return "All currently eligible episodes are already queued or running."

def exclude_prepared_identities(entries: list[dict], ready_identities: set[str]) -> list[dict]:
    """Do not start another worker while a successful episode artifact awaits publication."""
    ready = {value for value in ready_identities if isinstance(value, str) and value}
    return [entry for entry in entries if str(entry.get("identity") or "") not in ready]


def limit_to_available_slots(
    entries: list[dict],
    active_identities: set[str],
    max_workers: int = 5,
) -> list[dict]:
    """Keep total queued/running episode workers within the global concurrency cap."""
    if max_workers < 1:
        return []
    active = {identity for identity in active_identities if isinstance(identity, str) and identity}
    available_slots = max(0, max_workers - len(active))
    if available_slots == 0:
        return []
    return [
        entry for entry in entries
        if str(entry.get("identity") or "") not in active
    ][:available_slots]


def exclude_recently_failed_series(
    entries: list[dict],
    failed_identities: set[str],
) -> list[dict]:
    """Back off a recently failing series while keeping unrelated anime eligible.

    A failed episode only delays its own series; unrelated series remain eligible.
    """
    failed = {identity for identity in failed_identities if isinstance(identity, str) and identity}
    if not entries or not failed:
        return entries
    blocked_series = {
        str(entry.get("series_key") or "")
        for entry in entries
        if str(entry.get("identity") or "") in failed and entry.get("series_key")
    }
    return [
        entry for entry in entries
        if str(entry.get("identity") or "") not in failed
        and str(entry.get("series_key") or "") not in blocked_series
    ]


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
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    if not isinstance(manifest, list):
        raise ValueError("Archive manifest must contain a JSON list of episode entries.")
    # All titled entries are eligible by default; the manual denylist is the operator's exclusion control.
    # Unfamiliar titles use their cleaned release title as a fallback deny key.
    exclusions_path = Path("data/archive_exclusions.json")
    exclusions = json.loads(exclusions_path.read_text(encoding="utf-8")) if exclusions_path.exists() else []
    manual_exclusions = exclusions
    manifest, exclusion_counts = filter_by_manual_exclusions(manifest, manual_exclusions)
    print(
        "Manual exclusions only: "
        f"{exclusion_counts['eligible']} eligible episode candidate(s), "
        f"{exclusion_counts['excluded']} manually excluded, "
        f"{exclusion_counts['invalid']} invalid catalog row(s)."
    )
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write(
                "## Anime queue\\n\\n"
                f"- Eligible candidates: {exclusion_counts['eligible']}\\n"
                f"- Manually excluded candidates: {exclusion_counts['excluded']}\\n"
                f"- Invalid catalog rows: {exclusion_counts['invalid']}\\n"
                "- All titled entries are eligible by default; only data/archive_exclusions.json can manually exclude a title.\\n"
            )
    config_path = Path("config.json")
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    cutoff = str(config.get("archive_cutoff_date") or "2026-10-01")
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
    try:
        failed_raw = os.environ.get("ANIMEHIGHBO_RECENT_FAILED_IDENTITIES", "[]")
        failed_value = json.loads(failed_raw)
        if not isinstance(failed_value, list) or not all(isinstance(item, str) for item in failed_value):
            raise ValueError("recent failed identities must be a JSON list of strings")
        recent_failed_identities = set(failed_value)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Could not safely determine recent episode failures; refusing to dispatch.") from exc
    before_failure_backoff = len(entries)
    entries = exclude_recently_failed_series(entries, recent_failed_identities)
    if len(entries) < before_failure_backoff:
        print(
            f"Temporarily backing off {before_failure_backoff - len(entries)} episode candidate(s) "
            "from recently failing series; unrelated anime remain eligible."
        )
    # Upload date is a priority only, not a barrier: later dates may run as soon
    # as their own series is eligible, without stale global state blocking dispatch.
    if entries:
        upload_date = min(_day(entry.get("release_date")) for entry in entries)
    # Do not redispatch work that already has a queued or running worker. The
    # dispatcher workflow serializes this short planning phase, while workers run
    # as independent workflow runs and can overlap for different anime.
    try:
        active_raw = os.environ.get("ANIMEHIGHBO_ACTIVE_IDENTITIES", "[]")
        active_identities = set(json.loads(active_raw))
        if not all(isinstance(identity, str) for identity in active_identities):
            raise ValueError("active identities must be strings")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Could not safely determine active episode workers; refusing to dispatch.") from exc
    try:
        ready_raw = os.environ.get("ANIMEHIGHBO_READY_IDENTITIES", "[]")
        ready_value = json.loads(ready_raw)
        if not isinstance(ready_value, list) or not all(isinstance(item, str) for item in ready_value):
            raise ValueError("ready artifact identities must be a JSON list of strings")
        ready_identities = set(ready_value)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Could not safely determine prepared artifacts; refusing to redispatch.") from exc
    before_ready_filter = len(entries)
    entries = exclude_prepared_identities(entries, ready_identities)
    if len(entries) < before_ready_filter:
        print(f"Skipping {before_ready_filter - len(entries)} episode(s) already prepared and awaiting publication.")
    # The cap applies only to queued/running workers, not to prepared artifacts.
    eligible_before_slots = len(entries)
    entries = limit_to_available_slots(entries, active_identities, max_workers=5)
    print(
        f"Queue plan: catalog_rows={len(manifest)}, "
        f"manually_excluded={exclusion_counts['excluded']}, "
        f"invalid_rows={exclusion_counts['invalid']}, "
        f"eligible_after_order_and_backoff={eligible_before_slots}, "
        f"active_workers={len(active_identities)}, selected_workers={len(entries)}."
    )
    if not entries:
        print(
            "No worker dispatched: "
            + empty_queue_reason(
                eligible_count=eligible_before_slots,
                active_count=len(active_identities),
                max_workers=5,
                cooldown_until=cooldown_until,
            )
        )
    catalog_dates = [
        _day(row.get("release_date"))
        for row in manifest
        if isinstance(row, dict) and row.get("release_date")
    ]
    if catalog_dates:
        print(
            f"Archive manifest coverage: {len(manifest)} rows; "
            f"latest source release date {max(catalog_dates)}."
        )
    candidate_path = Path("data/archive_candidates.json")
    if candidate_path.exists():
        try:
            candidates = json.loads(candidate_path.read_text(encoding="utf-8"))
            if isinstance(candidates, list):
                candidate_dates = [
                    _day(row.get("release_date"))
                    for row in candidates
                    if isinstance(row, dict) and row.get("release_date")
                ]
                latest_candidate_date = max(candidate_dates) if candidate_dates else "unknown"
                print(
                    f"Metadata discovery report: {len(candidates)} candidate row(s), "
                    f"latest source date {latest_candidate_date}; candidates are not "
                    "automatically publishable and are not added to the queue unless "
                    "they are in the authorized archive manifest."
                )
        except (OSError, ValueError, TypeError) as exc:
            print(f"Could not summarize metadata discovery report: {exc}")
    batch_key = f"priority-from-{upload_date}" if upload_date else ""
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
