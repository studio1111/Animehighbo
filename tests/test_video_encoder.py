import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.video_encoder import build_ffmpeg_command, encode_h265


def probe_json(codec="h264", duration="60.0"):
    streams = [
        {"codec_type": "video", "codec_name": codec, "width": 1280, "height": 720, "duration": duration},
        {"codec_type": "audio", "codec_name": "aac"},
        {"codec_type": "subtitle", "codec_name": "subrip"},
    ]
    return json.dumps({"format": {"duration": duration}, "streams": streams})


class VideoEncoderTests(unittest.TestCase):
    def test_h265_encoding_preserves_streams_and_validates_playable_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.mkv"
            output = root / "encoded.mkv"
            subtitle = root / "episode.fa.srt"
            source.write_bytes(b"source video bytes" * 100)
            subtitle.write_text("1\\n00:00:01,000 --> 00:00:02,000\\nسلام\\n", encoding="utf-8")
            commands = []

            def fake_run(args):
                commands.append(args)
                if args[0] == "ffprobe":
                    return probe_json("hevc" if args[-1].endswith(".part.mkv") else "h264", "59.5" if args[-1].endswith(".part.mkv") else "60.0")
                if args[0] == "ffmpeg" and args[-1] == "-":
                    return ""
                if args[0] == "ffmpeg":
                    Path(args[-1]).write_bytes(b"encoded" * 400)
                    return ""
                raise AssertionError(args[0])

            with patch("tools.video_encoder.run_command", side_effect=fake_run):
                result = encode_h265(source, output, crf=24, preset="slow", burn_subtitle=subtitle)

            self.assertEqual(result, output)
            self.assertTrue(output.is_file())
            ffmpeg = next(command for command in commands if command[0] == "ffmpeg")
            self.assertIn("libx265", ffmpeg)
            self.assertIn("copy", ffmpeg)
            self.assertIn("PrimaryColour=&H0000FFFF", " ".join(ffmpeg))
            self.assertIn("Outline=0.5", " ".join(ffmpeg))

    def test_uses_crf_quality_with_vbv_cap_instead_of_constant_bitrate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            command = build_ffmpeg_command(
                root / "source.mkv", root / "output.mkv",
                crf=24, video_bitrate="1650k", audio_bitrate="128k",
            )
        crf_index = command.index("-crf")
        self.assertEqual(command[crf_index + 1], "24")
        self.assertNotIn("-b:v", command)
        self.assertEqual(command[command.index("-maxrate") + 1], "1650k")
        self.assertEqual(command[command.index("-bufsize") + 1], "3300k")

    def test_uses_controlled_video_and_audio_bitrates_and_resizes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.mkv"
            source.write_bytes(b"x" * 1200)
            command = build_ffmpeg_command(
                source, root / "output.mkv",
                video_bitrate="1800k", audio_bitrate="128k", target_height=1080,
            )
        self.assertIn("1800k", command)
        self.assertIn("128k", command)
        self.assertIn("aac", command)
        self.assertIn("scale=-2:1080:flags=lanczos", command)

    def test_rejects_unsupported_output_height(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(ValueError, "Target height"):
                build_ffmpeg_command(root / "source.mkv", root / "output.mkv", target_height=360)

    def test_rejects_invalid_crf_and_preset(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.mkv"
            source.write_bytes(b"x" * 1200)
            with self.assertRaisesRegex(ValueError, "CRF"):
                build_ffmpeg_command(source, root / "output.mkv", crf=40)
            with self.assertRaisesRegex(ValueError, "preset"):
                build_ffmpeg_command(source, root / "output.mkv", preset="invalid")

    def test_burn_in_requires_existing_persian_subtitle(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(FileNotFoundError):
                build_ffmpeg_command(root / "source.mkv", root / "output.mkv", burn_subtitle=root / "missing.fa.srt")

    def test_requires_mkv_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(ValueError, "mkv"):
                build_ffmpeg_command(root / "source.mp4", root / "output.mp4")


if __name__ == "__main__":
    unittest.main()
