"""Notify Telegram about newly detected release metadata, without distributing media."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Callable

import requests


def release_key(row: dict) -> str:
    title = str(row.get("title") or "").strip().casefold()
    try:
        episode = int(row.get("episode") or 0)
    except (TypeError, ValueError):
        return ""
    return f"{title}::episode-{episode}" if title and episode > 0 else ""


def new_releases(previous: dict, current: dict) -> list[dict]:
    old_rows = previous.get("releases", []) if isinstance(previous, dict) else []
    new_rows = current.get("releases", []) if isinstance(current, dict) else []
    old_keys = {key for row in old_rows if isinstance(row, dict) if (key := release_key(row))}
    result = []
    seen = set()
    for row in new_rows:
        if not isinstance(row, dict):
            continue
        key = release_key(row)
        if not key or key in old_keys or key in seen:
            continue
        seen.add(key)
        result.append(row)
    return sorted(result, key=lambda row: (str(row.get("release_date") or ""), str(row.get("title") or "").casefold(), int(row.get("episode") or 0)))


def format_messages(rows: list[dict], max_rows: int = 30) -> list[str]:
    if not rows:
        return []
    lines = ["🆕 Anime release metadata detected", "New entries from the release feed:"]
    for row in rows[:max_rows]:
        lines.append(f"• {row.get('title', 'Unknown')} — episode {row.get('episode', '?')} ({row.get('release_date', 'date unknown')})")
    if len(rows) > max_rows:
        lines.append(f"…and {len(rows) - max_rows} more entries. See the repository release report.")
    lines.extend(["", "Metadata notification only. This does not confirm an authorized media source or publish an episode."])
    text = "\n".join(lines)
    # Telegram sendMessage has a 4096-character limit. Keep a conservative margin.
    if len(text) <= 3900:
        return [text]
    chunks: list[str] = []
    current = ["🆕 Anime release metadata detected"]
    for line in lines[1:]:
        if sum(len(part) + 1 for part in current) + len(line) > 3500:
            chunks.append("\n".join(current))
            current = ["🆕 Anime release metadata detected (continued)"]
        current.append(line)
    if current:
        chunks.append("\n".join(current))
    return chunks


def notify(rows: list[dict], token: str, chat_id: str, post: Callable = requests.post) -> int:
    messages = format_messages(rows)
    if not messages:
        print("No new release metadata detected; no Telegram notification needed.")
        return 0
    if not token or not chat_id:
        print("New releases detected, but Telegram notification skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing.", file=sys.stderr)
        return 0
    for message in messages:
        response = post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
            timeout=(10, 30),
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram sendMessage failed: {payload!r}")
    print(f"Sent {len(rows)} newly detected release metadata row(s) to Telegram.")
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", required=True)
    parser.add_argument("--current", required=True)
    args = parser.parse_args()
    try:
        previous = json.loads(Path(args.previous).read_text(encoding="utf-8")) if Path(args.previous).exists() else {}
        current = json.loads(Path(args.current).read_text(encoding="utf-8"))
        rows = new_releases(previous, current)
        print(f"Detected {len(rows)} new release metadata row(s).")
        notify(rows, os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", ""))
        return 0
    except (OSError, ValueError, requests.RequestException, RuntimeError) as exc:
        print(f"Release notification failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
