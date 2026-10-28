"""Replace bot-owned episode anchor images with real series posters and refresh captions."""
from __future__ import annotations
import json, logging, os, time, re
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import anime_catalog_metadata, build_episode_caption, download_poster, telegram_api_endpoint
LOG = logging.getLogger("animehighbo.poster_backfill")

def normalize_title(value: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()

def discover_channel_anchors_from_html(content: str) -> tuple[list[dict], str | None]:
    """Parse real photo-message IDs from a public Telegram channel page."""
    more_match = re.search(
        r'<a\b(?=[^>]*class=["\'][^"\']*tme_messages_more[^"\']*["\'])(?=[^>]*href=["\']([^"\']+)["\'])[^>]*>',
        content,
        re.I,
    )
    next_url = unescape(more_match.group(1)) if more_match else None
    anchors = []
    wrapper_pattern = re.compile(
        r'<div\b[^>]*data-post=["\'](?:animehighbo|Animehighbo)/(\d+)["\'][^>]*>(.*?)(?=<div\b[^>]*data-post=["\'](?:animehighbo|Animehighbo)/\d+["\']|$)',
        re.I | re.S,
    )
    for match in wrapper_pattern.finditer(content):
        message_id, block = match.groups()
        if "tgme_widget_message_photo_wrap" not in block and "tgme_widget_message_photo" not in block:
            continue
        caption_match = re.search(
            r'<div\b[^>]*class=["\'][^"\']*tgme_widget_message_text[^"\']*["\'][^>]*>(.*?)</div>',
            block,
            re.I | re.S,
        )
        if not caption_match:
            continue
        caption = unescape(re.sub(r"<[^>]+>", " ", caption_match.group(1)))
        caption = re.sub(r"\s+", " ", caption).strip()
        title_match = re.search(
            r"(?:English|🇬🇧)\s*[:：]\s*(.+?)(?=\s+(?:فارسی|日本語|Japanese|قسمت|EP(?:ISODE)?)(?:\s|[:：]|$))",
            caption,
            re.I,
        )
        episode_match = re.search(r"(?:قسمت|EP(?:ISODE)?)\s*[:#：]?\s*0*(\d{1,4})", caption, re.I)
        if not title_match or not episode_match:
            continue
        title = re.sub(r"\s+", " ", title_match.group(1)).strip(" -–—:")
        if title:
            anchors.append({"message_id": message_id, "title": title, "episode": episode_match.group(1)})
    anchors.sort(key=lambda row: int(row["message_id"]))
    return anchors, next_url


def discover_channel_anchors(channel_slug: str = "Animehighbo", max_pages: int = 100) -> list[dict]:
    """Walk Telegram's public channel pagination and discover every live photo anchor."""
    slug = str(channel_slug or "Animehighbo").strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{4,64}", slug):
        raise ValueError("Public Telegram channel name is invalid.")
    url = f"https://t.me/s/{slug}"
    visited_urls = set()
    seen_ids = set()
    anchors = []
    for _ in range(max_pages):
        if url in visited_urls:
            break
        visited_urls.add(url)
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0 AnimehighboPosterAudit/1.0"}, timeout=(15, 30))
        response.raise_for_status()
        page_anchors, next_url = discover_channel_anchors_from_html(response.text)
        for anchor in page_anchors:
            if anchor["message_id"] not in seen_ids:
                seen_ids.add(anchor["message_id"])
                anchors.append(anchor)
        if not next_url:
            break
        url = urljoin(url, next_url)
    anchors.sort(key=lambda row: int(row["message_id"]))
    return anchors


def build_poster_index(payload: dict) -> dict:
    rows = payload.get("published", {}) if isinstance(payload, dict) else {}
    index = {}
    for key, record in rows.items() if isinstance(rows, dict) else []:
        if not isinstance(record, dict): continue
        url = str(record.get("poster_url") or "").strip()
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname: continue
        titles = [key, record.get("title", "")]
        aliases = record.get("aliases", [])
        if isinstance(aliases, list): titles.extend(aliases)
        metadata_title = (record.get("metadata") or {}).get("title", {})
        if isinstance(metadata_title, dict):
            titles.extend(metadata_title.get(field, "") for field in ("english", "romaji", "native"))
        for title in titles:
            normalized = normalize_title(title)
            if normalized: index[normalized] = record
            base = re.sub(r"(?i)\s+(?:season\s*\d+|s\d+|\d+(?:st|nd|rd|th)\s+season)$", "", str(title or "")).strip()
            base_normalized = normalize_title(base)
            if base_normalized: index[base_normalized] = record
    return index

def merge_cached_metadata(catalog: dict, cached: dict) -> dict:
    """Treat curated, title-matched metadata as authoritative over fuzzy catalog matches."""
    merged = dict(catalog) if isinstance(catalog, dict) else {}
    for key, value in (cached.items() if isinstance(cached, dict) else []):
        if value in (None, "", [], {}):
            continue
        if isinstance(value, dict):
            current = merged.get(key, {})
            current = dict(current) if isinstance(current, dict) else {}
            for subkey, subvalue in value.items():
                if subvalue not in (None, "", [], {}):
                    current[subkey] = subvalue
            merged[key] = current
        else:
            merged[key] = value
    return merged


def select_anchors(state: dict) -> list[dict]:
    posters = state.get("posters", {}) if isinstance(state, dict) else {}
    anchors = []
    for record in posters.values() if isinstance(posters, dict) else []:
        if not isinstance(record, dict) or not str(record.get("message_id") or "").isdigit(): continue
        if not str(record.get("title") or "").strip(): continue
        anchors.append(record)
    return sorted(anchors, key=lambda r: int(r["message_id"]))

def edit_anchor(token: str, chat_id: str, record: dict, image_data: bytes, image_type: str, caption: str, timeout: int = 60) -> None:
    extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(image_type, "jpg")
    media = {"type": "photo", "media": "attach://poster", "caption": caption, "parse_mode": "HTML"}
    for attempt in range(6):
        response = requests.post(
            telegram_api_endpoint(token, "editMessageMedia"),
            data={"chat_id": chat_id, "message_id": str(record["message_id"]), "media": json.dumps(media, ensure_ascii=False)},
            files={"poster": (f"anime-poster.{extension}", image_data, image_type)},
            timeout=(20, timeout),
        )
        try: payload = response.json()
        except ValueError: payload = {}
        if response.status_code < 400 and isinstance(payload, dict) and payload.get("ok") is True: return
        description = payload.get("description", f"HTTP {response.status_code}") if isinstance(payload, dict) else f"HTTP {response.status_code}"
        if "message is not modified" in str(description).casefold(): return
        parameters = payload.get("parameters", {}) if isinstance(payload, dict) else {}
        retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
        if response.status_code == 429 and attempt < 5:
            try: wait = max(1, min(60, int(retry_after or 5)))
            except (TypeError, ValueError): wait = 5
            LOG.warning("Telegram rate limit for message %s; retrying in %s seconds (attempt %s/6).", record["message_id"], wait, attempt + 1)
            time.sleep(wait + 1)
            continue
        raise RuntimeError(f"Telegram could not refresh message {record['message_id']}: {description}")

def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id: raise SystemExit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in repository secrets.")
    poster_path = Path(os.environ.get("ANIMEHIGHBO_POSTER_CACHE", "data/anime_poster_state.json"))
    cache = json.loads(poster_path.read_text(encoding="utf-8"))
    channel_slug = os.environ.get("ANIMEHIGHBO_PUBLIC_CHANNEL", "Animehighbo")
    anchors = discover_channel_anchors(channel_slug)
    LOG.info("Discovered %d live photo anchors from the public @%s channel.", len(anchors), channel_slug.lstrip("@"))
    index = build_poster_index(cache)
    if not anchors: raise SystemExit("No bot-owned poster anchors were found in state.json.")
    if not index: raise SystemExit("No verified poster URLs were found in anime_poster_state.json.")
    metadata_by_title, image_by_title = {}, {}
    success = failures = missing = 0
    for record in anchors:
        title = str(record["title"]).strip()
        episode = str(record.get("episode") or "").strip()
        key = normalize_title(title)
        poster = index.get(key)
        if not poster:
            missing += 1
            LOG.error("No exact poster-cache match for %s; leaving message %s unchanged.", title, record["message_id"])
            continue
        try:
            if key not in metadata_by_title:
                metadata = anime_catalog_metadata(title, timeout=15)
                metadata.setdefault("coverImage", {})
                metadata["coverImage"]["extraLarge"] = str(poster["poster_url"])
                cached_metadata = poster.get("metadata")
                if isinstance(cached_metadata, dict):
                    metadata = merge_cached_metadata(metadata, cached_metadata)
                metadata["coverImage"] = {"extraLarge": str(poster["poster_url"])}
                metadata_by_title[key] = metadata
            if key not in image_by_title:
                image_by_title[key] = download_poster(str(poster["poster_url"]), timeout=25)
            image_data, image_type = image_by_title[key]
            caption = build_episode_caption(title, episode, metadata=metadata_by_title[key])
            edit_anchor(token, chat_id, record, image_data, image_type, caption)
            success += 1
            if success % 10 == 0: LOG.info("Refreshed %d/%d episode poster anchors.", success, len(anchors))
            time.sleep(3.2)  # Stay below Telegram edit rate limits across the full channel backfill.
        except Exception as exc:
            failures += 1
            LOG.exception("Could not refresh poster/caption for %s episode %s (message %s): %s", title, episode, record["message_id"], exc)
    print(f"Poster backfill complete: {success} updated, {failures} failed, {missing} missing exact poster mapping, {len(anchors)} anchors checked.")
    return 1 if failures or missing else 0

if __name__ == "__main__": raise SystemExit(main())
