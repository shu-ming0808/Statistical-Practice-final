from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import ptt_web_collect as ptt
from threads_web_report import build_reports, load_sample

NOW = datetime(2026, 9, 30, tzinfo=timezone.utc)
STAMP = int(datetime(2026, 7, 15, tzinfo=ptt.TAIPEI).timestamp())
URL = f"https://www.ptt.cc/bbs/MRT/M.{STAMP}.A.ABC.html"


def article(stamp="Wed Jul 15 00:00:00 2026"):
    return f'''<div id="main-content">
      <div class="article-metaline"><span class="article-meta-tag">作者</span><span class="article-meta-value">PRIVATE_AUTHOR_TEST</span></div>
      <div class="article-metaline"><span class="article-meta-tag">標題</span><span class="article-meta-value">[情報] 大稻埕煙火交通</span></div>
      <div class="article-metaline"><span class="article-meta-tag">時間</span><span class="article-meta-value">{stamp}</span></div>
      大稻埕煙火 捷運散場人潮<br>正文提到2025年8月1日，但不是發文日期。
      <span class="f2">※ 發信站: PRIVATE_IP_TEST 127.0.0.99</span>
      <div class="push"><span class="push-userid">PRIVATE_COMMENTER_TEST</span><span class="push-content">不要混入推文</span></div>
      </div>'''


def search(links, older=None):
    html = '<div class="r-list-container">' + ''.join(
        f'<div class="r-ent"><div class="title"><a href="{url}">大稻埕</a></div><div class="date">7/15</div></div>'
        for url in links) + '</div>'
    if older:
        html += f'<div class="btn-group-paging"><a href="{older}">‹ 上頁</a></div>'
    return html


