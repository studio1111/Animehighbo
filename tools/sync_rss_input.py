"""Sync the user-editable RSS input list into config.json without changing other settings."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "RSS input.txt"
CONFIG_PATH = ROOT / "config.json"


def parse_rss_input(text: str) -> list[dict[str, str]]:
    """Parse one HTTPS RSS/Atom URL per line; blank lines and # comments are ignored."""
    feeds: list[dict[str, str]] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(text.lstrip("\ufeff").splitlines(), start=1):
        value = raw_line.strip()
        if not value or value.startswith("#"):
            continue
        parsed = urlparse(value)
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or any(character.isspace() for character in value)
        ):
            raise ValueError(
                f"Line {line_number}: enter a complete HTTPS RSS/Atom URL (one URL per line)."
            )
        normalized = value.rstrip("/")
        if normalized in seen:
            continue
        seen.add(normalized)
        feeds.append({"name": f"RSS {len(feeds) + 1}: {parsed.hostname.lower()}", "url": value})
    return feeds


def sync_rss_input(input_path: Path = INPUT_PATH, config_path: Path = CONFIG_PATH) -> bool:
    """Replace config.feeds from the input file; preserve every other config setting."""
    feeds = parse_rss_input(input_path.read_text(encoding="utf-8"))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("config.json must contain a JSON object.")
    if config.get("feeds") == feeds:
        return False
    config["feeds"] = feeds
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Updated config.json with {len(feeds)} RSS feed(s).")
    return True


if __name__ == "__main__":
    sync_rss_input()
