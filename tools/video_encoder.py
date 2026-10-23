"""Encode authorized local media to validated H.265 MKV, optionally burning Persian SRT."""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

LOG = logging.getLogger("animehighbo.video_encoder")
PRESETS = {
    "ultrafast", "superfast", "veryfast", "faster", "fast",
    "medium", "slow", "slower", "veryslow",
}
PERSIAN_SUBTITLE_STYLE = (
    "FontName=Noto Sans Arabic,FontSize=24,"
    "PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,"
    "BorderStyle=1,Outline=0.5,Shadow=0"
)


def _run_with_progress(args: list[str]) -> str:
    """Stream FFmpeg's machine-readable progress so long encodes never look silent."""
    try:
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"Required command not found: {args[0]}. Install FFmpeg.") from exc

    output: list[str] = []
    progress: dict[str, str] = {}
    try:
        for raw_line in process.stdout or ():
            line = raw_line.strip()
            if not line:
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                progress[key] = value
                if key == "progress":
                    LOG.info(
                        "FFmpeg progress: output_time=%s speed=%s frame=%s status=%s",
                        progress.get("out_time", "unknown"),
                        progress.get("speed", "unknown"),
                        progress.get("frame", "unknown"),
                        value,
                    )
                    progress.clear()
            else:
                output.append(line)
        return_code = process.wait()
    except BaseException:
        process.kill()
        process.wait()
        raise

    if return_code != 0:
        detail = "\\n".join(output).strip() or f"process exited with code {return_code}"
        raise RuntimeError(f"Command failed: {' '.join(args[:4])} ...\\n{detail[-2000:]}")
    return "\\n".join(output)


