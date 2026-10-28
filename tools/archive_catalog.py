"""Discover and refresh the ordered archive manifest from the SubsPlease catalogue."""
from __future__ import annotations

import argparse
from html import unescape
import json
import logging
import re
import time
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

LOG = logging.getLogger("animehighbo.archive_catalog")
BASE_URL = "https://subsplease.org"
USER_AGENT = "AnimehighboArchiveCatalog/1.0 (+authorized-media-only)"
# Explicitly check long-running series and known Fall 2026 shows even when the
# latest-feed pagination window does not surface a series page (for example when
# its first/only release is in late September). A page that has no eligible
# individual episode sources is safely ignored by the normal selection rules.
REQUIRED_SHOW_SLUGS = (
    "one-piece",
    "detective-conan",
    "kusuriya-no-hitorigoto",
    "tensei-shitara-ken-deshita-s2",
    "tokyo-revengers",
    "rilakkuma",
    "uchi-no-otouto-domo-ga-sumimasen",
    "doumo-suki-na-hito-ni-horegusuri-wo-irai-sareta-majo-desu",
    "tensei-goblin-dakedo-shitsumon-aru",
    "bleach-sennen-kessen-hen-kashin-tan",
)


def parse_release_date(value: str) -> date | None:
    value = str(value or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return parsedate_to_datetime(value).date()
        except (TypeError, ValueError, OverflowError):
            return None


def _episode_number(row: dict) -> int | None:
    raw = str(row.get("episode") or "").strip()
    if not re.fullmatch(r"\d{1,4}", raw):
        match = re.search(r"(?:episode|ep\.?\s*|[-–]\s*)(\d{1,4})(?:\s|$)", str(row.get("title") or ""), re.I)
        raw = match.group(1) if match else ""
    try:
        number = int(raw)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _preferred_download(row: dict, max_file_size_mb: int = 1800) -> tuple[str, str] | None:
    """Prefer the best available source that fits the configured download cap.

    SubsPlease magnet links expose the payload size in the optional ``xl`` query
    parameter. Avoid selecting a 1080p torrent that is known to exceed the worker
    limit when a smaller 720p source is available. Unknown sizes remain eligible;
    the downloader still enforces the hard limit while downloading.
    """
    downloads = row.get("downloads")
    if not isinstance(downloads, list):
        return None
    by_resolution = {
        str(item.get("res", "")).lower().replace("p", ""): str(item.get("magnet", "")).strip()
        for item in downloads if isinstance(item, dict)
    }
    max_bytes = max(1, int(max_file_size_mb)) * 1024 * 1024
    for resolution in ("1080", "720"):
        magnet = by_resolution.get(resolution, "")
        if not magnet.startswith("magnet:?xt=urn:btih:"):
            continue
        declared_size = re.search(r"(?:[?&])xl=(\d+)(?:&|$)", magnet)
        if declared_size and int(declared_size.group(1)) > max_bytes:
            LOG.warning(
                "Skipping %sp source with declared size %.1f MiB above the %d MiB cap.",
                resolution, int(declared_size.group(1)) / (1024 * 1024), max_file_size_mb,
            )
            continue
        return f"{resolution}p", magnet
    return None





def select_show_episodes(
    show_title: str,
    episodes: list[dict],
    cutoff: date,
    max_file_size_mb: int = 1800,
) -> list[dict]:
    """Select eligible SubsPlease episode sources for the automatic archive queue."""
    parsed_rows = []
    for row in episodes:
        if not isinstance(row, dict):
            continue
        number = _episode_number(row)
        released = parse_release_date(row.get("release_date") or row.get("releaseDate") or "")
        if number is None or released is None:
            continue
        parsed_rows.append((row, number, released))

    prior_episode_numbers = {number for _, number, released in parsed_rows if released < cutoff}
    include_prior = len(prior_episode_numbers) <= 3
    normalized = []
    for row, number, released in parsed_rows:
        if released < cutoff and not include_prior:
            continue
        source = _preferred_download(row, max_file_size_mb)
        if not source:
            LOG.warning("Skipping %s episode %s: no eligible 1080p/720p source.", show_title, number)
            continue
        quality, magnet = source
        normalized.append({
            "episode": number,
            "release_date": released,
            "quality": quality,
            "uri": magnet,
        })
    normalized.sort(key=lambda item: (item["episode"], item["release_date"], item["quality"]))
    selected_by_episode = {}
    for item in normalized:
        selected_by_episode.setdefault(item["episode"], item)
    selected = sorted(selected_by_episode.values(), key=lambda item: (
        item["release_date"], item["episode"]
    ))
    return [{
        "title": f"{show_title} - {item['episode']:02d} ({item['quality']})",
        "episode": item["episode"],
        "quality": item["quality"],
        "uri": item["uri"],
        "release_date": item["release_date"].isoformat(),
        "_episode": item["episode"],
        "_release_date": item["release_date"].isoformat(),
        "_quality": item["quality"],
    } for item in selected]



def _request_json(session: requests.Session, params: dict) -> dict:
    response = session.get(
        f"{BASE_URL}/api/",
        params=params,
        timeout=(5, 15),
        allow_redirects=False,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("Unexpected SubsPlease API response.")
    return data


def _show_page_url(raw_release: dict) -> str:
    page = str(raw_release.get("page") or "").strip()
    if not page:
        return ""
    candidate = urlparse(page)
    if candidate.scheme:
        url = page
    else:
        path = candidate.path.strip("/")
        if path.startswith("shows/"):
            url = urljoin(BASE_URL + "/", path)
        else:
            # The API's `page` field commonly contains only the show slug.
            url = urljoin(BASE_URL + "/shows/", path)
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "subsplease.org":
        return ""
    if not parsed.path.startswith("/shows/"):
        return ""
    return parsed._replace(path=parsed.path.rstrip("/") + "/").geturl()


def _show_title_from_html(document: str, fallback: str = "") -> str:
    """Prefer the visible show heading over the SEO page title."""
    heading = re.search(
        r"<h1\b[^>]*class=[\"'][^\"']*\bentry-title\b[^\"']*[\"'][^>]*>(.*?)</h1>",
        document, re.I | re.S,
    )
    if heading:
        title = unescape(re.sub(r"<[^>]+>", " ", heading.group(1)))
        title = re.sub(r"\s+", " ", title).strip()
        if title:
            return title
    page_title = re.search(r"<title[^>]*>(.*?)</title>", document, re.I | re.S)
    title = unescape(re.sub(r"<[^>]+>", " ", page_title.group(1))).strip() if page_title else ""
    title = re.sub(r"\s*(?:downloads?\s*)?(?:[-–—|:]\s*)?SubsPlease.*$", "", title, flags=re.I).strip()
    return title or fallback


def _fetch_show_episodes(session: requests.Session, page_url: str) -> tuple[str, list[dict]]:
    response = session.get(page_url, timeout=(5, 15), allow_redirects=False)
    response.raise_for_status()
    match = re.search(r"\bsid\s*=\s*['\"]?(\d+)", response.text, re.I)
    if not match:
        raise ValueError(f"Could not find SubsPlease show ID on {page_url}")
    payload = _request_json(session, {"f": "show", "tz": "Etc/GMT", "sid": match.group(1)})
    show_title = _show_title_from_html(response.text)
    raw_episodes = payload.get("episode", {})
    if not isinstance(raw_episodes, dict):
        return show_title, []
    episodes = []
    for key, value in raw_episodes.items():
        if not isinstance(value, dict):
            continue
        row = dict(value)
        row.setdefault("episode", key)
        row.setdefault("title", key)
        episodes.append(row)
    return show_title, episodes


def build_archive_manifest(
    cutoff: date, max_pages: int = 100, session: requests.Session | None = None,
    max_file_size_mb: int = 1800,
) -> list[dict]:
    """Discover and return an ordered manifest of eligible SubsPlease episode sources."""
    if max_pages < 1 or max_pages > 500:
        raise ValueError("max_pages must be between 1 and 500.")
    own_session = session is None
    client = session or requests.Session()
    client.headers.update({"User-Agent": USER_AGENT})
    show_pages: dict[str, dict] = {}
    try:
        for page_number in range(1, max_pages + 1):
            payload = _request_json(client, {"f": "latest", "tz": "Etc/GMT", "p": page_number})
            releases = list(payload.values())
            if not releases:
                break
            dates = [parse_release_date(item.get("release_date", "")) for item in releases if isinstance(item, dict)]
            for item in releases:
                if not isinstance(item, dict):
                    continue
                released = parse_release_date(item.get("release_date", ""))
                page_url = _show_page_url(item)
                if released and released >= cutoff and page_url:
                    show_pages.setdefault(page_url, item)
            valid_dates = [item for item in dates if item is not None]
            if valid_dates and max(valid_dates) < cutoff:
                break
            if page_number % 10 == 0:
                LOG.info("Scanned %d latest-release pages; found %d candidate show pages.", page_number, len(show_pages))
            time.sleep(0.2)
        LOG.info("Latest-release scan complete: %d candidate show pages.", len(show_pages))
        for slug in REQUIRED_SHOW_SLUGS:
            page_url = _show_page_url({"page": slug})
            if page_url:
                show_pages.setdefault(page_url, {"show": slug})
        manifest = []
        series = []
        for show_index, (page_url, sample) in enumerate(show_pages.items(), 1):
            LOG.info("Checking show page %d/%d: %s", show_index, len(show_pages), page_url)
            try:
                show_title, episodes = _fetch_show_episodes(client, page_url)
                show_title = show_title or str(sample.get("show") or sample.get("title") or page_url)
                selected = select_show_episodes(show_title, episodes, cutoff, max_file_size_mb)
                if selected:
                    first_date = min(row["_release_date"] for row in selected)
                    series.append((first_date, show_title.casefold(), show_title, selected))
            except (requests.RequestException, ValueError, KeyError) as exc:
                LOG.warning("Skipping show page %s: %s", page_url, exc)
            if show_index % 10 == 0:
                LOG.info("Processed %d/%d candidate show pages; %d have eligible episodes.", show_index, len(show_pages), len(series))
            time.sleep(0.2)
        LOG.info("Show catalogue scan complete: %d eligible series.", len(series))
        for _, _, show_title, selected in sorted(series):
            for row in selected:
                row.pop("_episode", None)
                row.pop("_release_date", None)
                row.pop("_quality", None)
                manifest.append(row)
        # The processing queue is global by source upload/release date, not grouped by show.
        manifest.sort(key=lambda row: (
            row.get("release_date", "9999-12-31"),
            str(row.get("title", "")).casefold(),
            int(row.get("episode", 0) or 0),
        ))
        # Source URIs are required for the durable archive queue; reject duplicates.
        seen = set()
        for row in manifest:
            uri = str(row.get("uri") or "").strip()
            if not uri:
                continue
            if uri in seen:
                raise ValueError(f"Duplicate source URI found while building archive: {row['title']}")
            seen.add(uri)
        return manifest
    finally:
        if own_session:
            client.close()


def merge_existing_manifest(manifest: list[dict], existing_rows: list[dict], cutoff: date) -> list[dict]:
    """Preserve valid existing archive entries when the live source API omits them."""
    merged = [
        dict(row) for row in manifest
        if isinstance(row, dict) and str(row.get("uri") or row.get("url") or "").strip()
    ]
    seen_uris = {str(row.get("uri") or row.get("url") or "").strip() for row in merged}

    def episode_key(row: dict) -> tuple[str, int | None]:
        title = str(row.get("title") or "").strip()
        title = re.sub(r"\s+\((?:1080|720|480)p\)$", "", title, flags=re.I)
        number = _episode_number(row)
        title = re.sub(r"\s+-\s+(?:episode\s*)?\d{1,4}(?:\.\d+)?$", "", title, flags=re.I)
        return re.sub(r"\s+", " ", title).strip().casefold(), number

    generated_episode_keys = {episode_key(row) for row in merged if isinstance(row, dict)}
    for row in existing_rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        uri = str(row.get("uri") or row.get("url") or "").strip()
        released = parse_release_date(row.get("release_date") or "")
        if (
            not title or not uri or uri in seen_uris or "[batch]" in title.casefold()
            or _episode_number(row) is None or released is None or released < cutoff
            or episode_key(row) in generated_episode_keys
        ):
            continue
        parsed = urlparse(uri)
        if parsed.scheme.lower() not in {"https", "magnet"}:
            continue
        preserved = dict(row)
        preserved["title"] = title
        preserved["uri"] = uri
        preserved["release_date"] = released.isoformat()
        merged.append(preserved)
        seen_uris.add(uri)
        generated_episode_keys.add(episode_key(preserved))

    merged.sort(key=lambda row: (
        str(row.get("release_date") or "9999-12-31"),
        str(row.get("title") or "").casefold(),
        int(row.get("episode") or 0),
    ))
    return merged


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default="2026-10-01", help="Include releases from this date (YYYY-MM-DD).")
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--max-file-size-mb", type=int, default=1800,
                        help="Skip sources whose declared payload exceeds this worker download limit.")
    parser.add_argument(
        "--output", default="data/archive_manifest.json",
        help="Output path for the durable, ordered archive manifest.",
    )
    args = parser.parse_args()
    destination = Path(args.output)
    try:
        cutoff = date.fromisoformat(args.since)
    except ValueError:
        parser.error("--since must be a valid YYYY-MM-DD date.")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.max_file_size_mb < 1:
        parser.error("--max-file-size-mb must be a positive integer.")
    manifest = build_archive_manifest(
        cutoff, args.max_pages, max_file_size_mb=args.max_file_size_mb,
    )
    if not manifest:
        LOG.error("No eligible episodes were found. Existing archive manifest was not overwritten.")
        return 1
    if destination.exists():
        try:
            existing_rows = json.loads(destination.read_text(encoding="utf-8"))
            if not isinstance(existing_rows, list):
                raise ValueError("Existing archive manifest must be a JSON list.")
            manifest = merge_existing_manifest(manifest, existing_rows, cutoff)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            LOG.error("Could not safely preserve existing archive entries: %s", exc)
            return 1
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    LOG.info("Wrote %d ordered archive entries to %s.", len(manifest), destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
