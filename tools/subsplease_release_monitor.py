"""Metadata-only monitor for recent SubsPlease RSS releases.

This module stores titles, episode numbers, publication dates and coverage flags only.
It intentionally discards links, GUIDs, magnets and media URLs and never downloads or
publishes episode files.
"""
from __future__ import annotations

import json
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

FEEDS = {
    "1080p": "https://subsplease.org/rss/?r=1080",
    "720p": "https://subsplease.org/rss/?r=720",
}
USER_AGENT = "AnimehighboReleaseCoverage/1.0 (metadata-only)"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def _text(node: ET.Element, name: str) -> str:
    for child in list(node):
        if _local_name(child.tag) == name.casefold():
            return " ".join("".join(child.itertext()).split())
    return ""


def _release_date(raw: str) -> str | None:
    raw = str(raw or "").strip()
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).astimezone(timezone.utc).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
        except (TypeError, ValueError, OverflowError):
            return None


def _clean_title(raw: str) -> str:
    title = unicodedata.normalize("NFKC", str(raw or "")).strip()
    title = re.sub(r"(?i)^\s*\[SubsPlease\]\s*", "", title)
    title = re.sub(r"(?i)\.(?:mkv|mp4|webm)$", "", title)
    title = re.sub(r"(?i)\s*\((?:1080|720|480)p\)", "", title)
    title = re.sub(r"\s*\[[^\]]*\]\s*$", "", title)
    return " ".join(title.split()).strip()


def parse_feed(xml_text: str, quality: str) -> list[dict]:
    """Extract only release metadata; intentionally never read link or guid fields."""
    root = ET.fromstring(xml_text)
    releases: list[dict] = []
    for item in root.iter():
        if _local_name(item.tag) not in {"item", "entry"}:
            continue
        title = _clean_title(_text(item, "title"))
        released = _release_date(_text(item, "pubDate") or _text(item, "published") or _text(item, "updated"))
        if not title or not released or re.search(r"(?i)\b(batch|complete series)\b|\(\s*\d+\s*[-–]\s*\d+\s*\)", title):
            continue
        match = re.match(r"^(.*?)\s*(?:-\s*|\bepisode\s+|\bep\.?\s*)(\d{1,4})(?:\.\d+)?\s*$", title, re.I)
        if not match:
            # Hold specials, movie posts and ambiguous release names for manual review.
            continue
        series = " ".join(match.group(1).split()).strip(" -–—")
        episode = int(match.group(2))
        if not series or episode < 1:
            continue
        releases.append({
            "title": series,
            "episode": episode,
            "release_date": released,
            "quality": quality,
            "series_key": normalize_series(series),
        })
    return releases


def normalize_series(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def release_key(row: dict) -> str:
    # RSS rows carry a normalized series key; manifest rows usually carry a full
    # episode title, so strip the episode suffix before normalizing.
    series = str(row.get("series_key") or "").strip()
    episode = row.get("episode")
    if not series:
        explicit_series = str(row.get("series_title") or "").strip()
        if explicit_series:
            series = normalize_series(explicit_series)
        else:
            raw = _clean_title(str(row.get("title") or ""))
            match = re.match(r"^(.*?)\s*(?:-\s*|\bepisode\s+|\bep\.?\s*)(\d{1,4})(?:\.\d+)?\s*$", raw, re.I)
            if match:
                series = normalize_series(match.group(1))
                episode = episode or int(match.group(2))
    try:
        number = int(episode)
    except (TypeError, ValueError):
        return ""
    return f"{series}::episode-{number}" if series and number > 0 else ""

def build_report(
    feed_rows: list[dict],
    manifest: list[dict],
    today: date,
    cutoff: str = "2026-10-01",
    candidates: list[dict] | None = None,
) -> dict:
    cutoff_date = date.fromisoformat(cutoff)
    manifest_keys = {
        key for row in manifest if isinstance(row, dict)
        if (key := release_key(row))
        and _release_date(str(row.get("release_date") or "")) is not None
        and date.fromisoformat(_release_date(str(row.get("release_date") or ""))) >= cutoff_date
    }
    candidate_keys = {
        key for row in (candidates or []) if isinstance(row, dict)
        if (key := release_key(row))
        and _release_date(str(row.get("release_date") or "")) is not None
        and date.fromisoformat(_release_date(str(row.get("release_date") or ""))) >= cutoff_date
    }
    unique: dict[str, dict] = {}
    for row in feed_rows:
        if not isinstance(row, dict):
            continue
        released = str(row.get("release_date") or "")
        try:
            if date.fromisoformat(released) < cutoff_date:
                continue
        except ValueError:
            continue
        key = release_key(row)
        if not key:
            continue
        previous = unique.get(key)
        # Keep a single metadata row per episode, preferring the highest feed quality.
        if previous is None or {"480p": 1, "720p": 2, "1080p": 3}.get(str(row.get("quality")), 0) > {"480p": 1, "720p": 2, "1080p": 3}.get(str(previous.get("quality")), 0):
            unique[key] = {
                "title": str(row.get("title") or ""),
                "episode": int(row["episode"]),
                "release_date": released,
                "feed_quality": str(row.get("quality") or ""),
                "in_archive_manifest": key in manifest_keys,
                "in_candidate_report": key in candidate_keys,
                "missing_from_manifest": key not in manifest_keys,
            }
    releases = sorted(unique.values(), key=lambda row: (row["release_date"], row["title"].casefold(), row["episode"]))
    return {
        "generated_on": today.isoformat(),
        "source": "SubsPlease RSS metadata (1080p and 720p feeds)",
        "cutoff_date": cutoff,
        "metadata_only": True,
        "summary": {
            "feed_releases": len(releases),
            "matched_in_archive_manifest": sum(1 for row in releases if row["in_archive_manifest"]),
            "found_in_candidate_report": sum(1 for row in releases if row["in_candidate_report"]),
            "missing_from_manifest": sum(1 for row in releases if row["missing_from_manifest"]),
        },
        "policy": (
            "This report contains metadata only and compares RSS episode records with the automatic archive manifest. No media URLs are stored in the report."
        ),
        "releases": releases,
    }


def fetch_feed(session: requests.Session, quality: str, url: str) -> list[dict]:
    response = session.get(url, timeout=(10, 30), allow_redirects=True)
    response.raise_for_status()
    return parse_feed(response.text, quality)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    manifest_path = root / "data" / "archive_manifest.json"
    output_path = root / "data" / "subsplease_release_report.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, list):
            raise ValueError("Archive manifest must be a JSON list.")
        rows: list[dict] = []
        with requests.Session() as session:
            session.headers.update({"User-Agent": USER_AGENT})
            for quality, url in FEEDS.items():
                print(f"Checking {quality} RSS metadata...", flush=True)
                rows.extend(fetch_feed(session, quality, url))
                time.sleep(0.2)
        candidates: list[dict] = []
        config = json.loads((root / "config.json").read_text(encoding="utf-8"))
        cutoff = str(config.get("archive_cutoff_date") or "2026-10-01")
        report = build_report(rows, manifest, date.today(), cutoff, candidates)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report["summary"], ensure_ascii=False))
        print(f"Wrote {output_path}")
        return 0
    except (OSError, ValueError, requests.RequestException, ET.ParseError) as exc:
        print(f"Release coverage monitor failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
