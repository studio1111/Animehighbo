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
        self.assertIn("Style: Persian,Noto Sans Arabic,42,&H0000FFFF", rendered)
        self.assertIn("&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,1.5,0.0,2", rendered)
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


class GeminiSingleKeyPolicyTests(unittest.TestCase):
    def test_429_retries_the_same_key_and_honors_retry_after(self):
        limited = Mock(status_code=429, headers={"Retry-After": "2"})
        success = Mock(status_code=200)
        success.json.return_value = {
            "candidates": [{"content": {"parts": [{
                "text": '{"translations":[{"id":1,"text":"سلام"}]}'
            }]}}]
        }
        translator = GeminiSubtitleTranslator("primary")
        cue = Cue(1, "00:00:01,000 --> 00:00:02,000", "Hello")

        with patch("tools.bilingual_subtitles.requests.post", side_effect=[limited, success]) as post, patch("tools.bilingual_subtitles.time.sleep") as sleep:
            result = translator.translate_batch([cue])

        self.assertEqual(result[0].text, "سلام")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args_list[0].kwargs["params"]["key"], "primary")
        self.assertEqual(post.call_args_list[1].kwargs["params"]["key"], "primary")
        sleep.assert_called_once_with(2.0)

    def test_429_honors_http_date_retry_after_with_a_safe_cap(self):
        limited = Mock(status_code=429, headers={"Retry-After": "Wed, 21 Oct 2031 07:00:00 GMT"})
        success = Mock(status_code=200)
        success.json.return_value = {
            "candidates": [{"content": {"parts": [{
                "text": '{"translations":[{"id":1,"text":"سلام"}]}'
            }]}}]
        }
        translator = GeminiSubtitleTranslator("primary")
        cue = Cue(1, "00:00:01,000 --> 00:00:02,000", "Hello")

        with patch("tools.bilingual_subtitles.requests.post", side_effect=[limited, success]), patch(
            "tools.bilingual_subtitles.time.sleep"
        ) as sleep:
            translator.translate_batch([cue])

        sleep.assert_called_once_with(60.0)

    def test_429_exhaustion_fails_closed_without_rotating_keys(self):
        limited = Mock(status_code=429, headers={})
        limited.json.return_value = {"error": {"message": "quota exhausted"}}
        translator = GeminiSubtitleTranslator("primary")
        cue = Cue(1, "00:00:01,000 --> 00:00:02,000", "Hello")

        with patch("tools.bilingual_subtitles.requests.post", return_value=limited) as post, patch("tools.bilingual_subtitles.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "subtitle batch remains incomplete"):
                translator.translate_batch([cue])

        self.assertEqual(post.call_count, 3)
        self.assertTrue(all(call.kwargs["params"]["key"] == "primary" for call in post.call_args_list))

    def test_environment_loader_uses_only_the_primary_key(self):
        env = {
            "GEMINI_API_KEY": "primary",
            "GEMINI_API_KEY_1": "backup-one",
            "GEMINI_API_KEY_2": "backup-two",
            "GEMINI_API_KEY_3": "backup-three",
            "GEMINI_API_KEY_4": "backup-four",
        }
        with patch.dict(os.environ, env, clear=True):
            translator = get_english_to_persian_translator()

        self.assertIsInstance(translator, GeminiSubtitleTranslator)
        self.assertEqual(translator.api_key, "primary")
        self.assertEqual(translator.api_keys, ["primary"])

    def test_missing_primary_key_fails_without_offline_fallback(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "GEMINI_API_KEY is required"):
                get_english_to_persian_translator()

    def test_secret_is_not_in_error_when_provider_returns_error(self):
        secret = "never-log-this-key"
        response = Mock(status_code=400)
        response.raise_for_status.side_effect = requests.HTTPError("bad request")
        translator = GeminiSubtitleTranslator(secret)
        with patch("tools.bilingual_subtitles.requests.post", return_value=response):
            with self.assertRaises(RuntimeError) as caught:
                translator._generate_json({}, "test request")
        self.assertNotIn(secret, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
