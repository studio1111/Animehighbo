"""Monitor SubsPlease RSS release titles only; never download or forward torrent links."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

import requests

FEED_URL = "https://subsplease.org/rss/?r=1080"
SOURCE_URL = "https://subsplease.org/"
STATE_PATH = Path("data/subsplease_state.json")
USER_AGENT = "Animehighbo-ReleaseMonitor/1.0"


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_releases(xml_text: str, limit: int = 30) -> list[dict[str, str]]:
    """Extract release titles and stable IDs only; intentionally discard item URLs."""
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
        if title:
            key = identity or title
            entries.append({
                "title": title,
                "id": hashlib.sha256(key.encode("utf-8")).hexdigest(),
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
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(STATE_PATH)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parsed = urlparse(FEED_URL)
    if parsed.scheme != "https" or parsed.hostname != "subsplease.org":
        raise ValueError("SubsPlease feed must remain on the expected HTTPS host")

    response = requests.get(FEED_URL, timeout=30, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    releases = parse_releases(response.text)
    state = load_state()
    seen = set(state["seen"])
    if not releases:
        logging.warning("RSS returned no release entries.")
        return 0

    # Establish a baseline on the first run to avoid flooding the channel with old releases.
    if not seen:
        state["seen"] = [item["id"] for item in releases]
        save_state(state)
        logging.info("Initialized baseline with %d existing releases; no messages sent.", len(releases))
        return 0

    fresh = [item for item in reversed(releases) if item["id"] not in seen]
    if not fresh:
        logging.info("No new release titles.")
        return 0

    dry_run = os.getenv("SUBSPLEASE_DRY_RUN", "true").strip().lower() not in {"0", "false", "no", "off"}
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not dry_run and (not token or not chat_id):
        raise RuntimeError("Live notifications require TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")

    for item in fresh:
        text = f"🆕 انتشار جدید انیمه\n{item['title']}\n\nمنبع فهرست انتشار: SubsPlease\n{SOURCE_URL}\n\nAnimehighbo"
        if dry_run:
            logging.info("[DRY RUN] Would notify: %s", item["title"])
            continue
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"},
            timeout=(20, 40),
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise RuntimeError(f"Telegram sendMessage failed: {result.get('description', 'unknown error')}")
        logging.info("Notified: %s", item["title"])

    # Do not mark entries as processed during dry-run, so they can be announced after live mode is enabled.
    if not dry_run:
        state["seen"] = list(dict.fromkeys(state["seen"] + [item["id"] for item in releases]))[-500:]
        save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
