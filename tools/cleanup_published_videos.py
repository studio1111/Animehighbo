"""Remove tracked video files only when their exact content was confirmed published to Telegram."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

VIDEO_SUFFIXES = {".mkv", ".mp4", ".webm", ".mov", ".m4v"}


def published_video_paths(root: Path, state: dict, tracked_paths: list[str]) -> list[Path]:
    published = state.get("published", {})
    if not isinstance(published, dict):
        raise ValueError("Publication state has an invalid 'published' section.")

    confirmed_hashes = {
        str(entry.get("file_sha256", "")).lower()
        for entry in published.values()
        if isinstance(entry, dict)
        and entry.get("message_id") not in (None, "")
        and entry.get("published_at") not in (None, "")
        and len(str(entry.get("file_sha256", ""))) == 64
    }
    candidates = []
    for relative in tracked_paths:
        path = Path(relative)
        if path.suffix.lower() not in VIDEO_SUFFIXES or path.is_absolute() or ".." in path.parts:
            continue
        absolute = root / path
        if not absolute.is_file():
            continue
        digest = hashlib.sha256(absolute.read_bytes()).hexdigest()
        if digest in confirmed_hashes:
            candidates.append(path)
    return candidates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delete", action="store_true", help="Delete eligible files after verifying Telegram publication state.")
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    root = args.root.resolve()
    state_path = root / "data/state.json"
    if not state_path.is_file():
        raise SystemExit("Refusing cleanup: data/state.json is missing.")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True, capture_output=True,
    )
    tracked = [item.decode("utf-8", errors="strict") for item in result.stdout.split(b"\0") if item]
    candidates = published_video_paths(root, state, tracked)
    if not candidates:
        print("No tracked video files with confirmed Telegram publication were found.")
        return 0
    for path in candidates:
        print(f"{'Deleting' if args.delete else 'Eligible'}: {path.as_posix()}")
        if args.delete:
            (root / path).unlink()
    if not args.delete:
        print("Dry run only. Pass --delete to remove these files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
