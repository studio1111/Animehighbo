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


class GeminiSubtitleTranslator:
    """Translate subtitle batches with Gemini and rotate configured API keys on quota/auth failures."""

    def __init__(self, api_key: str | list[str], model: str | None = None):
        candidates = [api_key] if isinstance(api_key, str) else list(api_key)
        self.api_keys = list(dict.fromkeys(
            key.strip() for key in candidates if isinstance(key, str) and key.strip()
        ))
        if not self.api_keys:
            raise ValueError("At least one non-empty Gemini API key is required.")
        # Keep the original attribute for compatibility with existing integrations.
        self.api_key = self.api_keys[0]
        self._key_index = 0
        self.model = (model or os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")).strip()
        self.endpoint = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )

    def _generate_json(self, body: dict, context: str) -> dict:
        """Try each configured key, retry transient errors, and never log key values."""
        errors = []
        key_count = len(self.api_keys)
        for key_offset in range(key_count):
            index = (self._key_index + key_offset) % key_count
            key = self.api_keys[index]
            for attempt in range(3):
                try:
                    response = requests.post(
                        self.endpoint,
                        params={"key": key},
                        json=body,
                        timeout=(20, 120),
                    )
                except requests.RequestException as exc:
                    errors.append(f"key {index + 1}: network error")
                    if attempt < 2:
                        time.sleep(2 ** attempt)
                        continue
                    break

                # Quota exhaustion and key-level authorization failures can be
                # isolated to one key, so try the next configured key.
                if response.status_code in {401, 403, 429}:
                    errors.append(f"key {index + 1}: HTTP {response.status_code}")
                    break
                if response.status_code in {500, 502, 503, 504}:
                    errors.append(f"key {index + 1}: HTTP {response.status_code}")
                    if attempt < 2:
                        time.sleep(2 ** attempt)
                        continue
                    break
                try:
                    response.raise_for_status()
                except requests.RequestException as exc:
                    # Other 4xx responses usually indicate a malformed request
                    # or unsupported model, so rotating keys will not help.
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
                    raise RuntimeError(
                        f"Gemini returned invalid JSON during {context}."
                    ) from exc
                self._key_index = index
                return decoded

        details = ", ".join(errors) if errors else "no usable API key"
        raise RuntimeError(
            f"All {key_count} configured Gemini API key(s) failed during {context} "
            f"({details}). The subtitle batch remains incomplete and must not be published."
        )

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
    key_names = (
        "GEMINI_API_KEY",
        "GEMINI_API_KEY_1",
        "GEMINI_API_KEY_2",
        "GEMINI_API_KEY_3",
        "GEMINI_API_KEY_4",
    )
    api_keys = list(dict.fromkeys(
        os.environ.get(name, "").strip()
        for name in key_names
        if os.environ.get(name, "").strip()
    ))
    if api_keys:
        print(
            f"Subtitle translator: Gemini ({os.environ.get('GEMINI_MODEL', 'gemini-3.5-flash-lite')}); "
            f"{len(api_keys)} API key(s) configured",
            flush=True,
        )
        return GeminiSubtitleTranslator(api_keys)

    # Optional offline fallback for local runs without a Gemini secret.
    try:
        import argostranslate.package
        import argostranslate.translate
    except ImportError as exc:
        raise RuntimeError(
            "No Gemini API key is configured and the offline Argos dependency is missing."
        ) from exc

    languages = argostranslate.translate.get_installed_languages()
    english = next((lang for lang in languages if lang.code == "en"), None)
    persian = next((lang for lang in languages if lang.code == "fa"), None)
    if english and persian:
        translation = english.get_translation(persian)
        if translation:
            print("Subtitle translator: offline Argos fallback", flush=True)
            return translation

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
    print("Subtitle translator: offline Argos fallback", flush=True)
    return translation

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

def make_bilingual_mkv(video: Path, english_srt: Path, persian_srt: Path, output: Path) -> None:
    if output.suffix.lower() != ".mkv":
        raise ValueError("Output filename must end in .mkv so both subtitle tracks are supported.")
    output.parent.mkdir(parents=True, exist_ok=True)
    run_command([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(video), "-i", str(english_srt), "-i", str(persian_srt),
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