def run_command(args: list[str]) -> str:
    if "-progress" in args and "pipe:1" in args:
        return _run_with_progress(args)
    try:
        result = subprocess.run(args, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Required command not found: {args[0]}. Install FFmpeg.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(f"Command failed: {' '.join(args[:4])} ...\\n{detail[-2000:]}") from exc
    return result.stdout


def probe_media(path: Path) -> dict:
    raw = run_command([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration:stream=codec_type,codec_name,width,height,duration",
        "-of", "json", str(path),
    ])
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe returned invalid JSON for {path.name}.") from exc
    streams = data.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if not video:
        raise ValueError(f"No video stream found in {path.name}.")
    format_info = data.get("format") or {}
    duration_value = format_info.get("duration") or video.get("duration")
    try:
        duration = float(duration_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Could not determine video duration for {path.name}.") from exc
    if duration <= 0:
        raise ValueError(f"Invalid video duration for {path.name}.")
    return {"duration": duration, "streams": streams, "video": video}


def build_ffmpeg_command(
    input_path: Path,
    output_path: Path,
    crf: int = 24,
    preset: str = "medium",
    burn_subtitle: Path | None = None,
    video_bitrate: str = "1800k",
    audio_bitrate: str = "128k",
    target_height: int | None = None,
) -> list[str]:
    if not 18 <= crf <= 28:
        raise ValueError("H.265 CRF must be between 18 and 28.")
    if preset not in PRESETS:
        raise ValueError(f"Unsupported H.265 preset: {preset}")
    if output_path.suffix.lower() != ".mkv":
        raise ValueError("H.265 output must use the .mkv container.")
    if target_height is not None and target_height not in {480, 720, 1080}:
        raise ValueError("Target height must be 480, 720, 1080, or None.")
    if not video_bitrate.endswith("k") or not audio_bitrate.endswith("k"):
        raise ValueError("Video and audio bitrates must use the 'k' suffix.")
    args = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-progress", "pipe:1", "-stats_period", "30", "-y",
        "-i", str(input_path),
        "-map", "0:v:0", "-map", "0:a?", "-map", "0:s?",
        "-map_metadata", "0", "-map_chapters", "0",
    ]
    filters = []
    if target_height is not None:
        filters.append(f"scale=-2:{target_height}:flags=lanczos")
    if burn_subtitle is not None:
        if not burn_subtitle.is_file():
            raise FileNotFoundError(f"Persian subtitle file not found: {burn_subtitle}")
        subtitle_path = burn_subtitle.resolve().as_posix()
        subtitle_path = subtitle_path.replace("\\", "\\\\").replace(":", "\\:")
        filters.append(f"subtitles=filename='{subtitle_path}':force_style='{PERSIAN_SUBTITLE_STYLE}'")
    if filters:
        args.extend(["-vf", ",".join(filters)])
    args.extend([
        "-c:v", "libx265", "-preset", preset, "-crf", str(crf),
        # CRF controls perceptual quality; VBV caps peak bitrate without forcing
        # a constant average bitrate that can waste space on simple anime scenes.
        "-maxrate", video_bitrate, "-bufsize", str(int(video_bitrate[:-1]) * 2) + "k",
        "-c:a", "aac", "-b:a", audio_bitrate, "-c:s", "copy", "-max_muxing_queue_size", "2048",
        str(output_path),
    ])
    return args



def build_ffmpeg_multi_command(input_path: Path, output_paths: dict[str, Path], crf: int = 24,
    preset: str = "medium", burn_subtitle: Path | None = None,
    video_bitrates: dict[str, str] | None = None, audio_bitrate: str = "128k",
    target_heights: dict[str, int] | None = None) -> list[str]:
    """Build one FFmpeg command to decode once and encode all three qualities."""
    required = ("1080p", "720p", "480p")
    if set(output_paths) != set(required):
        raise ValueError("Multi-output encoding requires exactly 1080p, 720p, and 480p paths.")
    if not 18 <= crf <= 28:
        raise ValueError("H.265 CRF must be between 18 and 28.")
    if preset not in PRESETS:
        raise ValueError(f"Unsupported H.265 preset: {preset}")
    if not audio_bitrate.endswith("k"):
        raise ValueError("Audio bitrate must use the 'k' suffix.")
    video_bitrates = video_bitrates or {"1080p": "1800k", "720p": "1000k", "480p": "550k"}
    target_heights = target_heights or {"1080p": 1080, "720p": 720, "480p": 480}
    for quality in required:
        if Path(output_paths[quality]).suffix.lower() != ".mkv":
            raise ValueError("H.265 outputs must use the .mkv container.")
        if target_heights.get(quality) != int(quality[:-1]):
            raise ValueError(f"Target height for {quality} is invalid.")
        if not str(video_bitrates[quality]).endswith("k"):
            raise ValueError("Video bitrate must use the 'k' suffix.")
    if burn_subtitle is not None and not Path(burn_subtitle).is_file():
        raise FileNotFoundError(f"Persian subtitle file not found: {burn_subtitle}")
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error",
            "-progress", "pipe:1", "-stats_period", "30", "-y", "-i", str(input_path)]
    graph = "[0:v:0]split=3[v1080src][v720src][v480src];"
    for quality, label in zip(required, ("v1080src", "v720src", "v480src")):
        filters = [f"scale=-2:{target_heights[quality]}:flags=lanczos"]
        if burn_subtitle is not None:
            subtitle_path = Path(burn_subtitle).resolve().as_posix()
            subtitle_path = subtitle_path.replace("\\", "\\\\").replace(":", "\\:")
            filters.append(f"subtitles=filename='{subtitle_path}':force_style='{PERSIAN_SUBTITLE_STYLE}'")
        graph += f"[{label}]" + ",".join(filters) + f"[{quality}out];"
    args.extend(["-filter_complex", graph])
    for quality in required:
        args.extend([
            "-map", f"[{quality}out]", "-map", "0:a?", "-map", "0:s?",
            "-map_metadata", "0", "-map_chapters", "0",
            "-c:v", "libx265", "-preset", preset, "-crf", str(crf),
            "-maxrate", str(video_bitrates[quality]),
            "-bufsize", str(int(str(video_bitrates[quality])[:-1]) * 2) + "k",
            "-c:a", "aac", "-b:a", audio_bitrate, "-c:s", "copy",
            "-max_muxing_queue_size", "2048", str(output_paths[quality]),
        ])
    return args


