#!/usr/bin/env python3
"""Encode and validate licensed Morevna test videos before Telegram delivery."""
from __future__ import annotations

import json
import os
import subprocess
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import requests

VIDEO_URL = "https://commons.wikimedia.org/wiki/Special:Redirect/file/Morevna_Episode_3.0.2_(English).webm"
SRT_URL = "https://commons.wikimedia.org/wiki/TimedText:Morevna_Episode_3.0.2_(English).webm.en.srt?action=raw"
STATE = Path("data/morevna_test_delivery.json")
OUT = Path("work/morevna")
CRF = "22"
PRESET = "slow"
MIN_VALID_DURATION = 1.0
ATTRIBUTION = (
    "Morevna Episode 3.0.2 (English) by Morevna Project. "
    "Source: https://commons.wikimedia.org/wiki/File:Morevna_Episode_3.0.2_(English).webm\n"
    "License: CC BY-SA 4.0 https://creativecommons.org/licenses/by-sa/4.0/\n"
    "Changes: Persian subtitles translated from the community English subtitles; "
    "re-encoded as MKV/HEVC for 480p, 720p and 1080p. Independent fan test, not endorsed by the creators."
)


def run(args):
    subprocess.run(args, check=True)


def fetch(url, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(
        url, stream=True, timeout=(30, 180),
        headers={"User-Agent": "Animehighbo/1.0 (open-license test)"}
    ) as response:
        response.raise_for_status()
        with path.open("wb") as output_file:
            for chunk in response.iter_content(1024 * 1024):
                if chunk:
                    output_file.write(chunk)
    if path.stat().st_size < 1000:
        raise RuntimeError(f"Download unexpectedly small: {url}")
    print(f"Downloaded {path.name}: {path.stat().st_size} bytes", flush=True)


def load_state():
    if STATE.exists():
        try:
            value = json.loads(STATE.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                value.setdefault("sent", {})
                return value
        except Exception as exc:
            print(f"Could not read delivery state; treating it as empty: {exc}", flush=True)
    return {"sent": {}}


def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(STATE)


def probe_media(path):
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration,size:stream=codec_type,codec_name,width,height",
            "-of", "json", str(path)
        ],
        check=True, capture_output=True, text=True
    )
    return json.loads(result.stdout)


