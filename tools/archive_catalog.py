"""Build a chronological archive manifest from the SubsPlease show catalogue.

The generator only records one-episode releases with 1080p or 720p magnets.
It requires an explicit CLI confirmation that the operator has redistribution
rights; it never downloads or publishes media itself.
"""
from __future__ import annotations

import argparse
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


def _preferred_download(row: dict) -> tuple[str, str] | None:
    downloads = row.get("downloads")
    if not isinstance(downloads, list):
        return None
    by_resolution = {
        str(item.get("res", "")).lower().replace("p", ""): str(item.get("magnet", "")).strip()
        for item in downloads if isinstance(item, dict)
    }
    for resolution in ("1080", "720"):
        magnet = by_resolution.get(resolution, "")
        if magnet.startswith("magnet:?xt=urn:btih:"):
            return f"{resolution}p", magnet
    return None


def select_show_episodes(show_title: str, episodes: list[dict], cutoff: date) -> list[dict]:
    """Select full runs for series debuting on/after cutoff, otherwise only recent episodes.

    A show is treated as a new series only when its episode 1 release date is
    on/after cutoff. Selected episodes must be consecutive; a gap fails closed.
    """
    normalized = []
    for row in episodes:
        if not isinstance(row, dict):
            continue
        number = _episode_number(row)
        released = parse_release_date(row.get("release_date") or row.get("releaseDate") or "")
        source = _preferred_download(row)
        if number is None or released is None:
            continue
        quality, magnet = source if source else ("", "")
        normalized.append({
            "episode": number,
            "release_date": released,
            "quality": quality,
            "uri": magnet,
        })
    if not normalized:
        return []
    normalized.sort(key=lambda item: (item["episode"], item["release_date"]))
    # If episode 1 itself debuted in the requested window, backfill from episode 1.
    episode_one = next((item for item in normalized if item["episode"] == 1), None)
    if episode_one and episode_one["release_date"] >= cutoff:
        selected = [item for item in normalized if item["episode"] >= 1]
        expected_start = 1
    else:
        selected = [item for item in normalized if item["release_date"] >= cutoff]
        expected_start = selected[0]["episode"] if selected else 0
    if not selected:
        return []
    expected = list(range(expected_start, selected[-1]["episode"] + 1))
    if [item["episode"] for item in selected] != expected:
        LOG.warning("Skipping %s because its selected episode sequence has a gap.", show_title)
        return []
    if any(not item["uri"] or not item["quality"] for item in selected):
        LOG.warning("Skipping %s because one or more selected episodes lack a 1080p/720p source.", show_title)
        return []
    result = []
    for item in selected:
        result.append({
            "title": f"{show_title} - {item['episode']:02d} ({item['quality']})",
            "episode": item["episode"],
            "uri": item["uri"],
            "authorized_for_redistribution": True,
            "_episode": item["episode"],
            "_release_date": item["release_date"].isoformat(),
            "_quality": item["quality"],
        })
    return result


def _request_json(session: requests.Session, params: dict) -> dict:
    response = session.get(
        f"{BASE_URL}/api/",
        params=params,
        timeout=(10, 30),
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
    url = urljoin(BASE_URL + "/", page)
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "subsplease.org":
        return ""
    return url


def _fetch_show_episodes(session: requests.Session, page_url: str) -> tuple[str, list[dict]]:
    response = session.get(page_url, timeout=(10, 30), allow_redirects=False)
    response.raise_for_status()
    match = re.search(r"\bsid\s*=\s*['\"]?(\d+)", response.text, re.I)
    if not match:
        raise ValueError(f"Could not find SubsPlease show ID on {page_url}")
    payload = _request_json(session, {"f": "show", "tz": "Etc/GMT", "sid": match.group(1)})
    page_title = re.search(r"<title[^>]*>(.*?)</title>", response.text, re.I | re.S)
    show_title = re.sub(r"\s+", " ", page_title.group(1)).strip() if page_title else ""
    show_title = re.sub(r"\s*[-|:]\s*SubsPlease.*$", "", show_title, flags=re.I).strip()
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


def build_archive_manifest(cutoff: date, max_pages: int = 100, session: requests.Session | None = None) -> list[dict]:
    """Discover shows released since cutoff, then create an ordered one-file-per-episode manifest."""
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
            time.sleep(0.2)
        manifest = []
        series = []
        for page_url, sample in show_pages.items():
            try:
                show_title, episodes = _fetch_show_episodes(client, page_url)
                show_title = show_title or str(sample.get("show") or sample.get("title") or page_url)
                selected = select_show_episodes(show_title, episodes, cutoff)
                if selected:
                    first_date = min(row["_release_date"] for row in selected)
                    series.append((first_date, show_title.casefold(), show_title, selected))
            except (requests.RequestException, ValueError, KeyError) as exc:
                LOG.warning("Skipping show page %s: %s", page_url, exc)
            time.sleep(0.2)
        for _, _, show_title, selected in sorted(series):
            for row in selected:
                row.pop("_episode", None)
                row.pop("_release_date", None)
                row.pop("_quality", None)
                manifest.append(row)
        # Duplicate magnet URIs can arise from alternate episode labels; fail closed.
        seen = set()
        for row in manifest:
            if row["uri"] in seen:
                raise ValueError(f"Duplicate source URI found while building archive: {row['title']}")
            seen.add(row["uri"])
        return manifest
    finally:
        if own_session:
            client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default="2026-09-01", help="Include releases from this date (YYYY-MM-DD).")
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--output", default="data/archive_manifest.json")
    parser.add_argument(
        "--confirm-redistribution-rights",
        action="store_true",
        help="Confirm that you have rights to download, process, and republish every selected release.",
    )
    args = parser.parse_args()
    if not args.confirm_redistribution_rights:
        parser.error("Refusing to generate an authorized manifest without --confirm-redistribution-rights.")
    try:
        cutoff = date.fromisoformat(args.since)
    except ValueError:
        parser.error("--since must be a valid YYYY-MM-DD date.")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    manifest = build_archive_manifest(cutoff, args.max_pages)
    if not manifest:
        LOG.error("No complete, consecutive, eligible episodes were found. Manifest was not overwritten.")
        return 1
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    LOG.info("Wrote %d ordered episode entries to %s.", len(manifest), destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
