"""Replace bot-owned episode anchor images with real series posters and refresh captions."""
from __future__ import annotations
import json, logging, os, time
from pathlib import Path
from urllib.parse import urlparse
import requests
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import anime_catalog_metadata, build_episode_caption, download_poster, telegram_api_endpoint
LOG = logging.getLogger("animehighbo.poster_backfill")

def normalize_title(value: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()

def build_poster_index(payload: dict) -> dict:
    rows = payload.get("published", {}) if isinstance(payload, dict) else {}
    index = {}
    for key, record in rows.items() if isinstance(rows, dict) else []:
        if not isinstance(record, dict): continue
        url = str(record.get("poster_url") or "").strip()
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname: continue
        for title in (key, record.get("title", "")):
            normalized = normalize_title(title)
            if normalized: index[normalized] = record
    return index

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
    raise RuntimeError(f"Telegram could not refresh message {record['message_id']}: {description}")

def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id: raise SystemExit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in repository secrets.")
    state_path = Path(os.environ.get("ANIMEHIGHBO_STATE_FILE", "data/state.json"))
    poster_path = Path(os.environ.get("ANIMEHIGHBO_POSTER_CACHE", "data/anime_poster_state.json"))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    cache = json.loads(poster_path.read_text(encoding="utf-8"))
    anchors = select_anchors(state)
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
                    for field, value in cached_metadata.items():
                        if metadata.get(field) in (None, "", [], {}) and value not in (None, "", [], {}): metadata[field] = value
                metadata_by_title[key] = metadata
            if key not in image_by_title:
                image_by_title[key] = download_poster(str(poster["poster_url"]), timeout=25)
            image_data, image_type = image_by_title[key]
            caption = build_episode_caption(title, episode, metadata=metadata_by_title[key])
            edit_anchor(token, chat_id, record, image_data, image_type, caption)
            success += 1
            if success % 10 == 0: LOG.info("Refreshed %d/%d episode poster anchors.", success, len(anchors))
            time.sleep(0.25)
        except Exception as exc:
            failures += 1
            LOG.exception("Could not refresh poster/caption for %s episode %s (message %s): %s", title, episode, record["message_id"], exc)
    print(f"Poster backfill complete: {success} updated, {failures} failed, {missing} missing exact poster mapping, {len(anchors)} anchors checked.")
    return 1 if failures or missing else 0

if __name__ == "__main__": raise SystemExit(main())
