"""Discover current and upcoming seasonal anime and report archive coverage gaps.

This is metadata-only: it never downloads media, creates episode sources, or grants
redistribution rights. A title absent from the archive is a review candidate.
"""
from __future__ import annotations

import json
import re
import sys
import time
import unicodedata
from datetime import date
from pathlib import Path

import requests

API_URL = "https://graphql.anilist.co"
USER_AGENT = "AnimehighboSeasonalCoverage/1.0"
SEASONS = ("WINTER", "SPRING", "SUMMER", "FALL")
QUERY = """
query ($page: Int, $perPage: Int, $season: MediaSeason, $seasonYear: Int) {
  Page(page: $page, perPage: $perPage) {
    pageInfo { hasNextPage }
    media(season: $season, seasonYear: $seasonYear, type: ANIME,
          isAdult: false, sort: POPULARITY_DESC) {
      id
      siteUrl
      seasonYear
      season
      format
      status
      title { romaji english native }
      synonyms
    }
  }
}
"""


def season_for_month(month: int) -> str:
    if not 1 <= month <= 12:
        raise ValueError("month must be between 1 and 12")
    return SEASONS[(month - 1) // 3]


def upcoming_seasons(today: date, count: int = 4) -> list[tuple[int, str]]:
    """Return this season and the next count-1 seasons, including year rollover."""
    if count < 1 or count > 12:
        raise ValueError("count must be between 1 and 12")
    year = today.year
    index = SEASONS.index(season_for_month(today.month))
    result = []
    for _ in range(count):
        result.append((year, SEASONS[index]))
        index += 1
        if index == len(SEASONS):
            index = 0
            year += 1
    return result


def normalize_title(value: object) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    value = re.sub(r"\([^)]*\b(?:1080|720|480)p\b[^)]*\)", " ", value)
    value = re.sub(r"\b(?:episode|ep)\.?\s*\d{1,4}\b", " ", value)
    value = re.sub(r"\s+-\s+\d{1,4}(?:\.\d+)?\s*$", " ", value)
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def archive_title_keys(manifest: list[dict]) -> set[str]:
    keys: set[str] = set()
    for row in manifest:
        if not isinstance(row, dict):
            continue
        title = normalize_title(row.get("title"))
        # Most episode titles use "Series - 01 (1080p)". Keep the series prefix.
        title = re.sub(r"\s+\d{1,4}\s*$", "", title).strip()
        if title:
            keys.add(title)
    return keys


def media_title_keys(media: dict) -> set[str]:
    title = media.get("title") or {}
    values = [title.get("romaji"), title.get("english"), title.get("native")]
    values.extend(media.get("synonyms") or [])
    return {key for value in values if (key := normalize_title(value))}


def fetch_season(session: requests.Session, year: int, season: str) -> list[dict]:
    all_media: list[dict] = []
    page = 1
    while True:
        response = session.post(
            API_URL,
            json={"query": QUERY, "variables": {
                "page": page, "perPage": 50, "season": season, "seasonYear": year
            }},
            timeout=(10, 30),
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            raise RuntimeError(f"AniList API error for {season} {year}: {payload['errors']}")
        page_data = payload.get("data", {}).get("Page", {})
        all_media.extend(item for item in page_data.get("media", []) if isinstance(item, dict))
        if not page_data.get("pageInfo", {}).get("hasNextPage"):
            break
        page += 1
        time.sleep(0.25)
    return all_media


def build_report(
    manifest: list[dict],
    seasonal_media: list[dict],
    today: date,
    seasons: list[tuple[int, str]],
) -> dict:
    archive_keys = archive_title_keys(manifest)
    entries = []
    seen_ids = set()
    season_set = set(seasons)
    for media in seasonal_media:
        media_id = media.get("id")
        key = (media.get("seasonYear"), media.get("season"))
        if media_id in seen_ids or key not in season_set:
            continue
        seen_ids.add(media_id)
        keys = media_title_keys(media)
        covered = bool(keys & archive_keys)
        title = media.get("title") or {}
        entries.append({
            "anilist_id": media_id,
            "title_english": title.get("english"),
            "title_romaji": title.get("romaji"),
            "title_native": title.get("native"),
            "season": media.get("season"),
            "season_year": media.get("seasonYear"),
            "format": media.get("format"),
            "status": media.get("status"),
            "anilist_url": media.get("siteUrl"),
            "in_episode_manifest": covered,
            "review_required": not covered,
            "note": (
                "Metadata match found; verify episode-level coverage."
                if covered else
                "Seasonal title not matched to the episode manifest. Review source availability and rights."
            ),
        })
    entries.sort(key=lambda item: (
        item["season_year"] or 0, SEASONS.index(item["season"]) if item["season"] in SEASONS else 9,
        (item["title_english"] or item["title_romaji"] or "").casefold(),
    ))
    return {
        "generated_on": today.isoformat(),
        "discovery_source": "AniList GraphQL",
        "scope": [{"season": season, "year": year} for year, season in seasons],
        "summary": {
            "seasonal_titles": len(entries),
            "matched_in_manifest": sum(1 for item in entries if item["in_episode_manifest"]),
            "review_required": sum(1 for item in entries if item["review_required"]),
        },
        "titles": entries,
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    manifest_path = root / "data" / "archive_manifest.json"
    output_path = root / "data" / "seasonal_discovery_report.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, list):
            raise ValueError("archive manifest must be a JSON list")
        today = date.today()
        seasons = upcoming_seasons(today, 4)
        with requests.Session() as session:
            session.headers.update({"User-Agent": USER_AGENT, "Content-Type": "application/json"})
            media = []
            for year, season in seasons:
                print(f"Discovering AniList titles for {season} {year}...", flush=True)
                media.extend(fetch_season(session, year, season))
        report = build_report(manifest, media, today, seasons)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report["summary"], ensure_ascii=False))
        print(f"Wrote {output_path}")
        return 0
    except (OSError, ValueError, requests.RequestException, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Seasonal discovery failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
