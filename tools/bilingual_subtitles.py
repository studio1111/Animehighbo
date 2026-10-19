#!/usr/bin/env python3
"""Create an MKV with selectable English and Persian subtitle tracks.

This tool processes a video file the user already has permission to modify and
redistribute. It does not download anime episodes or publish anything to Telegram.
Requires ffmpeg/ffprobe and the optional dependencies in requirements-subtitles.txt.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Cue:
    index: int
    timing: str
    text: str


def run_command(args: list[str]) -> str:
    try:
        result = subprocess.run(args, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Required command not found: {args[0]}. Install FFmpeg.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(f"Command failed: {' '.join(args[:4])} ...\n{detail[-2000:]}") from exc
    return result.stdout


def subtitle_streams(video: Path) -> list[dict]:
    raw = run_command([
        "ffprobe", "-v", "error", "-select_streams", "s",
        "-show_entries", "stream=index:stream_tags=language,title",
        "-of", "json", str(video),
    ])
    try:
        streams = json.loads(raw).get("streams", [])
    except json.JSONDecodeError as exc:
        raise RuntimeError("ffprobe returned invalid JSON while reading subtitle tracks.") from exc
    return [s for s in streams if isinstance(s, dict) and isinstance(s.get("index"), int)]


def choose_english_stream(streams: list[dict], requested: int | None) -> int:
    if requested is not None:
        for stream in streams:
            if stream["index"] == requested:
                return requested
        available = ", ".join(str(s["index"]) for s in streams) or "none"
        raise ValueError(f"Subtitle stream {requested} not found. Available global stream indexes: {available}")
    for stream in streams:
        tags = stream.get("tags") or {}
        language = str(tags.get("language", "")).lower()
        title = str(tags.get("title", "")).lower()
        if language in {"en", "eng", "english"} or "english" in title:
            return stream["index"]
    available = ", ".join(
        f"{s['index']}({(s.get('tags') or {}).get('language', 'unknown')})" for s in streams
    ) or "none"
    raise ValueError(
        "No English subtitle track was detected automatically. "
        f"Available tracks: {available}. Pass --subtitle-stream with a global stream index."
    )


def parse_srt(text: str) -> list[Cue]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", normalized.strip()):
        lines = [line.rstrip() for line in block.split("\n")]
        if not lines:
            continue
        if lines[0].strip().upper() == "WEBVTT":
            continue
        timing_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue
        timing = lines[timing_index].strip()
        body = "\n".join(lines[timing_index + 1:]).strip()
        if body:
            cues.append(Cue(len(cues) + 1, timing, body))
    if not cues:
        raise ValueError("No valid SRT subtitle cues were found.")
    return cues


def render_srt(cues: list[Cue]) -> str:
    blocks = []
    for number, cue in enumerate(cues, 1):
        blocks.append(f"{number}\n{cue.timing}\n{cue.text.strip()}")
    return "\n\n".join(blocks) + "\n"


def get_english_to_persian_translator():
    try:
        import argostranslate.package
        import argostranslate.translate
    except ImportError as exc:
        raise RuntimeError(
            "Offline translation dependency is missing. Install requirements-subtitles.txt."
        ) from exc

    languages = argostranslate.translate.get_installed_languages()
    english = next((lang for lang in languages if lang.code == "en"), None)
    persian = next((lang for lang in languages if lang.code == "fa"), None)
    if english and persian:
        translation = english.get_translation(persian)
        if translation:
            return translation

    # Install the free, open-source English -> Persian Argos model on first use.
    argostranslate.package.update_package_index()
    package = next(
        (
            item for item in argostranslate.package.get_available_packages()
            if item.from_code == "en" and item.to_code == "fa"
        ),
        None,
    )
    if package is None:
        raise RuntimeError("No English-to-Persian Argos model is currently available.")
    package_path = package.download()
    argostranslate.package.install_from_path(package_path)
    languages = argostranslate.translate.get_installed_languages()
    english = next((lang for lang in languages if lang.code == "en"), None)
    persian = next((lang for lang in languages if lang.code == "fa"), None)
    if not english or not persian:
        raise RuntimeError("English-to-Persian translation model installed, but languages are unavailable.")
    translation = english.get_translation(persian)
    if not translation:
        raise RuntimeError("English-to-Persian translation is not available after model installation.")
    return translation


def translate_cues(cues: list[Cue], translator) -> list[Cue]:
    translated: list[Cue] = []
    for cue in cues:
        # Keep common subtitle formatting tags intact while translating dialogue.
        tags: list[str] = []
        def hold_tag(match):
            tags.append(match.group(0))
            return f" ZZSUBTAG{len(tags) - 1}ZZ "
        text = re.sub(r"</?(?:i|b|u|font|span|ruby|rt)(?:\s+[^>]*)?>", hold_tag, cue.text, flags=re.I)
        try:
            result = translator.translate(text)
        except Exception as exc:
            raise RuntimeError(f"Translation failed at subtitle cue {cue.index}: {exc}") from exc
        for index, tag in enumerate(tags):
            result = result.replace(f"ZZSUBTAG{index}ZZ", tag).replace(f"ZZSUBTAG{index}ZZ".lower(), tag)
        result = result.strip()
        if not result:
            result = cue.text
        translated.append(Cue(cue.index, cue.timing, result))
    return translated


def make_bilingual_mkv(video: Path, english_srt: Path, persian_srt: Path, output: Path) -> None:
    if output.suffix.lower() != ".mkv":
        raise ValueError("Output filename must end in .mkv so both subtitle tracks are supported.")
    output.parent.mkdir(parents=True, exist_ok=True)
    run_command([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(video), "-i", str(english_srt), "-i", str(persian_srt),
        "-map", "0:v?", "-map", "0:a?", "-map", "0:d?", "-map", "0:t?",
        "-map", "1:0", "-map", "2:0", "-map_metadata", "0", "-map_chapters", "0",
        "-c", "copy",
        "-metadata:s:s:0", "language=eng",
        "-metadata:s:s:0", "title=English",
        "-metadata:s:s:1", "language=fas",
        "-metadata:s:s:1", "title=فارسی",
        "-disposition:s:0", "default", "-disposition:s:1", "0",
        str(output),
    ])


def process(video: Path, output: Path, requested_stream: int | None = None) -> tuple[Path, Path]:
    if not video.is_file():
        raise FileNotFoundError(f"Input video does not exist: {video}")
    streams = subtitle_streams(video)
    stream_index = choose_english_stream(streams, requested_stream)
    translator = get_english_to_persian_translator()

    with tempfile.TemporaryDirectory(prefix="animehighbo-subs-") as temp:
        temp_dir = Path(temp)
        english_path = temp_dir / "english.srt"
        persian_path = temp_dir / "persian.srt"
        run_command([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(video), "-map", f"0:{stream_index}", "-c:s", "srt", str(english_path),
        ])
        english_cues = parse_srt(english_path.read_text(encoding="utf-8-sig"))
        persian_cues = translate_cues(english_cues, translator)
        persian_path.write_text(render_srt(persian_cues), encoding="utf-8")
        # Keep standalone subtitle files beside the output for inspection/editing.
        english_output = output.with_name(output.stem + ".en.srt")
        persian_output = output.with_name(output.stem + ".fa.srt")
        english_output.write_text(render_srt(english_cues), encoding="utf-8")
        persian_output.write_text(render_srt(persian_cues), encoding="utf-8")
        make_bilingual_mkv(video, english_path, persian_path, output)
    return english_output, persian_output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract an English subtitle track, translate it to Persian offline, and mux both into an MKV."
    )
    parser.add_argument("video", type=Path, help="Local video file you are authorized to modify")
    parser.add_argument("--output", type=Path, help="Output MKV path (default: <video>.bilingual.mkv)")
    parser.add_argument(
        "--subtitle-stream", type=int,
        help="Global ffprobe stream index for the English subtitle; by default detect language tags",
    )
    args = parser.parse_args(argv)
    output = args.output or args.video.with_name(args.video.stem + ".bilingual.mkv")
    try:
        english, persian = process(args.video, output, args.subtitle_stream)
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"Created: {output}")
    print(f"English subtitle: {english}")
    print(f"Persian subtitle: {persian}")
    print("Note: offline machine translation is a draft; review idioms, names, and timing before redistribution.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
