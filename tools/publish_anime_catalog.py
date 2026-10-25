"""Publish a poster-backed, metadata-only Telegram catalog for every discovered anime series.

This workflow never downloads, links, or redistributes episode video files.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import random
from pathlib import Path

import requests

LOG = logging.getLogger("animehighbo.anime_catalog")
ANILIST_URL = "https://graphql.anilist.co"
TELEGRAM_API = "https://api.telegram.org"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "AnimehighboCatalog/1.0"})


def series_title(title: str) -> str:
    """Strip common release episode/quality suffixes from a release title."""
    value = re.sub(r"\s*[-–—]\s*\d{1,4}\s*\([^)]*\)\s*$", "", str(title or "")).strip()
    value = re.sub(r"\s*[-–—]\s*\d{1,4}\s*\[[^]]*\]\s*$", "", value).strip()
    value = re.sub(r"\s*[-–—]\s*\d{1,4}\s*$", "", value).strip()
    return value or str(title or "").strip()


def unique_series(rows: list[dict]) -> list[str]:
    titles = {series_title(str(row.get("title", ""))) for row in rows if isinstance(row, dict)}
    return sorted((title for title in titles if title), key=str.casefold)


def find_poster_kitsu(title: str, timeout: int = 20) -> dict:
    """Fallback poster lookup when AniList is unavailable or returns HTTP 404."""
    response = SESSION.get(
        "https://kitsu.io/api/edge/anime",
        params={"filter[text]": title, "page[limit]": 1},
        headers={"Accept": "application/vnd.api+json"},
        timeout=timeout,
    )
    response.raise_for_status()
    rows = (response.json() or {}).get("data") or []
    if not rows or not isinstance(rows[0], dict):
        raise ValueError(f"Kitsu returned no anime match for {title!r}")
    item = rows[0]
    attrs = item.get("attributes") or {}
    titles = attrs.get("titles") or {}
    poster_image = attrs.get("posterImage") or {}
    poster = poster_image.get("original") or poster_image.get("large") or poster_image.get("medium") or ""
    if not poster.startswith("https://"):
        raise ValueError(f"Kitsu did not return an HTTPS poster for {title!r}")
    description = re.sub(r"\s+", " ", str(attrs.get("synopsis") or "")).strip()
    if len(description) > 600:
        description = description[:597].rsplit(" ", 1)[0] + "..."
    start_date = str(attrs.get("startDate") or "")
    slug = str(attrs.get("slug") or item.get("id") or "")
    return {
        "query_title": title,
        "title": titles.get("en") or titles.get("en_jp") or attrs.get("canonicalTitle") or title,
        "romaji": titles.get("en_jp") or attrs.get("canonicalTitle") or title,
        "native": titles.get("ja_jp") or "",
        "poster_url": poster,
        "site_url": f"https://kitsu.io/anime/{slug}" if slug else "https://kitsu.io",
        "year": int(start_date[:4]) if re.match(r"^\d{4}", start_date) else None,
        "episodes": attrs.get("episodeCount"),
        "genres": [],
        "description": description,
        "anilist_id": None,
        "metadata_source": "Kitsu",
    }


def find_poster(title: str, timeout: int = 20, max_attempts: int = 7) -> dict:
    query = """
    query ($search: String) {
      Media(search: $search, type: ANIME, sort: SEARCH_MATCH) {
        id
        title { romaji english native }
        coverImage { extraLarge large medium }
        siteUrl
        description(asHtml: false)
        seasonYear
        episodes
        genres
      }
    }
    """
    response = None
    for attempt in range(max_attempts):
        try:
            response = SESSION.post(
                ANILIST_URL, json={"query": query, "variables": {"search": title}},
                timeout=timeout,
            )
        except requests.RequestException:
            if attempt + 1 >= max_attempts:
                raise
            delay = min(90, 5 * (2 ** attempt)) + random.uniform(0.2, 1.8)
            LOG.warning("AniList request failed for %r; retrying in %.1fs (%d/%d).",
                        title, delay, attempt + 1, max_attempts)
            time.sleep(delay)
            continue
        if response.status_code == 429 or 500 <= response.status_code < 600:
            if attempt + 1 >= max_attempts:
                response.raise_for_status()
            retry_after = response.headers.get("Retry-After", "")
            try:
                delay = max(1.0, float(retry_after))
            except (TypeError, ValueError):
                delay = min(90, 8 * (2 ** attempt))
            delay += random.uniform(0.2, 1.8)
            LOG.warning("AniList returned HTTP %s for %r; retrying in %.1fs (%d/%d).",
                        response.status_code, title, delay, attempt + 1, max_attempts)
            time.sleep(delay)
            continue
        if response.status_code == 404:
            LOG.warning("AniList returned HTTP 404; falling back to Kitsu for %r.", title)
            return find_poster_kitsu(title, timeout=timeout)
        response.raise_for_status()
        break
    if response is None:
        return find_poster_kitsu(title, timeout=timeout)
    if response.status_code == 404:
        LOG.warning("AniList returned HTTP 404; falling back to Kitsu for %r.", title)
        return find_poster_kitsu(title, timeout=timeout)
    media = (response.json().get("data") or {}).get("Media")
    if not isinstance(media, dict):
        raise ValueError(f"AniList returned no anime match for {title!r}")
    cover = media.get("coverImage") or {}
    poster = cover.get("extraLarge") or cover.get("large") or cover.get("medium") or ""
    if not poster.startswith("https://"):
        raise ValueError(f"No HTTPS poster was available for {title!r}")
    titles = media.get("title") or {}
    description = re.sub(r"<[^>]+>", "", str(media.get("description") or ""))
    description = re.sub(r"\s+", " ", description).strip()
    if len(description) > 600:
        description = description[:597].rsplit(" ", 1)[0] + "..."
    return {
        "query_title": title,
        "title": titles.get("english") or titles.get("romaji") or title,
        "romaji": titles.get("romaji") or title,
        "native": titles.get("native") or "",
        "poster_url": poster,
        "site_url": media.get("siteUrl") or "",
        "year": media.get("seasonYear"),
        "episodes": media.get("episodes"),
        "genres": media.get("genres") or [],
        "description": description,
        "anilist_id": media.get("id"),
    }


def caption(record: dict) -> str:
    lines = [
        f"🎬 {record.get('title') or record['query_title']}",
        f"🇯🇵 {record.get('native') or '—'}",
        f"🔤 {record.get('romaji') or record['query_title']}",
        f"📅 {record.get('year') or '—'}  |  Episodes: {record.get('episodes') or '—'}",
    ]
    genres = record.get("genres") or []
    if genres:
        lines.append("🏷 " + " · ".join(str(item) for item in genres[:6]))
    if record.get("description"):
        lines.extend(["", record["description"]])
    if record.get("site_url"):
        lines.extend(["", "🔎 AniList: " + record["site_url"]])
    lines.append("\nℹ️ Anime details and poster only. Episode video is not included.")
    result = "\n".join(lines)
    return result[:1024]


def send_poster(token: str, chat_id: str, record: dict, timeout: int = 35) -> str:
    response = SESSION.get(record["poster_url"], timeout=timeout, stream=True)
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
    if not content_type.startswith("image/"):
        raise ValueError(f"Poster URL did not return an image: {record['query_title']}")
    image = response.content
    if not image or len(image) > 8 * 1024 * 1024:
        raise ValueError(f"Poster image is empty or exceeds 8 MB: {record['query_title']}")
    result = SESSION.post(
        f"{TELEGRAM_API}/bot{token}/sendPhoto",
        data={"chat_id": chat_id, "caption": caption(record)},
        files={"photo": ("anime-poster.jpg", image, content_type)},
        timeout=timeout,
    )
    result.raise_for_status()
    payload = result.json()
    if not payload.get("ok") or not (payload.get("result") or {}).get("message_id"):
        raise RuntimeError(f"Telegram sendPhoto failed for {record['query_title']}: {payload.get('description', 'unknown error')}")
    return str(payload["result"]["message_id"])


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHANNEL_ID", "").strip()
    if not token or not chat_id:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID in repository secrets.")
    manifest_path = Path(os.environ.get("ANIME_MANIFEST", "data/archive_manifest.json"))
    candidates_path = Path(os.environ.get("ANIME_CANDIDATES", "data/archive_candidates.json"))
    seasonal_path = Path(os.environ.get("ANIME_SEASONAL_REPORT", "data/seasonal_discovery_report.json"))
    state_path = Path(os.environ.get("ANIME_POSTER_STATE", "data/anime_poster_state.json"))
    rows = []
    for path in (manifest_path, candidates_path, seasonal_path):
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                rows.extend(data)
            elif isinstance(data, dict) and isinstance(data.get("entries"), list):
                for item in data["entries"]:
                    if not isinstance(item, dict):
                        continue
                    title = item.get("title_english") or item.get("title_romaji") or item.get("title_native")
                    if title:
                        rows.append({"title": title, "season_year": item.get("season_year"),
                                     "season": item.get("season"), "anilist_id": item.get("anilist_id")})
    if not rows:
        raise SystemExit("No anime entries were found in the manifest or discovery candidates.")
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"published": {}}
    published = state.setdefault("published", {})
    failed = []
    for title in unique_series(rows):
        key = title.casefold()
        if key in published:
            continue
        try:
            record = find_poster(title)
            message_id = send_poster(token, chat_id, record)
            published[key] = {
                "title": title,
                "message_id": message_id,
                "anilist_id": record.get("anilist_id"),
                "poster_url": record["poster_url"],
                "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            LOG.info("Published poster %s (%s)", title, message_id)
            time.sleep(4.0)
        except Exception as exc:
            failed.append(title)
            LOG.exception("Could not publish poster for %r: %s", title, exc)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    LOG.info("Poster catalog complete: %d published, %d failed, %d total unique series.",
             len(published), len(failed), len(unique_series(rows)))
    if failed:
        LOG.warning("Retrying failed poster lookups on the next scheduled run: %s", ", ".join(failed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