def encode_h265_variants(input_path: Path, output_paths: dict[str, Path], crf: int = 24,
    preset: str = "medium", burn_subtitle: Path | None = None,
    video_bitrates: dict[str, str] | None = None, audio_bitrate: str = "128k",
    target_heights: dict[str, int] | None = None) -> dict[str, Path]:
    """Encode all three outputs in one FFmpeg process and validate each output."""
    input_path = Path(input_path)
    output_paths = {q: Path(p) for q, p in output_paths.items()}
    if not input_path.is_file():
        raise FileNotFoundError(f"Input video not found: {input_path}")
    if any(input_path.resolve() == p.resolve() for p in output_paths.values()):
        raise ValueError("Input and output paths must be different.")
    source_info = probe_media(input_path)
    for p in output_paths.values():
        p.parent.mkdir(parents=True, exist_ok=True)
    temporary = {q: p.with_name(f".{p.stem}.part.mkv") for q, p in output_paths.items()}
    for p in temporary.values():
        p.unlink(missing_ok=True)
    try:
        run_command(build_ffmpeg_multi_command(input_path, temporary, crf, preset, burn_subtitle,
                                               video_bitrates, audio_bitrate, target_heights))
        for quality, temp_path in temporary.items():
            if not temp_path.is_file() or temp_path.stat().st_size < 1024:
                raise RuntimeError(f"FFmpeg did not produce a valid-sized {quality} output.")
            info = probe_media(temp_path)
            if str(info["video"].get("codec_name", "")).lower() not in {"hevc", "h265"}:
                raise RuntimeError(f"{quality} output is not HEVC/H.265.")
            if abs(source_info["duration"] - info["duration"]) > max(2.0, source_info["duration"] * 0.03):
                raise RuntimeError(f"{quality} output duration differs too much from the source.")
            run_command(["ffmpeg", "-hide_banner", "-v", "error", "-xerror", "-i", str(temp_path),
                         "-map", "0:v:0", "-map", "0:a?", "-f", "null", "-"])
            for kind in ("audio", "subtitle"):
                src_count = sum(s.get("codec_type") == kind for s in source_info["streams"])
                out_count = sum(s.get("codec_type") == kind for s in info["streams"])
                if out_count < src_count:
                    raise RuntimeError(f"{quality} output lost {kind} stream(s).")
            expected = (target_heights or {"1080p": 1080, "720p": 720, "480p": 480})[quality]
            if int(info["video"].get("height", 0)) != expected:
                raise RuntimeError(f"{quality} output height is not {expected}p.")
        for quality, temp_path in temporary.items():
            temp_path.replace(output_paths[quality])
        return output_paths
    except Exception:
        for p in temporary.values():
            p.unlink(missing_ok=True)
        raise

def encode_h265(
    input_path: Path,
    output_path: Path,
    crf: int = 24,
    preset: str = "medium",
    burn_subtitle: Path | None = None,
    video_bitrate: str = "1800k",
    audio_bitrate: str = "128k",
    target_height: int | None = None,
) -> Path:
    """Encode the first video stream, preserve audio/subtitle streams, then validate the result."""
    input_path = Path(input_path)
    output_path = Path(output_path)
    if not input_path.is_file():
        raise FileNotFoundError(f"Input video not found: {input_path}")
    if input_path.resolve() == output_path.resolve():
        raise ValueError("Input and output paths must be different.")
    if burn_subtitle is not None and not Path(burn_subtitle).is_file():
        raise FileNotFoundError(f"Persian subtitle file not found: {burn_subtitle}")

    source_info = probe_media(input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(f".{output_path.stem}.part.mkv")
    temporary_path.unlink(missing_ok=True)
    try:
        command = build_ffmpeg_command(input_path, temporary_path, crf, preset, burn_subtitle, video_bitrate, audio_bitrate, target_height)
        run_command(command)
        if not temporary_path.is_file() or temporary_path.stat().st_size < 1024:
            raise RuntimeError("FFmpeg did not produce a valid-sized MKV output.")
        output_info = probe_media(temporary_path)
        if str(output_info["video"].get("codec_name", "")).lower() not in {"hevc", "h265"}:
            raise RuntimeError("Encoded output is not HEVC/H.265.")
        tolerance = max(2.0, source_info["duration"] * 0.03)
        if abs(source_info["duration"] - output_info["duration"]) > tolerance:
            raise RuntimeError("Encoded output duration differs too much from the source.")
        # ffprobe validates the container and streams; a full decode catches truncated/corrupt frames.
        run_command([
            "ffmpeg", "-hide_banner", "-v", "error", "-xerror",
            "-i", str(temporary_path), "-map", "0:v:0", "-map", "0:a?",
            "-f", "null", "-",
        ])
        for stream_type in ("audio", "subtitle"):
            source_count = sum(stream.get("codec_type") == stream_type for stream in source_info["streams"])
            output_count = sum(stream.get("codec_type") == stream_type for stream in output_info["streams"])
            if output_count < source_count:
                raise RuntimeError(f"Encoded output lost {stream_type} stream(s).")
        source_video = source_info["video"]
        output_video = output_info["video"]
        if target_height is None:
            for dimension in ("width", "height"):
                if source_video.get(dimension) and output_video.get(dimension) != source_video[dimension]:
                    raise RuntimeError(f"Encoded output changed video {dimension}.")
        elif int(output_video.get("height", 0)) != target_height:
            raise RuntimeError(f"Encoded output height is not the requested {target_height}p.")
        temporary_path.replace(output_path)
        if output_path.stat().st_size >= input_path.stat().st_size:
            LOG.warning("H.265 output is not smaller than its input; source may already be highly compressed.")
        LOG.info("Validated H.265 MKV: %s (%.1f MB)", output_path.name, output_path.stat().st_size / 1048576)
        return output_path
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
