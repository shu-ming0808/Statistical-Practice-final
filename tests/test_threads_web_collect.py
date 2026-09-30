import csv
from datetime import date, datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import threads_web_collect as m


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 30, tzinfo=timezone.utc)
        self.first, self.last = date(2026, 7, 15), date(2026, 8, 15)

    def card(self, stamp="2026-07-14T16:00:00Z", **kwargs):
        result = dict(permalink="https://www.threads.com/@example/post/SyntheticTest01?tracking=unused",
                      text="大稻埕煙火下雨，捷運交通管制", datetime=stamp,
                      date_display_raw="2026-7-15", text_complete=True, topics=[])
        result.update(kwargs)
        return result

    def norm(self, card):
        return m.normalise_card(card, "大稻埕 煙火", self.now, self.first, self.last)

    def test_taipei_boundaries(self):
        self.assertIsNone(self.norm(self.card("2026-07-14T15:59:59Z")))
        self.assertEqual(self.norm(self.card())["published_date_taipei"], "2026-07-15")
        self.assertEqual(self.norm(self.card("2026-08-15T15:59:59Z"))["published_date_taipei"], "2026-08-15")
        self.assertIsNone(self.norm(self.card("2026-08-15T16:00:00Z")))
        self.assertEqual((self.last-self.first).days+1, 32)

    def test_canonical_url_and_unsafe_hosts(self):
        post = self.norm(self.card())
        self.assertNotIn("?", post["permalink"])
        for url in ("https://threads.com.evil.test/@x/post/y", "http://threads.com/@x/post/y",
                    "https://user:secret@threads.com/@x/post/y", "https://threads.com/@x/post/y/media"):
            self.assertIsNone(m.canonical_post_url(url))

    def test_unknown_dates_and_topic_only_never_auto_included(self):
        row = self.norm(self.card(None, date_display_raw="3小時"))
        self.assertIsNone(row["published_date_taipei"])
        self.assertEqual(row["relevance"], "review")
        row = self.norm(self.card(text="河景第一排", topics=["大稻埕夏日節"]))
        self.assertEqual(row["relevance"], "review")
        row = self.norm(self.card(text="大稻埕咖啡好喝"))
        self.assertEqual(row["relevance"], "review")
        self.assertEqual(row["relevance_reason"], "place_context_requires_review")
        self.assertEqual(self.norm(self.card(text="淡水咖啡好喝"))["relevance"], "exclude")

    def test_incomplete_ambiguous_and_invalid_dates(self):
        for changes in ({"text_complete": False}, {"ambiguous_card": True}, {"text": ""}):
            self.assertEqual(self.norm(self.card(**changes))["relevance"], "review")
        row = self.norm(self.card(None, date_display_raw="2026-7-25"))
        self.assertEqual(row["date_precision"], "day")
        self.assertEqual(self.norm(self.card(None, date_display_raw="2026-2-30"))["date_precision"], "uncertain")

    def test_dedup_preserves_complete_text_and_query_provenance(self):
        records = {}
        row = self.norm(self.card())
        m.merge_observation(records, row)
        shorter = self.norm(self.card(text="大稻埕…", text_complete=False))
        shorter["query_text"] = "大稻埕夏日節"
        m.merge_observation(records, shorter)
        self.assertEqual(len(records), 1)
        self.assertTrue(next(iter(records.values()))["text_complete"])
        self.assertEqual(len(next(iter(records.values()))["matched_queries"]), 2)

    def test_search_url_and_log_allowlist(self):
        url = m.search_url("大稻埕 煙火")
        self.assertNotIn("filter", m.safe_search_description(url)["parameters"])
        with self.assertRaises(ValueError):
            m.search_url("大稻埕", "recent")
        bad = "https://www.instagram.com/login/?access_token=secret"
        self.assertEqual(m.safe_search_description(bad), {"on_search_page":False})
        self.assertNotIn("secret", json.dumps(m.safe_search_description(url+"&access_token=secret")))

    def test_csv_formula_safety_and_canonical_json(self):
        row = self.norm(self.card(text="=HYPERLINK(惡意) 大稻埕煙火"))
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            m.save_records(folder, {row["post_id"]: row})
            saved = json.loads((folder/"posts.jsonl").read_text(encoding="utf-8"))
            self.assertTrue(saved["text"].startswith("="))
            with (folder/"posts.csv").open(encoding="utf-8-sig", newline="") as handle:
                self.assertTrue(next(csv.DictReader(handle))["text"].startswith("'="))

    def test_purge_only_owned_expired_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            raw, out = Path(temp)/"raw", Path(temp)/"out"
            run = raw/"web_sample_2026-07-15_2026-08-15"/"run"
            run.mkdir(parents=True)
            result = out/run.relative_to(raw)
            result.mkdir(parents=True)
            (run/"posts.jsonl").write_text("private raw", encoding="utf-8")
            (run/"keep.txt").write_text("unrelated", encoding="utf-8")
            (result/"wordcloud.png").write_bytes(b"derived")
            m.write_json(run/"manifest.json", dict(source_type=m.SOURCE,
                expires_at_utc=(self.now-timedelta(seconds=1)).isoformat()))
            with patch.object(m,"RAW_ROOT",raw), patch.object(m,"OUTPUT_ROOT",out):
                self.assertEqual(m.purge_expired(self.now),2)
            self.assertTrue((run/"keep.txt").exists())
            self.assertEqual(json.loads((run/"manifest.json").read_text())["status"],"expired")

    def test_plan_never_requires_browser_dependency(self):
        with patch("builtins.print"):
            self.assertEqual(m.main(["--plan"]),0)
            self.assertEqual(m.main(["--plan","--start-date","2026-08-16","--end-date","2026-08-15"]),2)


