"""Monitor SubsPlease release RSS feeds and publish poster-backed metadata only.

This monitor does not download torrents or forward magnet links. It uses AniList
for anime metadata/poster art and sends the configured Persian caption to Telegram.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

import requests
import sys

# When executed as `python tools/subsplease_monitor.py`, Python puts the
# tools/ directory (not the repository root) on sys.path. Add the project root
# so shared helpers in app.py resolve consistently in GitHub Actions and locally.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import (
    anime_catalog_metadata,
    build_episode_caption,
    download_poster,
    episode_details,
    telegram_api_endpoint,
)

FEEDS = {
    "720p": "https://subsplease.org/rss/?r=720",
    "1080p": "https://subsplease.org/rss/?r=1080",
}
STATE_PATH = Path("data/subsplease_state.json")
USER_AGENT = "Animehighbo-ReleaseMonitor/1.1"
ANILIST_IMAGE_QUERY = """
query ($search: String) {
  Media(search: $search, type: ANIME) {
    title { english romaji native }
    coverImage { extraLarge large medium }
    season seasonYear episodes genres format isAdult description(asHtml: false)
  }
}
"""


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_releases(xml_text: str, quality: str = "1080p", limit: int = 30) -> list[dict[str, str]]:
    """Extract only title/quality identity; never retain or expose magnet URLs."""
    root = ET.fromstring(xml_text)
    entries: list[dict[str, str]] = []
    for node in root.iter():
        if local_name(node.tag) not in {"item", "entry"}:
            continue
        title = ""
        identity = ""
        for child in list(node):
            name = local_name(child.tag)
            if name == "title" and not title:
                title = "".join(child.itertext()).strip()
            elif name in {"guid", "id"} and not identity:
                identity = "".join(child.itertext()).strip()
        if not title or "[batch]" in title.lower():
            continue
        # Quality is kept in the title because it is part of the requested post.
        stable_key = f"{quality}|{identity or title}"
        entries.append({
            "title": title,
            "quality": quality,
            "id": hashlib.sha256(stable_key.encode("utf-8")).hexdigest(),
        })
        if len(entries) >= limit:
            break
    return entries


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"seen": []}
    value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("State file must contain a JSON object")
    value.setdefault("seen", [])
    return value


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(STATE_PATH)


def fetch_metadata(title: str) -> dict:
    query_title, _ = episode_details(title)
    response = requests.post(
        "https://graphql.anilist.co",
        json={"query": ANILIST_IMAGE_QUERY, "variables": {"search": query_title}},
        timeout=20,
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
    )
    response.raise_for_status()
    media = (response.json().get("data") or {}).get("Media")
    return media if isinstance(media, dict) else {}


def poster_url_from_metadata(metadata: dict) -> str:
    cover = metadata.get("coverImage") or {}
    return str(cover.get("extraLarge") or cover.get("large") or cover.get("medium") or "")


def send_release(token: str, chat_id: str, item: dict[str, str]) -> None:
    title = item["title"]
    metadata = fetch_metadata(title)
    poster_url = poster_url_from_metadata(metadata)
    if not poster_url:
        raise RuntimeError(f"No anime poster found in AniList for release: {title}")

    image_data, image_type = download_poster(poster_url)
    _, episode = episode_details(title)
    # Caption generation gets the release title so its actual resolution is retained.
    from app import build_episode_caption
    caption = build_episode_caption(title, episode, metadata=metadata)

    parsed = urlparse(poster_url)
    if parsed.scheme != "https":
        raise ValueError("AniList poster URL must use HTTPS.")
    endpoint = telegram_api_endpoint(token, "sendPhoto")
    with requests.post(
        endpoint,
        data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
        files={"photo": ("anime-poster.jpg", image_data, image_type)},
        timeout=(30, 60),
    ) as response:
        response.raise_for_status()
        result = response.json()
    if not isinstance(result, dict) or result.get("ok") is not True:
        detail = result.get("description", "unknown Telegram API error") if isinstance(result, dict) else "invalid Telegram response"
        raise RuntimeError(f"Telegram sendPhoto failed: {detail}")


def merge_queue(queue: list[dict[str, str]], releases: list[dict[str, str]], seen: set[str]) -> list[dict[str, str]]:
    """Append unseen releases once, preserving the feed's newest-first order."""
    queued_ids = {item.get("id") for item in queue}
    merged = list(queue)
    for item in releases:
        release_id = item.get("id", "")
        if release_id and release_id not in seen and release_id not in queued_ids:
            merged.append(item)
            queued_ids.add(release_id)
    return merged[-1000:]


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for url in FEEDS.values():
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "subsplease.org":
            raise ValueError("SubsPlease feeds must remain on the expected HTTPS host.")

    releases: list[dict[str, str]] = []
    failures = 0
    for quality, feed_url in FEEDS.items():
        try:
            response = requests.get(feed_url, timeout=30, headers={"User-Agent": USER_AGENT})
            response.raise_for_status()
            entries = parse_releases(response.text, quality)
            logging.info("SubsPlease %s feed connected: %d releases parsed.", quality, len(entries))
            releases.extend(entries)
        except Exception:
            failures += 1
            logging.exception("SubsPlease %s feed failed.", quality)
    if not releases:
        if failures:
            raise RuntimeError("Could not read any SubsPlease quality feed.")
        logging.warning("SubsPlease RSS returned no release entries.")
        return 0

    state = load_state()
    state.setdefault("queue", [])
    seen = set(state["seen"])
    if not seen:
        # On first activation, publish only the newest 1080p release as a
        # launch post. Mark the rest of the current feed as historical so we
        # do not flood the channel with old episodes.
        launch_item = next(
            (item for item in releases if item.get("quality") == "1080p"),
            releases[0],
        )
        state["seen"] = list(dict.fromkeys(
            item["id"] for item in releases if item["id"] != launch_item["id"]
        ))[-1000:]
        state["queue"] = [launch_item]
        save_state(state)
        logging.info(
            "Initialized baseline with %d historical releases; queued one launch post: %s",
            len(state["seen"]), launch_item["title"],
        )
        seen = set(state["seen"])

    queue = merge_queue(state["queue"], releases, seen)
    if not queue:
        logging.info("SubsPlease feeds connected; no pending or new releases.")
        return 0

    dry_run_value = os.getenv("SUBSPLEASE_DRY_RUN", "").strip().lower()
    dry_run = not dry_run_value or dry_run_value not in {"0", "false", "no", "off"}
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not dry_run and (not token or not chat_id):
        raise RuntimeError("Live notifications require TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")

    batch = queue[:5]
    logging.info("Queue contains %d releases; this run will process at most %d.", len(queue), len(batch))
    if dry_run:
        for item in batch:
            logging.info("[DRY RUN] Would publish %s release: %s", item["quality"], item["title"])
        state["queue"] = queue
        save_state(state)
        return 0

    processed: list[str] = []
    for item in batch:
        try:
            send_release(token, chat_id, item)
            processed.append(item["id"])
            logging.info("Published poster-backed %s release: %s", item["quality"], item["title"])
        except Exception:
            logging.exception("Could not publish release; it remains queued: %s", item["title"])

    processed_set = set(processed)
    state["seen"] = list(dict.fromkeys(state["seen"] + processed))[-1000:]
    state["queue"] = [item for item in queue if item["id"] not in processed_set]
    save_state(state)
    logging.info("Batch complete: %d published, %d still queued.", len(processed), len(state["queue"]))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
