import os
import unittest
from unittest.mock import patch, Mock

import requests

from tools.bilingual_subtitles import (
    Cue,
    GeminiSubtitleTranslator,
    get_english_to_persian_translator,
    choose_english_stream,
    parse_srt,
    render_srt,
    render_ass,
    translate_cues,
)


class SubtitleUtilityTests(unittest.TestCase):
    def test_parse_and_render_srt_preserves_timing_and_dialogue(self):
        source = """1
00:00:01,000 --> 00:00:02,500
Hello there.

2
00:00:03,000 --> 00:00:04,000
<i>Run!</i>
"""
        cues = parse_srt(source)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0].timing, "00:00:01,000 --> 00:00:02,500")
        self.assertEqual(cues[1].text, "<i>Run!</i>")
        rendered = render_srt(cues)
        self.assertIn("00:00:03,000 --> 00:00:04,000", rendered)
        self.assertIn("<i>Run!</i>", rendered)


    def test_persian_ass_track_is_soft_subtitle_with_yellow_text_and_thin_black_outline(self):
        cues = [Cue(1, "00:00:01,000 --> 00:00:02,500", "سلام دنیا")]
        rendered = render_ass(cues)
        self.assertIn("[V4+ Styles]", rendered)
        self.assertIn("PrimaryColour,&H0000FFFF", rendered)
        self.assertIn("OutlineColour,&H00000000", rendered)
        self.assertIn("Persian,Noto Sans Arabic", rendered)
        self.assertIn("Dialogue: 0,0:00:01.00,0:00:02.50,Persian", rendered)
        self.assertIn("سلام دنیا", rendered)

    def test_parser_ignores_invalid_blocks_and_rejects_empty_file(self):
        self.assertEqual(len(parse_srt("header\n\n1\n00:00:01,000 --> 00:00:02,000\nHi")), 1)
        with self.assertRaises(ValueError):
            parse_srt("this is not a subtitle")

    def test_auto_selects_english_subtitle_using_language_metadata(self):
        streams = [
            {"index": 2, "tags": {"language": "jpn"}},
            {"index": 4, "tags": {"language": "eng"}},
        ]
        self.assertEqual(choose_english_stream(streams, None), 4)
        self.assertEqual(choose_english_stream(streams, 2), 2)

    def test_missing_english_track_produces_actionable_error(self):
        with self.assertRaisesRegex(ValueError, "No English text subtitle track"):
            choose_english_stream([{"index": 2, "tags": {"language": "jpn"}}], None)

    def test_batch_translation_runs_review_and_preserves_placeholders(self):
        class FakeTranslator:
            def translate_batch(self, cues):
                return [Cue(c.index, c.timing, c.text.replace("Hello", "سلام")) for c in cues]

            def review_batch(self, source, translated):
                self.review_called = True
                return [Cue(c.index, c.timing, c.text.replace("سلام", "درود")) for c in translated]

        translator = FakeTranslator()
        cues = [Cue(1, "00:00:01,000 --> 00:00:02,000", "<i>Hello</i>")]
        translated = translate_cues(cues, translator)
        self.assertTrue(translator.review_called)
        self.assertEqual(translated[0].timing, cues[0].timing)
        self.assertIn("<i>", translated[0].text)
        self.assertIn("درود", translated[0].text)

    def test_translation_preserves_cue_timing_and_formatting_tags(self):
        class FakeTranslator:
            def translate(self, text):
                return text.replace("Hello", "سلام").replace("Run", "بدو")

        cues = [Cue(1, "00:00:01,000 --> 00:00:02,000", "<i>Hello</i>")]
        translated = translate_cues(cues, FakeTranslator())
        self.assertEqual(translated[0].timing, cues[0].timing)
        self.assertIn("<i>", translated[0].text)
        self.assertIn("سلام", translated[0].text)


class GeminiKeyRotationTests(unittest.TestCase):
    def test_translation_rotates_to_next_key_after_quota_response(self):
        limited = Mock(status_code=429)
        limited.raise_for_status.side_effect = requests.HTTPError("quota")
        success = Mock(status_code=200)
        success.json.return_value = {
            "candidates": [{"content": {"parts": [{
                "text": '{"translations":[{"id":1,"text":"سلام"}]}'
            }]}}]
        }
        translator = GeminiSubtitleTranslator(["primary", "backup"])
        cue = Cue(1, "00:00:01,000 --> 00:00:02,000", "Hello")

        with patch("tools.bilingual_subtitles.requests.post", side_effect=[limited, success]) as post, patch("tools.bilingual_subtitles.time.sleep"):
            result = translator.translate_batch([cue])

        self.assertEqual(result[0].text, "سلام")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args_list[0].kwargs["params"]["key"], "primary")
        self.assertEqual(post.call_args_list[1].kwargs["params"]["key"], "backup")

    def test_environment_loader_collects_all_configured_gemini_keys(self):
        env = {
            "GEMINI_API_KEY": "primary",
            "GEMINI_API_KEY_2": "backup-two",
            "GEMINI_API_KEY_3": "backup-three",
            "GEMINI_API_KEY_4": "backup-four",
        }
        with patch.dict(os.environ, env, clear=True):
            translator = get_english_to_persian_translator()

        self.assertIsInstance(translator, GeminiSubtitleTranslator)
        self.assertEqual(
            translator.api_keys,
            ["primary", "backup-two", "backup-three", "backup-four"],
        )

    def test_duplicate_keys_are_removed_without_reordering(self):
        translator = GeminiSubtitleTranslator(["same", "same", "next", ""])
        self.assertEqual(translator.api_keys, ["same", "next"])


if __name__ == "__main__":
    unittest.main()
