"""Select and gate authorized episodes without cross-series failure coupling."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from datetime import date, datetime, timezone

try:
    from tools.redistribution_rights import has_redistribution_authorization
except ModuleNotFoundError:  # Direct script execution puts tools/ rather than repo root on sys.path.
    from redistribution_rights import has_redistribution_authorization


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



def _approval_key(entry: dict) -> str | None:
    """Normalize a series title for explicit operator approval."""
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
            if not match:
                return None
            title = match.group(1).strip(" -–—")
    key = re.sub(r"\s+", " ", title).strip().casefold()
    return key or None


def filter_by_publication_approval(
    manifest: list[dict], policy: dict
) -> tuple[list[dict], dict[str, int]]:
    """Allow identifiable series by default; a manual denylist always takes precedence."""
    if not isinstance(policy, dict):
        raise ValueError("Publication approval policy must be a JSON object.")
    approved_raw = policy.get("approved_series", [])
    denied_raw = policy.get("denied_series", [])
    default_decision = str(policy.get("default_decision", "pending")).strip().casefold()
    if default_decision not in {"allow", "pending"}:
        raise ValueError("default_decision must be 'allow' or 'pending'.")
    if not isinstance(approved_raw, list) or not isinstance(denied_raw, list):
        raise ValueError("approved_series and denied_series must be JSON lists.")
    approved = {
        re.sub(r"\s+", " ", str(item)).strip().casefold()
        for item in approved_raw if isinstance(item, str) and item.strip()
    }
    denied = {
        re.sub(r"\s+", " ", str(item)).strip().casefold()
        for item in denied_raw if isinstance(item, str) and item.strip()
    }
    allowed: list[dict] = []
    counts = {"approved": 0, "pending": 0, "denied": 0, "ambiguous": 0}
    for entry in manifest:
        if not isinstance(entry, dict):
            counts["ambiguous"] += 1
            continue
        key = _approval_key(entry)
        if key is None:
            counts["ambiguous"] += 1
        elif key in denied:
            counts["denied"] += 1
        elif key in approved or default_decision == "allow":
            allowed.append(entry)
            counts["approved"] += 1
        else:
            counts["pending"] += 1
    return allowed, counts

def apply_global_rights_evidence(
    manifest: list[dict],
    evidence: str,
    reviewed_at: str,
) -> int:
    """Inherit a validated archive-wide license record without weakening the gate."""
    record = {
        "authorization_evidence": str(evidence or "").strip(),
        "authorization_reviewed_at": str(reviewed_at or "").strip(),
    }
    if not has_redistribution_authorization(record):
        return 0
    inherited = 0
    for entry in manifest:
        if not isinstance(entry, dict) or has_redistribution_authorization(entry):
            continue
        entry["authorization_evidence"] = record["authorization_evidence"]
        entry["authorization_reviewed_at"] = record["authorization_reviewed_at"]
        if has_redistribution_authorization(entry):
            inherited += 1
    return inherited


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

    parsed: list[tuple[dict, str, str, int, bool]] = []
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
        parsed.append((
            entry, identity, series, number,
            has_redistribution_authorization(entry),
        ))
        counts[(series, number)] = counts.get((series, number), 0) + 1

    eligible: list[dict] = []
    for entry, identity, series, number, authorized in parsed:
        if not authorized:
            continue
        status = str(entry.get("status") or "prepared").casefold()
        if status not in {"prepared", "ready", "complete"}:
            continue
        if _is_complete(published.get(identity)):
            continue
        # Duplicate entries for the same series/episode are ambiguous and held.
        if counts[(series, number)] != 1:
            continue
        blocked = False
        for earlier, earlier_identity, earlier_series, earlier_number, earlier_authorized in parsed:
            # An episode excluded by the rights gate must not block separately
            # authorized episodes in the same series. The gate applies per item.
            if not earlier_authorized or earlier_series != series or earlier_number >= number:
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



def select_upload_date_group(
    entries: list[dict],
    upload_date: str | None,
    state: dict,
    lock_path: str | Path = "data/publisher.lock",
) -> list[dict]:
    """Keep normal dispatches on one source-upload date; resume an interrupted thread first."""
    if not entries:
        return []
    path = Path(lock_path)
    if path.exists():
        try:
            lock = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise RuntimeError("Publisher lock is malformed; refusing to cross upload-date groups.") from exc
        if not isinstance(lock, dict):
            raise RuntimeError("Publisher lock is malformed; refusing to cross upload-date groups.")
        identity = str(lock.get("identity") or "").strip()
        if not identity:
            raise RuntimeError("Publisher lock has no episode identity; refusing to cross upload-date groups.")
        published = state.get("published", {}) if isinstance(state, dict) else {}
        record = published.get(identity, {}) if isinstance(published, dict) else {}
        if not _is_complete(record):
            matching = [entry for entry in entries if episode_identity(entry) == identity]
            if not matching:
                raise RuntimeError(
                    f"Publisher lock is held by incomplete episode {identity}, but it is not eligible."
                )
            # Do not let another date or poster thread overtake an interrupted publication.
            return [matching[0]]
    if not upload_date:
        return []
    return [entry for entry in entries if _day(entry.get("release_date")) == upload_date]


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
        if _is_complete(published.get(identity, {})):
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
            "authorized_for_redistribution": has_redistribution_authorization(row),
        })

    if not normalized:
        return None, []
    counts: dict[tuple[str, int, int], int] = {}
    for row in normalized:
        key = (row["series_title"].casefold(), row["season"], row["episode"])
        counts[key] = counts.get(key, 0) + 1

    eligible = []
    for row in normalized:
        if not row["authorized_for_redistribution"]:
            continue
        series_key = (row["series_title"].casefold(), row["season"])
        episode_key = (*series_key, row["episode"])
        if counts[episode_key] != 1:
            continue
        blocked = False
        for earlier in normalized:
            earlier_series = (earlier["series_title"].casefold(), earlier["season"])
            # Missing rights for one episode excludes that item only. It must not
            # prevent the queue from progressing to later episodes with valid rights.
            if (
                not earlier["authorized_for_redistribution"]
                or earlier_series != series_key
                or earlier["episode"] >= row["episode"]
            ):
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
    locked_identity: str | None = None,
) -> list[dict]:
    """Back off a recently failing series while keeping unrelated anime eligible.

    A retained global publisher lock is the exception: its owner must resume first
    to finish the existing poster thread without duplicating or interleaving posts.
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
        if str(entry.get("identity") or "") == locked_identity
        or (
            str(entry.get("identity") or "") not in failed
            and str(entry.get("series_key") or "") not in blocked_series
        )
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
    parser.add_argument("--publisher-lock", default="data/publisher.lock")
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    if not isinstance(manifest, list):
        raise ValueError("Archive manifest must contain a JSON list of episode entries.")
    # Identifiable titles are eligible by default; the manual denylist is the operator's exclusion control.
    # Ambiguous titles remain held, and explicit denial always overrides any legacy approval.
    approval_path = Path("data/anime_publication_approvals.json")
    if not approval_path.exists():
        raise RuntimeError("Manual publication policy is missing; refusing to dispatch any episode.")
    approval_policy = json.loads(approval_path.read_text(encoding="utf-8"))
    manifest, approval_counts = filter_by_publication_approval(manifest, approval_policy)
    print(
        "Manual publication policy: "
        f"{approval_counts['approved']} approved episode candidate(s), "
        f"{approval_counts['pending']} pending, {approval_counts['denied']} denied, "
        f"{approval_counts['ambiguous']} ambiguous."
    )
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write(
                "## Manual anime publication approvals\\n\\n"
                f"- Approved candidates: {approval_counts['approved']}\\n"
                f"- Pending candidates (not published): {approval_counts['pending']}\\n"
                f"- Denied candidates (not published): {approval_counts['denied']}\\n"
                f"- Ambiguous candidates (not published): {approval_counts['ambiguous']}\\n"
                "- Identifiable titles are eligible by default; denied_series takes precedence.\\n"
            )
    # Legal redistribution evidence remains a separate mandatory gate; manual approval
    # decides what the operator wants published, not whether rights exist.
    global_evidence = os.environ.get("ANIMEHIGHBO_AUTHORIZATION_EVIDENCE", "").strip()
    global_reviewed_at = os.environ.get("ANIMEHIGHBO_AUTHORIZATION_REVIEWED_AT", "").strip()
    inherited = apply_global_rights_evidence(manifest, global_evidence, global_reviewed_at)
    if inherited:
        print(f"Applied the validated repository-level rights record to {inherited} curated manifest entries.")
    elif global_evidence or global_reviewed_at:
        print("Repository-level rights record is incomplete or invalid; no global authorization was applied.")

    rights_blocked_count = sum(
        1 for entry in manifest if not has_redistribution_authorization(entry)
    )
    if rights_blocked_count:
        message = (
            f"Redistribution safety gate: {rights_blocked_count} manifest entry/entries "
            "blocked because rights evidence and a valid review date are missing or invalid. "
            "No worker will be dispatched for these entries."
        )
        print(message)
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as summary:
                summary.write(
                    "## Redistribution authorization gate\n\n"
                    f"- **Blocked manifest entries:** {rights_blocked_count}\n"
                    "- Automatic discovery is metadata-only and is not permission to redistribute.\n"
                    "- Add a genuine rights record and review date only after verifying the rights-holder permission.\n"
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
    # A retained lock represents an interrupted three-quality thread. Resume it
    # before selecting unrelated episodes, otherwise every publisher can wait forever.
    if entries or not cooldown_until:
        entries = prioritize_locked_episode(entries, state, args.publisher_lock)
    locked_identity = ""
    lock_path = Path(args.publisher_lock)
    if lock_path.exists():
        lock_payload = json.loads(lock_path.read_text(encoding="utf-8"))
        if isinstance(lock_payload, dict):
            locked_identity = str(lock_payload.get("identity") or "").strip()
    try:
        failed_raw = os.environ.get("ANIMEHIGHBO_RECENT_FAILED_IDENTITIES", "[]")
        failed_value = json.loads(failed_raw)
        if not isinstance(failed_value, list) or not all(isinstance(item, str) for item in failed_value):
            raise ValueError("recent failed identities must be a JSON list of strings")
        recent_failed_identities = set(failed_value)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Could not safely determine recent episode failures; refusing to dispatch.") from exc
    before_failure_backoff = len(entries)
    entries = exclude_recently_failed_series(
        entries, recent_failed_identities, locked_identity=locked_identity or None
    )
    if len(entries) < before_failure_backoff:
        print(
            f"Temporarily backing off {before_failure_backoff - len(entries)} episode candidate(s) "
            "from recently failing series; unrelated anime remain eligible."
        )
    # Upload date is a priority only, not a barrier: later dates may run as soon
    # as their own series is eligible, without waiting for the slowest earlier date.
    # The repository-wide publisher lock keeps each poster and its three replies contiguous.
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
    # The cap applies to all queued/running workers across dispatcher runs, not
    # merely to the number launched by this invocation.
    entries = limit_to_available_slots(entries, active_identities, max_workers=5)
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
