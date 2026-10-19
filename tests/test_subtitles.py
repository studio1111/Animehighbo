import unittest

from tools.bilingual_subtitles import (
    Cue,
    choose_english_stream,
    parse_srt,
    render_srt,
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

    def test_translation_preserves_cue_timing_and_formatting_tags(self):
        class FakeTranslator:
            def translate(self, text):
                return text.replace("Hello", "سلام").replace("Run", "بدو")

        cues = [Cue(1, "00:00:01,000 --> 00:00:02,000", "<i>Hello</i>")]
        translated = translate_cues(cues, FakeTranslator())
        self.assertEqual(translated[0].timing, cues[0].timing)
        self.assertIn("<i>", translated[0].text)
        self.assertIn("سلام", translated[0].text)


if __name__ == "__main__":
    unittest.main()