class PTTTests(unittest.TestCase):
    def test_publication_header_is_authoritative_and_metadata_comments_removed(self):
        row = ptt.parse_article(article(), URL, "大稻埕", NOW)
        self.assertEqual(row["published_date_taipei"], "2026-07-15")
        self.assertEqual(row["published_at_utc"], "2026-07-14T16:00:00+00:00")
        self.assertEqual(row["relevance"], "include")
        for marker in ("PRIVATE_AUTHOR", "PRIVATE_IP", "PRIVATE_COMMENTER", "不要混入推文", "127.0.0.99"):
            self.assertNotIn(marker, json.dumps(row, ensure_ascii=False))
        self.assertIn("2025年8月1日", row["text"])

    def test_missing_header_does_not_infer_year_from_url_or_list(self):
        row = ptt.parse_article(article("7/15"), URL, "大稻埕", NOW)
        self.assertEqual(row["relevance"], "review")
        self.assertIsNone(row["published_date_taipei"])
        self.assertIsNone(ptt.parse_header_time("Tue Feb 30 12:00:00 2026"))

    def test_urls_and_pagination_are_board_and_query_scoped(self):
        for bad in ("https://ptt.cc.evil.test/bbs/MRT/M.1784044800.A.ABC.html",
                    "http://www.ptt.cc/bbs/MRT/search?q=x", "https://secret@www.ptt.cc/bbs/MRT/search?q=x",
                    "https://www.ptt.cc/bbs/MRT/search?access_token=TEST_ONLY"):
            self.assertIsNone(ptt.safe_ptt_url(bad))
        older = ptt.search_url("MRT", "大稻埕") + "&page=2"
        links, next_page = ptt.parse_search(search([URL], older), "MRT", "大稻埕")
        self.assertEqual(links, [URL])
        self.assertEqual(next_page, older)
        _, next_page = ptt.parse_search(search([URL], ptt.search_url("MRT", "其他")+"&page=2"), "MRT", "大稻埕")
        self.assertIsNone(next_page)

    def test_age_confirmation_is_not_automatically_accepted(self):
        with self.assertRaisesRegex(ptt.CollectionStop, "age_confirmation_required"):
            ptt.parse_search('<form action="/ask/over18"></form>', "Gossiping", "大稻埕")
        client = ptt.PTTClient(2, float("inf"))
        response = MagicMock()
        response.__enter__.return_value = response
        response.status_code = 302
        response.headers = {"Location": "/ask/over18?from=x"}
        with patch.object(client.session, "get", return_value=response):
            with self.assertRaisesRegex(ptt.CollectionStop, "age_confirmation_required"):
                client.get(ptt.search_url("Gossiping", "大稻埕"))
        self.assertEqual(len(client.session.cookies), 0)
        client.close()

    def test_ptt_reports_reject_threads_and_outside_period_and_keep_ptt_labels(self):
        row = ptt.parse_article(article(), URL, "大稻埕", NOW)
        wrong_source = dict(row, post_id="threads", source_type="threads_visible_web")
        outside = ptt.parse_article(article("Sun Aug 16 00:00:00 2026"), URL.replace("ABC", "DEF"), "大稻埕", NOW)
        with tempfile.TemporaryDirectory() as temp:
            raw, output = Path(temp)/"raw", Path(temp)/"out"
            ptt.save_records(raw, {r["post_id"]: r for r in (row, wrong_source, outside)})
            with patch("threads_web_report._utc_now", return_value=NOW), patch("threads_web_report.render_wordcloud") as render:
                summary = build_reports(raw/"posts.jsonl", output, "2026-07-15", "2026-08-15", source_type=ptt.SOURCE)
            self.assertEqual(summary["sample_post_count"], 1)
            self.assertEqual(summary["omission_counts"], {"invalid_source": 1, "out_of_period_post": 1})
            self.assertTrue(summary["sample_label"].startswith("PTT"))
            self.assertTrue(render.call_args.args[2]["sample_label"].startswith("PTT"))
            self.assertNotIn("threads_visible_web", (output/"words.csv").read_text(encoding="utf-8-sig"))

    def test_collector_paginates_deduplicates_and_filters_header_date(self):
        second = URL.replace("ABC", "DEF")
        first_page = ptt.search_url("MRT", "大稻埕")
        older = first_page + "&page=2"
        pages = {first_page: search([URL], older), older: search([URL, second]),
                 URL: article(), second: article("Sun Aug 16 00:00:00 2026")}
        client = MagicMock()
        client.requests_made = 5
        client.check_robots.return_value = "not_found_404"
        client.get.side_effect = lambda url: pages[url]
        with tempfile.TemporaryDirectory() as temp, patch.object(ptt, "PTTClient", return_value=client), \
                patch.object(ptt, "RAW_ROOT", Path(temp)/"raw"), patch.object(ptt, "OUTPUT_ROOT", Path(temp)/"out"), \
                patch("threads_web_report.build_reports", return_value={"sample_post_count": 1, "wordcloud_created": True}), \
                patch("builtins.print"):
            self.assertEqual(ptt.main(["--board", "MRT"]), 0)
            manifest = json.loads(next((Path(temp)/"raw").glob("*/*/manifest.json")).read_text(encoding="utf-8"))
            self.assertEqual(manifest["retained_posts"], 1)
            self.assertEqual(manifest["query_runs"][0]["search_pages"], 2)
            self.assertEqual(manifest["query_runs"][0]["header_outside_period"], 1)
            self.assertEqual([args.args[0] for args in client.get.call_args_list].count(URL), 1)

    def test_expiry_only_deletes_owned_files(self):
        with tempfile.TemporaryDirectory() as temp:
            raw, output = Path(temp)/"raw", Path(temp)/"out"
            folder = raw/"web_sample_2026-07-15_2026-08-15"/"test"
            ptt.save_records(folder, {})
            (folder/"unrelated.txt").write_text("keep")
            ptt.write_json(folder/"manifest.json", {"source_type":ptt.SOURCE,"expires_at_utc":(NOW-timedelta(seconds=1)).isoformat()})
            with patch.object(ptt, "RAW_ROOT", raw), patch.object(ptt,"OUTPUT_ROOT",output):
                self.assertEqual(ptt.purge_expired(NOW), 2)
            self.assertTrue((folder/"unrelated.txt").is_file())


if __name__ == "__main__":
    unittest.main()
