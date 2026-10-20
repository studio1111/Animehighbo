"""Download explicitly authorized torrent media with aria2; never infer rights from a feed."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".webm", ".mov"}


def validate_torrent_source(source: str, allowed_hosts: list[str]) -> str:
    """Accept magnet URIs or HTTPS .torrent URLs from exact allow-listed hosts."""
    source = str(source or "").strip()
    parsed = urlparse(source)
    if parsed.scheme.lower() == "magnet":
        params = parse_qs(parsed.query)
        if not any(value.lower().startswith("urn:btih:") for value in params.get("xt", [])):
            raise ValueError("Magnet URI must include a BitTorrent info hash.")
        if parsed.username or parsed.password:
            raise ValueError("Magnet URI must not contain credentials.")
        return source
    host = (parsed.hostname or "").lower().rstrip(".")
    allowed = {str(item).strip().lower().rstrip(".") for item in allowed_hosts}
    if (parsed.scheme != "https" or not host or host not in allowed
            or parsed.username or parsed.password
            or not parsed.path.lower().endswith(".torrent")):
        raise ValueError("Torrent file must be an HTTPS .torrent URL on an exact allow-listed host.")
    return source


def _tree_size(directory: Path) -> int:
    total = 0
    for path in directory.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except FileNotFoundError:
                pass
    return total


def download_authorized_torrent(
    source: str,
    title: str,
    config: dict,
    *,
    authorized_for_redistribution: bool = False,
) -> Path:
    """Download one authorized torrent, enforcing time/size limits and validating the result."""
    if authorized_for_redistribution is not True:
        raise PermissionError("Torrent source is disabled unless redistribution authorization is explicitly true.")
    source = validate_torrent_source(source, config.get("allowed_torrent_hosts", []))
    aria2 = shutil.which("aria2c")
    if not aria2:
        raise RuntimeError("aria2c is required. Install aria2 in the GitHub Actions runner.")
    target_dir = Path(config.get("download_dir", "downloads")).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_title = "".join(ch if ch.isalnum() or ch in " ._()-" else "_" for ch in title).strip(" ._")[:80] or "authorized-torrent"
    work_dir = target_dir / (".torrent-" + str(os.getpid()) + "-" + str(time.time_ns()))
    work_dir.mkdir(parents=True, exist_ok=False)
    max_bytes = int(config.get("max_file_size_mb", 45)) * 1024 * 1024
    timeout = max(1, int(config.get("torrent_timeout_seconds", 18000)))
    command = [
        aria2, "--dir", str(work_dir), "--continue=true",
        "--file-allocation=none", "--seed-time=0", "--summary-interval=0",
        "--console-log-level=warn", "--max-connection-per-server=8",
        "--split=8", "--bt-save-metadata=true", "--allow-overwrite=true",
        source,
    ]
    process = None
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        started = time.monotonic()
        while process.poll() is None:
            if _tree_size(work_dir) > max_bytes:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise ValueError("Torrent download exceeded configured total size limit.")
            if time.monotonic() - started > timeout:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise TimeoutError(f"Torrent download timed out after {timeout} seconds.")
            time.sleep(2)
        output = process.stdout.read()[-4000:] if process.stdout else ""
        if process.returncode != 0:
            raise RuntimeError(f"aria2c failed with exit code {process.returncode}: {output[-1200:]}")
        videos = sorted(
            (path for path in work_dir.rglob("*")
             if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS),
            key=lambda path: path.stat().st_size,
            reverse=True,
        )
        if not videos:
            raise ValueError("Torrent completed but contains no supported video file.")
        source_file = videos[0]
        if source_file.stat().st_size < 1024 or source_file.stat().st_size > max_bytes:
            raise ValueError("Torrent video is empty or exceeds configured size limit.")
        with source_file.open("rb") as stream:
            header = stream.read(12)
        suffix = source_file.suffix.lower()
        if suffix in {".mkv", ".webm"} and not header.startswith(bytes.fromhex("1A45DFA3")):
            raise ValueError("Downloaded Matroska/WebM file signature is invalid.")
        if suffix in {".mp4", ".mov"} and (len(header) < 8 or header[4:8] != b"ftyp"):
            raise ValueError("Downloaded MP4/MOV file signature is invalid.")
        final_path = target_dir / (safe_title + suffix)
        if final_path.exists():
            final_path = target_dir / (safe_title + "-" + str(time.time_ns()) + suffix)
        shutil.move(str(source_file), str(final_path))
        return final_path
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        shutil.rmtree(work_dir, ignore_errors=True)