def validate_output(path, expected_width, expected_height):
    if not path.is_file() or path.stat().st_size < 100_000:
        raise RuntimeError(f"Encoded output is missing or implausibly small: {path}")
    info = probe_media(path)
    try:
        duration = float(info["format"]["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"Cannot read output duration; refusing to upload: {path}") from exc
    streams = info.get("streams", [])
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    subtitle_streams = [s for s in streams if s.get("codec_type") == "subtitle"]
    if duration < MIN_VALID_DURATION:
        raise RuntimeError(f"Output duration is {duration}s; refusing to upload zero-duration file: {path}")
    if not video_streams:
        raise RuntimeError(f"No video stream found in {path}")
    video = video_streams[0]
    if video.get("codec_name") != "hevc":
        raise RuntimeError(f"Expected HEVC/H.265, found {video.get('codec_name')} in {path}")
    if video.get("width") != expected_width or video.get("height") != expected_height:
        raise RuntimeError(
            f"Wrong output dimensions in {path}: {video.get('width')}x{video.get('height')}"
        )
    if len(subtitle_streams) != 2:
        raise RuntimeError(f"Expected two subtitle tracks (English and Persian), found {len(subtitle_streams)}")
    print(
        f"VALIDATED {path.name}: duration={duration:.2f}s, "
        f"size={path.stat().st_size} bytes, video=HEVC "
        f"{video.get('width')}x{video.get('height')}, subtitle_tracks={len(subtitle_streams)}",
        flush=True,
    )
    return info


def encode_if_needed(source, english, persian, output, width, height):
    if output.exists():
        try:
            validate_output(output, width, height)
            return
        except Exception as exc:
            print(f"Existing output is invalid and will be rebuilt: {exc}", flush=True)
            output.unlink(missing_ok=True)

    temporary = output.with_name(output.stem + ".partial.mkv")
    temporary.unlink(missing_ok=True)
    try:
        run([
            "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
            "-i", str(source), "-i", str(english), "-i", str(persian),
            "-map", "0:v:0", "-map", "0:a?", "-map", "1:0", "-map", "2:0",
            "-vf",
            f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
            "-c:v", "libx265", "-preset", PRESET, "-crf", CRF,
            "-pix_fmt", "yuv420p",
            "-x265-params", "log-level=error",
            "-c:a", "aac", "-b:a", "96k",
            "-c:s", "srt",
            "-metadata", "title=Morevna Episode 3.0.2 - " + output.stem.split("_")[-2],
            "-metadata:s:s:0", "language=eng", "-metadata:s:s:0", "title=English",
            "-metadata:s:s:1", "language=fas", "-metadata:s:s:1", "title=فارسی",
            "-disposition:s:0", "default", "-disposition:s:1", "0",
            str(temporary),
        ])
        # ffmpeg can exit successfully yet leave a truncated or unusable file; probe before rename.
        validate_output(temporary, width, height)
        temporary.replace(output)
        validate_output(output, width, height)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    api = os.environ.get("TELEGRAM_API_BASE_URL", "http://127.0.0.1:8081").rstrip("/")
    sample_version = os.environ.get("SAMPLE_VERSION", "v1").strip()
    requested_qualities = [
        quality.strip() for quality in
        os.environ.get("SAMPLE_QUALITIES", "480p,720p,1080p").split(",")
        if quality.strip()
    ]
    if not token or not chat:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID secret.")
    if not sample_version or any(not q for q in requested_qualities):
        raise RuntimeError("Invalid sample version or quality list.")
    if os.environ.get("REQUIRE_GEMINI", "0").strip() == "1" and not os.environ.get("GEMINI_API_KEY", "").strip():
        raise RuntimeError("REQUIRE_GEMINI=1 but GEMINI_API_KEY is missing; refusing an unreviewed sample.")

    state = load_state()
    OUT.mkdir(parents=True, exist_ok=True)
    source = OUT / "morevna_source.webm"
    english = OUT / "english.srt"
    persian = OUT / "persian.srt"
    if not source.exists():
        fetch(VIDEO_URL, source)
    if not english.exists():
        fetch(SRT_URL, english)
    subtitle_text = english.read_text(encoding="utf-8-sig").strip()
    if not subtitle_text.startswith(("1", "WEBVTT")):
        raise RuntimeError("Downloaded English subtitle file does not look like SRT.")

    from tools.bilingual_subtitles import (
        parse_srt, render_srt, get_english_to_persian_translator, translate_cues
    )
    cues = parse_srt(subtitle_text)
    if not cues:
        raise RuntimeError("No subtitle cues were parsed from the English subtitle file.")
    if not persian.exists() or not persian.read_text(encoding="utf-8").strip():
        translator = get_english_to_persian_translator()
        translated = translate_cues(cues, translator)
        if os.environ.get("REQUIRE_GEMINI", "0").strip() == "1" and not hasattr(translator, "review_batch"):
            raise RuntimeError("A Gemini-reviewed subtitle translator is required for this sample.")
        persian.write_text(render_srt(translated), encoding="utf-8")

    source_info = probe_media(source)
    try:
        source_duration = float(source_info["format"]["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("The downloaded source has no valid duration.") from exc
    if source_duration < MIN_VALID_DURATION:
        raise RuntimeError(f"Source duration is invalid: {source_duration}s")
    print(f"Source duration: {source_duration:.2f}s; subtitle cues: {len(cues)}", flush=True)

    specs = {
        "480p": (854, 480),
        "720p": (1280, 720),
        "1080p": (1920, 1080),
    }
    unknown_qualities = sorted(set(requested_qualities) - set(specs))
    if unknown_qualities:
        raise RuntimeError(f"Unknown sample quality values: {unknown_qualities}")
    for quality, (width, height) in specs.items():
        if quality not in requested_qualities:
            continue
        receipt_key = f"{quality}_mkv_h265_{sample_version}"
        if state["sent"].get(receipt_key):
            print(f"{quality} {sample_version} already sent; skipping duplicate.")
            continue

        output = OUT / f"Morevna_Episode_3_Persian_{quality}_H265_{sample_version}.mkv"
        encode_if_needed(source, english, persian, output, width, height)
        info = validate_output(output, width, height)
        size = output.stat().st_size
        if size > 1900 * 1024 * 1024:
            raise RuntimeError(f"{quality} output is too large for this test: {size} bytes")

        caption = f"🎬 Morevna Episode 3.0.2 | {quality} | Persian subtitle reviewed\n{ATTRIBUTION}"
        with output.open("rb") as video:
            response = requests.post(
                f"{api}/bot{token}/sendDocument",
                data={"chat_id": chat, "caption": caption},
                files={"document": (output.name, video, "video/x-matroska")},
                timeout=(60, 1800),
            )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise RuntimeError(f"Telegram rejected {quality}: {result.get('description', result)}")
        message = result.get("result", {})
        state["sent"][receipt_key] = {
            "message_id": message.get("message_id"),
            "file_id": (message.get("document") or message.get("video") or {}).get("file_id"),
            "bytes": size,
            "duration_seconds": float(info["format"]["duration"]),
            "codec": "hevc",
            "crf": int(CRF),
            "preset": PRESET,
            "sent_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        save_state(state)
        print(
            f"TELEGRAM_SENT {quality} MKV_H265 version={sample_version} message_id={message.get('message_id')} "
            f"bytes={size} duration={info['format']['duration']}s",
            flush=True,
        )
    print("Delivery attempt finished.", flush=True)


if __name__ == "__main__":
    main()
