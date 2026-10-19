#!/usr/bin/env python3
"""One-time licensed Morevna test delivery to Telegram in three resolutions."""
from __future__ import annotations
import json, os, subprocess, time, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import requests

VIDEO_URL = "https://commons.wikimedia.org/wiki/Special:Redirect/file/Morevna_Episode_3.0.2_(English).webm"
SRT_URL = "https://commons.wikimedia.org/wiki/TimedText:Morevna_Episode_3.0.2_(English).webm.en.srt?action=raw"
STATE = Path("data/morevna_test_delivery.json")
OUT = Path("work/morevna")
ATTRIBUTION = (
    "Morevna Episode 3.0.2 (English) by Morevna Project. "
    "Source: https://commons.wikimedia.org/wiki/File:Morevna_Episode_3.0.2_(English).webm\n"
    "License: CC BY-SA 4.0 https://creativecommons.org/licenses/by-sa/4.0/\n"
    "Changes: Persian subtitles translated from the community English subtitles; "
    "re-encoded for 480p, 720p and 1080p. Independent fan test, not endorsed by the creators."
)

def run(args):
    subprocess.run(args, check=True)

def fetch(url, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=(30, 180), headers={"User-Agent":"Animehighbo/1.0 (open-license test)"}) as r:
        r.raise_for_status()
        with path.open("wb") as f:
            for chunk in r.iter_content(1024 * 1024):
                if chunk:
                    f.write(chunk)
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
        except Exception:
            pass
    return {"sent": {}}

def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE)

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    api = os.environ.get("TELEGRAM_API_BASE_URL", "http://127.0.0.1:8081").rstrip("/")
    if not token or not chat:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID secret.")
    state = load_state()
    wanted = ["480p", "720p", "1080p"]
    if all(state["sent"].get(q) for q in wanted):
        print("All three test videos are already marked sent; no duplicate uploads.")
        return
    OUT.mkdir(parents=True, exist_ok=True)
    source = OUT / "morevna_source.webm"
    english = OUT / "english.srt"
    persian = OUT / "persian.srt"
    if not source.exists():
        fetch(VIDEO_URL, source)
    if not english.exists():
        fetch(SRT_URL, english)
    if not english.read_text(encoding="utf-8-sig").strip().startswith(("1", "WEBVTT")):
        raise RuntimeError("Downloaded English subtitle file does not look like SRT.")
    # Reuse the project's existing offline Argos translation implementation.
    from tools.bilingual_subtitles import parse_srt, render_srt, get_english_to_persian_translator, translate_cues
    cues = parse_srt(english.read_text(encoding="utf-8-sig"))
    if not persian.exists():
        translator = get_english_to_persian_translator()
        translated = translate_cues(cues, translator)
        persian.write_text(render_srt(translated), encoding="utf-8")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(source)],
        check=True, capture_output=True, text=True
    )
    print(f"Source duration: {probe.stdout.strip()} seconds; subtitle cues: {len(cues)}", flush=True)
    specs = {
        "480p": (854, 480, "1200k"),
        "720p": (1280, 720, "2500k"),
        "1080p": (1920, 1080, "4500k"),
    }
    for quality, (width, height, bitrate) in specs.items():
        if state["sent"].get(quality):
            print(f"{quality} already sent; skipping.")
            continue
        output = OUT / f"Morevna_Episode_3_Persian_{quality}.mp4"
        if not output.exists():
            run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(source), "-i", str(english), "-i", str(persian),
                "-map", "0:v:0", "-map", "0:a?", "-map", "1:0", "-map", "2:0",
                "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
                "-c:v", "libx264", "-preset", "veryfast", "-b:v", bitrate, "-maxrate", bitrate,
                "-bufsize", str(int(bitrate[:-1]) * 2) + "k",
                "-c:a", "aac", "-b:a", "128k", "-c:s", "mov_text",
                "-metadata", "title=Morevna Episode 3.0.2 - " + quality,
                "-metadata:s:s:0", "language=eng", "-metadata:s:s:0", "title=English",
                "-metadata:s:s:1", "language=fas", "-metadata:s:s:1", "title=فارسی",
                "-disposition:s:0", "default", "-disposition:s:1", "0",
                str(output)
            ])
        size = output.stat().st_size
        if size > 1900 * 1024 * 1024:
            raise RuntimeError(f"{quality} output is too large for this test: {size} bytes")
        caption = f"🎬 Morevna Episode 3.0.2 | {quality}\n{ATTRIBUTION}"
        with output.open("rb") as video:
            response = requests.post(
                f"{api}/bot{token}/sendVideo",
                data={"chat_id": chat, "caption": caption, "supports_streaming": "true"},
                files={"video": (output.name, video, "video/mp4")},
                timeout=(60, 1800),
            )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise RuntimeError(f"Telegram rejected {quality}: {result.get('description', result)}")
        msg = result.get("result", {})
        state["sent"][quality] = {
            "message_id": msg.get("message_id"),
            "file_id": (msg.get("video") or {}).get("file_id"),
            "bytes": size,
            "sent_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        save_state(state)
        print(f"TELEGRAM_SENT {quality} message_id={msg.get('message_id')} bytes={size}", flush=True)
    print("Delivery attempt finished.", flush=True)

if __name__ == "__main__":
    main()
