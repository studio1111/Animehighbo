#!/usr/bin/env python3
"""Create an MKV with selectable English and Persian subtitle tracks.

This tool processes a video file the user already has permission to modify and
redistribute. It does not download anime episodes or publish anything to Telegram.
Requires ffmpeg/ffprobe and the optional dependencies in requirements-subtitles.txt.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import requests
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
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
    text_codecs = {"subrip", "ass", "ssa", "webvtt", "mov_text", "text", "ttml"}
    if requested is not None:
        for stream in streams:
            if stream["index"] == requested:
                codec = str(stream.get("codec_name", "")).lower()
                if codec and codec not in text_codecs:
                    raise ValueError(
                        f"Subtitle stream {requested} uses {codec}, which is not a text subtitle. "
                        "Bitmap subtitles need OCR first; this tool does not perform OCR."
                    )
                return requested
        available = ", ".join(str(s["index"]) for s in streams) or "none"
        raise ValueError(f"Subtitle stream {requested} not found. Available global stream indexes: {available}")
    for stream in streams:
        tags = stream.get("tags") or {}
        language = str(tags.get("language", "")).lower()
        title = str(tags.get("title", "")).lower()
        codec = str(stream.get("codec_name", "")).lower()
        if (language in {"en", "eng", "english"} or "english" in title) and (
            not codec or codec in text_codecs
        ):
            return stream["index"]
    available = ", ".join(
        f"{s['index']}({(s.get('tags') or {}).get('language', 'unknown')},"
        f"{s.get('codec_name', 'unknown')})" for s in streams
    ) or "none"
    raise ValueError(
        "No English text subtitle track was detected automatically. "
        f"Available tracks: {available}. Bitmap subtitles need OCR first."
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


def _srt_timestamp_to_ass(value: str) -> str:
    match = re.fullmatch(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})", value.strip())
    if not match:
        raise ValueError(f"Invalid SRT timestamp: {value!r}")
    hours, minutes, seconds, milliseconds = (int(part) for part in match.groups())
    return f"{hours}:{minutes:02d}:{seconds:02d}.{milliseconds // 10:02d}"


def render_ass(cues: list[Cue]) -> str:
    """Render Persian cues as a selectable ASS track with yellow text and a thin black outline."""
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "PlayResX: 1920",
        "PlayResY: 1080",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Persian,Noto Sans Arabic,42,&H0000FFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,1.5,0.0,2,40,40,42,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for cue in cues:
        start_end = cue.timing.split("-->")
        if len(start_end) != 2:
            raise ValueError(f"Invalid SRT cue timing: {cue.timing!r}")
        start = _srt_timestamp_to_ass(start_end[0])
        end = _srt_timestamp_to_ass(start_end[1])
        text = cue.text.strip().replace("\r\n", "\n").replace("\n", r"\N")
        for source, replacement in (
            ("<i>", r"{\i1}"), ("</i>", r"{\i0}"),
            ("<b>", r"{\b1}"), ("</b>", r"{\b0}"),
            ("<u>", r"{\u1}"), ("</u>", r"{\u0}"),
        ):
            text = re.sub(re.escape(source), lambda _match, value=replacement: value, text, flags=re.I)
        text = re.sub(r"</?(?:font|span|ruby|rt)(?:\s+[^>]*)?>", "", text, flags=re.I)
        text = re.sub(r"<br\s*/?>", lambda _match: r"\N", text, flags=re.I)
        lines.append(f"Dialogue: 0,{start},{end},Persian,,0,0,0,,{text}")
    return "\n".join(lines) + "\n"


class GeminiSubtitleTranslator:
    """Translate subtitle batches with one Gemini key and bounded quota retries."""

    def __init__(self, api_key: str, model: str | None = None):
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("GEMINI_API_KEY is required.")
        self.api_key = api_key.strip()
        self.api_keys = [self.api_key]
        self.model = (model or os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")).strip()
        self.endpoint = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )

    def _generate_json(self, body: dict, context: str) -> dict:
        """Retry transient errors with the same key; never rotate or log credentials."""
        errors = []
        for attempt in range(3):
            try:
                response = requests.post(
                    self.endpoint,
                    params={"key": self.api_key},
                    json=body,
                    timeout=(20, 120),
                )
            except requests.RequestException:
                errors.append("network error")
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                break

            if response.status_code == 429 or response.status_code in {500, 502, 503, 504}:
                errors.append(f"HTTP {response.status_code}")
                if attempt < 2:
                    if response.status_code == 429:
                        retry_after = response.headers.get("Retry-After", "")
                        try:
                            delay = min(60.0, max(0.0, float(retry_after)))
                        except (TypeError, ValueError):
                            try:
                                retry_time = parsedate_to_datetime(retry_after)
                                if retry_time.tzinfo is None:
                                    retry_time = retry_time.replace(tzinfo=timezone.utc)
                                delay = min(
                                    60.0,
                                    max(0.0, (retry_time - datetime.now(timezone.utc)).total_seconds()),
                                )
                            except (TypeError, ValueError, OverflowError):
                                delay = min(30.0, 2.0 ** (attempt + 1))
                    else:
                        delay = min(30.0, 2.0 ** attempt)
                    time.sleep(delay)
                    continue
                break

            try:
                response.raise_for_status()
            except requests.RequestException as exc:
                raise RuntimeError(
                    f"Gemini request failed during {context} (HTTP {response.status_code})."
                ) from exc

            try:
                data = response.json()
                parts = data["candidates"][0]["content"]["parts"]
                raw_text = "".join(part.get("text", "") for part in parts)
                decoded = json.loads(raw_text)
                if not isinstance(decoded, dict):
                    raise ValueError("Expected a JSON object.")
            except (ValueError, KeyError, TypeError, IndexError) as exc:
                raise RuntimeError(f"Gemini returned invalid JSON during {context}.") from exc
            return decoded

        detail = self._safe_api_error(response) if "response" in locals() and response.status_code == 429 else ""
        suffix = f" ({detail})" if detail else ""
        raise RuntimeError(
            f"Gemini failed during {context} after bounded retries ({', '.join(errors) or 'no response'}). "
            f"The subtitle batch remains incomplete and must not be published.{suffix}"
        )

    @staticmethod
    def _safe_api_error(response) -> str:
        """Return a short provider error summary without request credentials."""
        try:
            payload = response.json()
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            message = str(error.get("message", "")).strip()
            details = error.get("details", [])
            quota_labels = []
            if isinstance(details, list):
                for detail in details:
                    if not isinstance(detail, dict):
                        continue
                    for violation in detail.get("violations", []) if isinstance(detail.get("violations", []), list) else []:
                        if isinstance(violation, dict):
                            label = " / ".join(
                                str(violation.get(key, "")).strip()
                                for key in ("quotaMetric", "quotaId", "description")
                                if violation.get(key)
                            )
                            if label:
                                quota_labels.append(label)
            summary = "; ".join([message, *quota_labels]).strip("; ")
            return summary[:500] or "provider did not include quota details"
        except (ValueError, TypeError, AttributeError):
            return "provider did not include readable quota details"

    @staticmethod
    def _translation_body(prompt: str, temperature: float) -> dict:
        return {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "OBJECT",
                    "properties": {
                        "translations": {
                            "type": "ARRAY",
                            "items": {
                                "type": "OBJECT",
                                "properties": {
                                    "id": {"type": "INTEGER"},
                                    "text": {"type": "STRING"}
                                },
                                "required": ["id", "text"]
                            }
                        }
                    },
                    "required": ["translations"]
                }
            }
        }

    @staticmethod
    def _index_translations(decoded: dict, expected: set[int], context: str) -> dict[int, str]:
        try:
            items = decoded["translations"]
            by_id = {int(item["id"]): str(item["text"]).strip() for item in items}
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError(f"Gemini returned malformed translations during {context}.") from exc
        if set(by_id) != expected or any(not text for text in by_id.values()):
            raise RuntimeError(
                f"Gemini omitted, duplicated, or emptied subtitle cues during {context}; "
                "refusing a partial translation."
            )
        return by_id

    def translate_batch(self, cues: list[Cue]) -> list[Cue]:
        if not cues:
            return []
        translated: list[Cue] = []
        batch_size = 20
        for offset in range(0, len(cues), batch_size):
            batch = cues[offset:offset + batch_size]
            context = f"translation cues {batch[0].index}-{batch[-1].index}"
            payload = [{"id": cue.index, "text": cue.text} for cue in batch]
            prompt = (
                "You are a professional Persian anime subtitle localizer. Translate the English "
                "dialogue into natural, readable contemporary Persian with conversational phrasing. "
                "Preserve meaning, emotion, humor, character voice, names, and subtitle formatting "
                "tags such as <i>...</i>. Do not translate or alter placeholder tokens. Do not add "
                "explanations, omit dialogue, or merge cues. Keep every input id exactly once and "
                "return only JSON matching the requested schema. Prefer concise Persian suitable "
                "for on-screen subtitles. The source cues are in chronological order.\n\n"
                + json.dumps(payload, ensure_ascii=False)
            )
            body = self._translation_body(prompt, 0.2)
            body["systemInstruction"] = {
                "parts": [{
                    "text": "Return faithful, idiomatic Persian subtitle translations. "
                            "Output must contain exactly one translation for each supplied id."
                }]
            }
            decoded = self._generate_json(body, context)
            by_id = self._index_translations(decoded, {cue.index for cue in batch}, context)
            translated.extend(Cue(cue.index, cue.timing, by_id[cue.index]) for cue in batch)
        return translated

    def review_batch(self, source_cues: list[Cue], translated_cues: list[Cue]) -> list[Cue]:
        """Run a second Gemini pass to catch obvious meaning, fluency, and omission errors."""
        if len(source_cues) != len(translated_cues):
            raise RuntimeError("Subtitle review received mismatched source and translation cue counts.")
        reviewed: list[Cue] = []
        batch_size = 20
        for offset in range(0, len(source_cues), batch_size):
            sources = source_cues[offset:offset + batch_size]
            translations = translated_cues[offset:offset + batch_size]
            context = f"review cues {sources[0].index}-{sources[-1].index}"
            payload = [
                {"id": src.index, "english": src.text, "persian": dst.text}
                for src, dst in zip(sources, translations)
            ]
            prompt = (
                "You are a senior Persian subtitle editor reviewing a draft translation. "
                "Compare each Persian line with its English source. Correct only clear meaning "
                "errors, omissions, unnatural wording, overly long subtitle phrasing, or broken "
                "format placeholders. Keep natural conversational Persian, character tone, names, "
                "and placeholders such as ZZSUBTAG0ZZ exactly unchanged. Do not add explanations. "
                "Keep every cue separate and return JSON only. If a line is already good, keep it.\n\n"
                + json.dumps(payload, ensure_ascii=False)
            )
            decoded = self._generate_json(self._translation_body(prompt, 0.1), context)
            by_id = self._index_translations(decoded, {cue.index for cue in sources}, context)
            reviewed.extend(Cue(cue.index, cue.timing, by_id[cue.index]) for cue in translations)
        return reviewed

    def translate(self, text: str) -> str:
        # Compatibility for callers that translate one line at a time.
        result = self.translate_batch([Cue(1, "00:00:00,000 --> 00:00:01,000", text)])
        return result[0].text

def get_english_to_persian_translator():
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is required; offline translation fallback is disabled.")
    print(
        f"Subtitle translator: Gemini ({os.environ.get('GEMINI_MODEL', 'gemini-3.5-flash-lite')}); one API key configured",
        flush=True,
    )
    return GeminiSubtitleTranslator(api_key)


def translate_cues(cues: list[Cue], translator) -> list[Cue]:
    # Gemini gets small chronological batches, improving consistency and reducing API calls.
    if hasattr(translator, "translate_batch"):
        tagged_cues: list[Cue] = []
        tag_maps: dict[int, list[str]] = {}
        for cue in cues:
            tags: list[str] = []
            def hold_tag(match):
                tags.append(match.group(0))
                return f" ZZSUBTAG{len(tags) - 1}ZZ "
            text = re.sub(
                r"</?(?:i|b|u|font|span|ruby|rt)(?:\s+[^>]*)?>",
                hold_tag, cue.text, flags=re.I
            )
            tag_maps[cue.index] = tags
            tagged_cues.append(Cue(cue.index, cue.timing, text))
        translated = translator.translate_batch(tagged_cues)
        if hasattr(translator, "review_batch"):
            translated = translator.review_batch(tagged_cues, translated)
        restored: list[Cue] = []
        for cue in translated:
            text = cue.text
            for index, tag in enumerate(tag_maps.get(cue.index, [])):
                text = re.sub(
                    re.escape(f"ZZSUBTAG{index}ZZ"), lambda _match: tag, text, flags=re.I
                )
            restored.append(Cue(cue.index, cue.timing, text.strip()))
        return restored

    # Compatibility with the existing offline translator.
    translated: list[Cue] = []
    for cue in cues:
        tags: list[str] = []
        def hold_tag(match):
            tags.append(match.group(0))
            return f" ZZSUBTAG{len(tags) - 1}ZZ "
        text = re.sub(
            r"</?(?:i|b|u|font|span|ruby|rt)(?:\s+[^>]*)?>",
            hold_tag, cue.text, flags=re.I
        )
        try:
            result = translator.translate(text)
        except Exception as exc:
            raise RuntimeError(f"Translation failed at subtitle cue {cue.index}: {exc}") from exc
        for index, tag in enumerate(tags):
            result = result.replace(f"ZZSUBTAG{index}ZZ", tag).replace(
                f"ZZSUBTAG{index}ZZ".lower(), tag
            )
        result = result.strip() or cue.text
        translated.append(Cue(cue.index, cue.timing, result))
    return translated

def make_bilingual_mkv(video: Path, english_srt: Path, persian_ass: Path, output: Path) -> None:
    if output.suffix.lower() != ".mkv":
        raise ValueError("Output filename must end in .mkv so both subtitle tracks are supported.")
    output.parent.mkdir(parents=True, exist_ok=True)
    run_command([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(video), "-i", str(english_srt), "-i", str(persian_ass),
        "-map", "0:v?", "-map", "0:a?",
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
        persian_ass = temp_dir / "persian.ass"
        run_command([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(video), "-map", f"0:{stream_index}", "-c:s", "srt", str(english_path),
        ])
        english_cues = parse_srt(english_path.read_text(encoding="utf-8-sig"))
        persian_cues = translate_cues(english_cues, translator)
        persian_path.write_text(render_srt(persian_cues), encoding="utf-8")
        persian_ass.write_text(render_ass(persian_cues), encoding="utf-8")
        # Keep standalone subtitle files beside the output for inspection/editing.
        english_output = output.with_name(output.stem + ".en.srt")
        persian_output = output.with_name(output.stem + ".fa.srt")
        persian_ass_output = output.with_name(output.stem + ".fa.ass")
        english_output.write_text(render_srt(english_cues), encoding="utf-8")
        persian_output.write_text(render_srt(persian_cues), encoding="utf-8")
        persian_ass_output.write_text(render_ass(persian_cues), encoding="utf-8")
        make_bilingual_mkv(video, english_path, persian_ass, output)
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
