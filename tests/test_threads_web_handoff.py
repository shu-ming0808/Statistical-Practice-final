"""Exercise collect() at the browser/login boundary, without live credentials."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import threads_web_collect as collector
from playwright.sync_api import Error, TimeoutError


class BrowserHandoffTests(unittest.TestCase):
    def page(self):
        page = MagicMock()
        page.url = "https://www.threads.com/"
        page.is_closed.return_value = False
        page.get_by_text.return_value.first.is_visible.return_value = False
        page.evaluate.return_value = []
        page.goto.side_effect = lambda url, **kwargs: setattr(page, "url", url)
        return page

    def run_collector(self, setup, extra_options=None):
        first_page, replacement = self.page(), self.page()
        browser, context, playwright = MagicMock(), MagicMock(), MagicMock()
        browser.is_connected.return_value = True
        playwright.chromium.launch.return_value = browser
        browser.new_context.return_value = context
        context.new_page.return_value = first_page
        context.pages = [first_page]
        context.wait_for_event.side_effect = TimeoutError("expected event-pump timeout")
        on_enter = setup(first_page, replacement, context)
        with tempfile.TemporaryDirectory() as temp:
            raw, output = Path(temp) / "raw", Path(temp) / "reports"
            captured = io.StringIO()
            with patch("playwright.sync_api.sync_playwright") as manager, \
                    patch("builtins.input", side_effect=on_enter), \
                    patch.object(collector, "RAW_ROOT", raw), \
                    patch.object(collector, "OUTPUT_ROOT", output), \
                    patch("threads_web_report.build_reports", return_value={}), \
                    contextlib.redirect_stdout(captured):
                manager.return_value.__enter__.return_value = playwright
                result = collector.main(["--query", "大稻埕 煙火", "--sort", "top", "--max-scrolls", "0"]
                                        + (extra_options or []))
            manifest_path = next(raw.glob("*/*/manifest.json"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return result, manifest, captured.getvalue(), first_page, replacement

    def test_closed_original_tab_replaced_during_login(self):
        def setup(first, replacement, context):
            def enter(_):
                first.is_closed.return_value = True
                first.goto.side_effect = Error("Target page, context or browser has been closed")
                context.pages = [replacement]
                return ""
            return enter
        result, manifest, output, original, replacement = self.run_collector(setup)
        self.assertEqual(result, 0)
        self.assertEqual(manifest["query_runs"][0]["snapshots"], 1)
        replacement.goto.assert_called_once()
        self.assertEqual(original.goto.call_count, 1)  # homepage only, never stale tab

    def test_search_timeout_is_diagnostic_not_empty_data_and_no_secret_leak(self):
        def setup(first, replacement, context):
            def enter(_):
                first.goto.side_effect = TimeoutError(
                    'Page.goto: Timeout exceeded at https://www.instagram.com/oauth?code=SECRET_TEST_ONLY')
                return ""
            return enter
        result, manifest, output, _, _ = self.run_collector(setup)
        self.assertEqual(result, 1)
        self.assertEqual(manifest["error"]["stage"], "search_navigation")
        self.assertEqual(manifest["error"]["category"], "timeout")
        self.assertEqual(manifest["query_runs"][0]["stop_reason"], "browser_error")
        self.assertIn("尚未開始讀取貼文", output)
        self.assertNotIn("沒有符合日期的可用資料", output)
        self.assertNotIn("SECRET_TEST_ONLY", json.dumps(manifest) + output)
        self.assertNotIn("instagram.com", json.dumps(manifest) + output)

    def test_interrupted_navigation_has_safe_specific_diagnostic(self):
        def setup(first, replacement, context):
            def enter(_):
                first.goto.side_effect = Error(
                    'Page.goto: net::ERR_ABORTED at https://www.instagram.com/oauth?code=SECRET_TEST_ONLY')
                return ""
            return enter
        result, manifest, output, _, _ = self.run_collector(setup)
        self.assertEqual(result, 1)
        self.assertEqual(manifest["error"]["category"], "navigation_interrupted")
        self.assertEqual(manifest["error"]["network_code"], "ERR_ABORTED")
        self.assertNotIn("SECRET_TEST_ONLY", json.dumps(manifest) + output)

    def test_keywords_keep_one_copy_and_only_top_sort(self):
        def setup(first, replacement, context):
            first.evaluate.return_value = [{
                "permalink": "https://www.threads.com/@synthetic/post/SharedPost",
                "text": "大稻埕煙火 捷運交通", "datetime": "2026-07-25T12:00:00Z",
                "date_display_raw": "2026-7-25", "text_complete": True,
            }]
            return lambda _: ""
        result, manifest, output, original, replacement = self.run_collector(setup, ["--query", "大稻埕"])
        self.assertEqual(result, 0)
        self.assertEqual(manifest["retained_posts"], 1)
        self.assertEqual([entry["sort"] for entry in manifest["query_runs"]], ["top", "top"])
        self.assertTrue(all(entry["budget_seconds"] == 15 * 60 for entry in manifest["query_runs"]))
        self.assertEqual(original.goto.call_count, 3)


class EdgeLoginLifecycleTests(unittest.TestCase):
    def test_popup_created_while_waiting_for_enter_and_closed_original_recovered(self):
        from playwright.sync_api import sync_playwright
        # All requests are fulfilled locally; no real login or Threads data.
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
            context = browser.new_context()
            context.route("**/*", lambda route: route.fulfill(
                content_type="text/html", body="<html><body>Synthetic login handoff</body></html>"))
            original = context.new_page()
            original.goto("https://www.threads.com/")
            # Chromium changes its tabs while stdin is waiting, as OAuth can.
            original.evaluate("""setTimeout(() => window.open(
                'https://www.threads.com/search?q=SyntheticHandoff'), 100)""")
            with patch("builtins.input", side_effect=lambda _: Event().wait(0.8)):
                collector.wait_for_confirmation(context, "synthetic Enter")
            self.assertEqual(len(context.pages), 2)
            original.close()
            selected = collector.ready_threads_page(context, browser, timeout_ms=5000)
            self.assertIn("SyntheticHandoff", selected.url)
            self.assertFalse(selected.is_closed())
            context.close()
            browser.close()


if __name__ == "__main__":
    unittest.main()