class VisibleDomTests(unittest.TestCase):
    """Synthetic public-card HTML, never live credentials or private APIs."""
    def test_dom_excludes_header_actions_quotes_and_captures_timestamp(self):
        from playwright.sync_api import sync_playwright
        html = '''<div data-pressable-container="true">
          <a href="https://www.threads.com/@example/post/Synthetic01"><time datetime="2026-07-14T16:00:00Z">2026-7-15</time></a>
          <a href="/@example"><span dir="auto" translate="no">example</span></a>
          <a href="/search?q=x&serp_type=tags"><span dir="auto">大稻埕夏日節</span></a>
          <span dir="auto"><span>大稻埕煙火 無人機</span><div>1 / 2</div></span>
          <div role="button"><span dir="auto">999</span></div>
          <div data-pressable-container="true"><span dir="auto">不得混入的引文</span></div>
          </div>'''
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page()
            page.set_content(html)
            rows = page.evaluate(m.EXTRACT_JS)
            # Expander outside the text span must not look like full text.
            page.set_content(html.replace('<div role="button">', '<button>顯示更多</button><div role="button">'))
            truncated = page.evaluate(m.EXTRACT_JS)
            # A toolbar SVG title alone is not a text expander.
            page.set_content(html.replace('<div role="button">', '<button><svg><title>更多</title></svg></button><div role="button">'))
            toolbar = page.evaluate(m.EXTRACT_JS)
            browser.close()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["text"],"大稻埕煙火 無人機")
        self.assertEqual(rows[0]["topics"],["大稻埕夏日節"])
        self.assertEqual(rows[0]["datetime"],"2026-07-14T16:00:00Z")
        self.assertFalse(truncated[0]["text_complete"])
        self.assertTrue(toolbar[0]["text_complete"])


class SlowSearchTests(unittest.TestCase):
    def sample(self, batches, *, idle_seconds=90, budget_seconds=60, max_scrolls=4):
        page = MagicMock()
        page.url = m.search_url("大稻埕")
        page.get_by_text.return_value.first.is_visible.return_value = False
        page.evaluate.side_effect = batches
        elapsed = [0.0]
        page.wait_for_timeout.side_effect = lambda ms: elapsed.__setitem__(0, elapsed[0]+ms/1000)
        args = SimpleNamespace(delay=4, max_scrolls=max_scrolls, max_posts=2000, idle_seconds=idle_seconds)
        entry = {"sort": "top", "snapshots": 0, "stop_reason": None}
        records, manifest = {}, {"retained_posts": 0}
        with tempfile.TemporaryDirectory() as temp, patch.object(m.time, "monotonic", side_effect=lambda: elapsed[0]), patch("builtins.print"):
            reason = m.sample_search_page(page, "大稻埕", date(2026, 7, 15), date(2026, 8, 15),
                args, entry, records, Path(temp), manifest, lambda _: None, budget_seconds)
        self.assertIsNone(reason)
        return entry, records

    def card(self, post_id="Inside", stamp="2026-07-14T16:00:00Z"):
        return {"permalink": f"https://www.threads.com/@synthetic/post/{post_id}",
                "text": "大稻埕煙火 交通管制", "datetime": stamp,
                "date_display_raw": "", "text_complete": True}

    def test_delayed_card_survives_three_empty_snapshots_and_date_counts_are_unique(self):
        cards = [self.card(), self.card("Outside", "2026-08-15T16:00:00Z"), self.card("Unknown", None)]
        entry, records = self.sample([[], [], [], [], cards])
        self.assertEqual(entry["snapshots"], 5)
        self.assertEqual(entry["date_counts"], {"in_period": 1, "outside_period": 1, "unknown": 1})
        self.assertEqual(set(records), {"Inside", "Unknown"})
        self.assertEqual(records["Unknown"]["relevance"], "review")

    def test_repeated_cards_wait_until_elapsed_idle_limit(self):
        entry, records = self.sample([[self.card()]] * 10, idle_seconds=16, max_scrolls=9)
        self.assertEqual(entry["stop_reason"], "idle_timeout")
        self.assertEqual(entry["snapshots"], 5)
        self.assertEqual(entry["elapsed_seconds"], 20)
        self.assertEqual(len(records), 1)

    def test_time_budget_stops_even_when_more_cards_are_available(self):
        entry, records = self.sample([[self.card("A")], [self.card("B")], [self.card("C")]],
                                     budget_seconds=8)
        self.assertEqual(entry["stop_reason"], "time_limit")
        self.assertEqual(set(records), {"A", "B"})
        self.assertEqual(entry["elapsed_seconds"], 8)


if __name__ == "__main__":
    unittest.main()
