"""Synthetic text only: these fixtures are not collected or published samples."""

import csv
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from threads_web_report import (DEFAULT_FONT, SOURCE_LABEL, _safe_csv_cell, build_reports,
                                document_frequencies, load_sample, render_wordcloud, sanitize_text)


NOW = datetime(2026, 9, 30, 4, tzinfo=timezone.utc)


def observation(post_id="p1", **overrides):
    row = dict(post_id=post_id, permalink=f"https://www.threads.com/@sample/post/{post_id}",
               text="捷運 捷運 下雨 無人機", published_at_utc="2026-07-24T16:00:00Z",
               published_date_taipei="2026-07-25", date_precision="exact_timestamp",
               date_display_raw="2026-7-25", collected_at_utc="2026-09-30T02:00:00Z",
               query_text="大稻埕煙火", text_complete=True, relevance="include",
               relevance_reason="synthetic test", source_type="threads_visible_web")
    row.update(overrides)
    return row


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.raw = self.root / "observations.jsonl"
        self.out = self.root / "output"

    def tearDown(self):
        self.temp.cleanup()

    def write(self, *rows):
        self.raw.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")

    def sample(self):
        return load_sample(self.raw, "2026-07-15", "2026-08-15", now=NOW)

    def test_timezone_boundaries_and_32_inclusive_dates(self):
        self.write(
            observation("before", published_at_utc="2026-07-14T15:59:59Z", published_date_taipei="2026-07-14"),
            observation("first", published_at_utc="2026-07-14T16:00:00Z", published_date_taipei="2026-07-15"),
            observation("last", published_at_utc="2026-08-15T15:59:59Z", published_date_taipei="2026-08-15"),
            observation("after", published_at_utc="2026-08-15T16:00:00Z", published_date_taipei="2026-08-16"),
        )
        sample = self.sample()
        self.assertEqual([row["post_id"] for row in sample["posts"]], ["first", "last"])
        self.assertEqual(len(sample["daily"]), 32)
        self.assertEqual(sample["daily"][0]["observed_post_count"], 1)
        self.assertEqual(sample["daily"][-1]["observed_post_count"], 1)
        self.assertIsNone(sample["daily"][1]["observed_post_count"])

    def test_day_only_is_valid_but_uncertain_and_conflicting_dates_are_not(self):
        self.write(
            observation("day", date_precision="day", published_at_utc=None),
            observation("unknown", date_precision="uncertain"),
            observation("conflict", published_date_taipei="2026-07-24"),
            observation("naive", published_at_utc="2026-07-25T00:00:00"),
        )
        self.assertEqual([row["post_id"] for row in self.sample()["posts"]], ["day"])

    def test_latest_complete_text_survives_newer_truncated_card_and_deduplicates_queries(self):
        self.write(
            observation(text="舊文字"),
            observation(text="新文字", query_text="大稻埕夏日節", collected_at_utc="2026-09-30T02:01:00Z"),
            observation(text="截斷", text_complete=False, collected_at_utc="2026-09-30T02:02:00Z"),
        )
        self.assertEqual(len(self.sample()["posts"]), 1)
        self.assertEqual(self.sample()["posts"][0]["text"], "新文字")

    def test_latest_complete_review_excludes_older_include(self):
        self.write(observation(), observation(relevance="review", collected_at_utc="2026-09-30T02:01:00Z"))
        self.assertEqual(self.sample()["posts"], [])

    def test_retention_collection_time_and_source(self):
        self.write(
            observation("edge", collected_at_utc=(NOW - timedelta(days=90)).isoformat()),
            observation("expired", collected_at_utc=(NOW - timedelta(days=90, seconds=1)).isoformat()),
            observation("future", collected_at_utc=(NOW + timedelta(seconds=1)).isoformat()),
            observation("naive", collected_at_utc="2026-09-30T02:00:00"),
            observation("wrong", source_type="unapproved_private_api"),
        )
        before = self.raw.read_bytes()
        self.assertEqual([row["post_id"] for row in self.sample()["posts"]], ["edge"])
        self.assertEqual(self.raw.read_bytes(), before)

    def test_relevance_and_boolean_complete_are_required(self):
        self.write(observation("bad", text_complete="true"), observation("review", relevance="review"),
                   observation("exclude", relevance="exclude"), observation("empty", text="  "))
        self.assertEqual(self.sample()["posts"], [])

    def test_malformed_lines_are_accounted_without_printing_raw_text(self):
        self.write(observation())
        with self.raw.open("a", encoding="utf-8") as handle:
            handle.write("not-json-secret\n[]\n{}\n")
        sample = self.sample()
        self.assertEqual(len(sample["posts"]), 1)
        self.assertEqual(sample["omission_counts"]["invalid_json"], 1)
        self.assertEqual(sample["omission_counts"]["invalid_source"], 2)

    def test_document_frequency_privacy_and_query_word_exclusion(self):
        text = ("捷運 捷運 無人機 大稻埕 煙火 @private_name someone@example.com "
                "https://www.example.com/secret +886 912-345-678 02-2345-6789")
        posts = [dict(post_id="a", text=text), dict(post_id="a", text="重複"), dict(post_id="b", text="捷運 下雨")]
        result = document_frequencies(posts, tokenizer=str.split, extra_stopwords=["下雨"])
        self.assertEqual(result, {"捷運": 2, "無人機": 1})
        clean = sanitize_text(text)
        self.assertNotIn("private_name", clean)
        self.assertNotIn("example", clean)
        self.assertNotIn("678", clean)

    def test_csv_formula_sanitizer(self):
        for text in ("=1+1", "+SUM(A1)", "-2", "@cmd", "  =X"):
            self.assertEqual(_safe_csv_cell(text), "'" + text)
        self.assertEqual(_safe_csv_cell("捷運"), "捷運")
        self.assertEqual(_safe_csv_cell(3), 3)

    def test_empty_report_removes_stale_cloud_and_exports_blank_unknown_days(self):
        self.write(observation(relevance="review"))
        self.out.mkdir()
        (self.out / "wordcloud.png").write_bytes(b"stale")
        with patch("threads_web_report._utc_now", return_value=NOW):
            report = build_reports(self.raw, self.out, "2026-07-15", "2026-08-15")
        self.assertFalse(report["wordcloud_created"])
        self.assertFalse((self.out / "wordcloud.png").exists())
        self.assertFalse(report["platform_total"])
        with (self.out / "daily_observed_counts.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 32)
        self.assertTrue(all(row["observed_post_count"] == "" for row in rows))
        self.assertTrue(all(row["count_kind"] == "observed_samples" for row in rows))

    def test_reports_export_terms_not_raw_text_or_identifiers(self):
        self.write(observation())
        with patch("threads_web_report._utc_now", return_value=NOW), \
             patch("threads_web_report._jieba_tokenizer", return_value=str.split), \
             patch("threads_web_report.render_wordcloud"):
            report = build_reports(self.raw, self.out, "2026-07-15", "2026-08-15")
        self.assertEqual(report["sample_post_count"], 1)
        self.assertTrue(report["wordcloud_created"])
        with (self.out / "words.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual({row["word"]: int(row["document_frequency"]) for row in rows}, {"捷運": 1, "下雨": 1, "無人機": 1})
        self.assertTrue(all(row["sample_label"] == SOURCE_LABEL for row in rows))
        self.assertNotIn("permalink", rows[0])
        self.assertNotIn("text", rows[0])

    def test_render_failure_does_not_keep_stale_success_summary(self):
        self.write(observation())
        self.out.mkdir()
        (self.out / "wordcloud.png").write_bytes(b"stale")
        with patch("threads_web_report._utc_now", return_value=NOW), \
             patch("threads_web_report._jieba_tokenizer", return_value=str.split):
            with self.assertRaises(FileNotFoundError):
                build_reports(self.raw, self.out, "2026-07-15", "2026-08-15", self.root / "missing.ttf")
        self.assertFalse((self.out / "wordcloud.png").exists())
        self.assertFalse(json.loads((self.out / "summary.json").read_text(encoding="utf-8"))["wordcloud_created"])

    def test_invalid_range(self):
        self.write(observation())
        with self.assertRaises(ValueError):
            load_sample(self.raw, "2026-08-15", "2026-07-15", now=NOW)

    @unittest.skipUnless(DEFAULT_FONT.is_file(), "Traditional Chinese font unavailable")
    def test_real_render_is_4_by_3(self):
        from PIL import Image
        path = self.root / "synthetic.png"
        render_wordcloud({"捷運": 3, "下雨": 2, "無人機": 1}, path,
                         {"start_date": "2026-07-15", "end_date": "2026-08-15", "sample_post_count": 3,
                          "sample_label": SOURCE_LABEL}, DEFAULT_FONT)
        with Image.open(path) as image:
            self.assertEqual(image.size, (1600, 1200))


if __name__ == "__main__":
    unittest.main()
