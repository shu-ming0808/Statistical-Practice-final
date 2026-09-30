"""Collect a bounded sample of public Threads search cards through the normal UI.

No API token, private endpoint, saved cookies, or existing browser profile is used.
Run from anywhere: paths resolve relative to this project's directory.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from queue import Empty, Queue
import re
import sys
from threading import Thread
import time
from urllib.parse import parse_qs, urlencode, urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = ROOT / "data/original_data/threads"
OUTPUT_ROOT = ROOT / "results/threads"
TAIPEI = timezone(timedelta(hours=8))
DEFAULT_QUERIES = ("大稻埕 煙火", "大稻埕")
SOURCE = "threads_visible_web"
RETENTION_DAYS = 90
NAVIGATION_TIMEOUT_MS = 45000

# Derived from visible Threads search cards, inspected 2026-09-30. Only DOM
# content is read; no network interception, hydration data, cookies, or tokens.
EXTRACT_JS = r"""() => {
  const cards = [], seen = new Set();
  const visible = e => e.getClientRects().length > 0 &&
    getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
  for (const anchor of document.querySelectorAll('a[href*="/post/"]')) {
    const stamp = anchor.querySelector('time');
    if (!stamp || !visible(stamp)) continue;
    const card = anchor.closest('[data-pressable-container]');
    if (!card || seen.has(card)) continue;
    seen.add(card);
    const own = e => e.closest('[data-pressable-container]') === card;
    const times = Array.from(card.querySelectorAll('time')).filter(own);
    if (!times.length || stamp !== times[0]) continue;
    const chunks = Array.from(card.querySelectorAll('[dir="auto"]')).filter(e => {
      if (!own(e) || !visible(e) || e.closest('a,button,[role="button"]')) return false;
      if (e.querySelector('time') || e.getAttribute('translate') === 'no') return false;
      for (let p=e.parentElement; p && p!==card; p=p.parentElement)
        if(p.getAttribute('dir')==='auto') return false;
      return true;
    });
    // The toolbar's SVG '更多' title is not an expand-text button. innerText
    // intentionally ignores that title while detecting actual visible labels.
    let truncated = Array.from(card.querySelectorAll('button,[role="button"]'))
      .filter(own).some(e => /^(更多|展開|閱讀更多|顯示更多|查看更多|Read more|See more)$/i.test(e.innerText.trim()));
    const parts = chunks.map(e => {
      for(let p=e; p && p!==card; p=p.parentElement) {
        const style = getComputedStyle(p);
        if ((parseInt(style.webkitLineClamp,10)>0 || /hidden|clip/.test(style.overflowY))
            && p.scrollHeight>p.clientHeight+2) truncated = true;
      }
      const value = e.innerText.trim();
      if (/(?:閱讀更多|顯示更多|查看更多|Read more|See more)\s*$/i.test(value)) truncated=true;
      return value.replace(/\s+\d+\s*\/\s*\d+\s*$/, '').trim();
    }).filter(v => v && !/^[\d,.\s]+$/.test(v));
    const topics = Array.from(card.querySelectorAll('a[href*="serp_type=tags"]'))
      .filter(own).map(e=>e.innerText.trim()).filter(Boolean);
    cards.push({permalink:anchor.href, text:parts.join('\n'),
      datetime:stamp.getAttribute('datetime'), date_display_raw:stamp.innerText,
      topics:[...new Set(topics)], text_complete:!truncated,
      ambiguous_card:times.length!==1, edited:!!Array.from(card.querySelectorAll('[aria-label],svg title'))
        .find(e=>/^(已編輯|Edited)$/.test(e.getAttribute('aria-label')||e.textContent||''))});
  }
  return cards;
}"""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_day(value: str) -> date:
    day = date.fromisoformat(value)
    if day.isoformat() != value:
        raise ValueError("日期請使用 YYYY-MM-DD。")
    return day


def validate_period(start: str, end: str) -> tuple[date, date]:
    first, last = parse_day(start), parse_day(end)
    if last < first or (last-first).days > 366:
        raise ValueError("日期起迄順序錯誤，或超過單次 367 天上限。")
    return first, last


def canonical_post_url(value: str) -> tuple[str, str] | None:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.hostname not in {"www.threads.com", "threads.com", "www.threads.net", "threads.net"}:
        return None
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        return None
    match = re.fullmatch(r"/@([A-Za-z0-9._]+)/post/([A-Za-z0-9_-]+)(?:/)?", parsed.path)
    if not match:
        return None
    return match[2], f"https://www.threads.com/@{match[1]}/post/{match[2]}"


def search_url(query: str, sort: str = "top") -> str:
    if sort != "top":
        raise ValueError("Threads 僅使用最相關排序。")
    params = {"q": query, "serp_type": "default"}
    return "https://www.threads.com/search?" + urlencode(params)


def safe_search_description(url: str) -> dict:
    """Allowlist ordinary search fields; never persist login/OAuth URLs."""
    parsed = urlsplit(url)
    if parsed.hostname not in {"www.threads.com", "threads.com"} or parsed.path != "/search":
        return {"on_search_page": False}
    query = parse_qs(parsed.query)
    return {"on_search_page": True, "parameters": {
        key: query[key][0][:500] for key in ("q", "serp_type", "filter", "since", "until") if key in query}}


def publication_time(card: dict) -> tuple[str | None, date | None, str, str]:
    """Parse the publication time, never a date mentioned in the post's text."""
    stamp, local_day, precision = None, None, "uncertain"
    raw_stamp = card.get("datetime")
    if raw_stamp:
        try:
            parsed = datetime.fromisoformat(raw_stamp.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                stamp = parsed.astimezone(timezone.utc).isoformat()
                local_day = parsed.astimezone(TAIPEI).date()
                precision = "exact_timestamp"
        except (ValueError, TypeError):
            pass
    # Relative labels are never guessed. Date-only labels rely on the browser's
    # explicit Asia/Taipei timezone; exact DOM timestamps take precedence.
    label = str(card.get("date_display_raw") or "").strip()
    match = re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", label)
    if local_day is None and match:
        try:
            local_day = date(*(int(v) for v in match.groups()))
            precision = "day"
        except ValueError:
            pass
    return stamp, local_day, precision, label


def normalise_card(card: dict, query: str, collected: datetime,
                   first: date, last: date) -> dict | None:
    canonical = canonical_post_url(str(card.get("permalink") or ""))
    if canonical is None:
        return None
    post_id, permalink = canonical
    stamp, local_day, precision, label = publication_time(card)
    if local_day is not None and not first <= local_day <= last:
        return None  # out-of-period text is not written to disk
    body = str(card.get("text") or "").strip()
    complete = card.get("text_complete") is True
    topics = [str(t) for t in card.get("topics", []) if isinstance(t, str)]
    related = "大稻埕" in body and bool(re.search(r"煙火|烟火|花火|夏日節|夏日节", body))
    topic_related = any("大稻埕" in t and re.search(r"煙火|烟火|花火|夏日節|夏日节", t) for t in topics)
    relevance, reason = "exclude", "not_event_related"
    if related:
        relevance, reason = "include", "event_terms_in_body"
    elif topic_related:
        relevance, reason = "review", "topic_only_check_body_manually"
    elif "大稻埕" in body or any("大稻埕" in t for t in topics):
        relevance, reason = "review", "place_context_requires_review"
    if not body or not complete or card.get("ambiguous_card") or local_day is None:
        relevance = "review"
        reason = ("unknown_date" if local_day is None else "ambiguous_card" if card.get("ambiguous_card")
                  else "empty_or_incomplete_text")
    return dict(post_id=post_id, permalink=permalink, text=body,
                published_at_utc=stamp, published_date_taipei=local_day.isoformat() if local_day else None,
                date_precision=precision, date_display_raw=label,
                collected_at_utc=collected.astimezone(timezone.utc).isoformat(),
                query_text=query, topics=topics, text_complete=complete,
                relevance=relevance, relevance_reason=reason, author_type="unknown",
                edited=bool(card.get("edited")), source_type=SOURCE)


def merge_observation(records: dict, row: dict) -> None:
    old = records.get(row["post_id"])
    if old:
        queries = set(old.get("matched_queries", [old["query_text"]])) | {row["query_text"]}
        # Never replace a complete earlier observation with a truncated card.
        if old["text_complete"] and not row["text_complete"]:
            old["matched_queries"] = sorted(queries)
            return
    else:
        queries = {row["query_text"]}
    row["matched_queries"] = sorted(queries)
    records[row["post_id"]] = row


def write_json(path: Path, value) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def csv_value(value):
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False)
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value  # Excel formula injection protection; JSONL remains canonical.
    return value


def save_records(directory: Path, records: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    rows = sorted(records.values(), key=lambda r: (r.get("published_date_taipei") or "", r["post_id"]))
    temp = directory / "posts.jsonl.tmp"
    with temp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    temp.replace(directory / "posts.jsonl")
    fields = ("post_id", "published_date_taipei", "published_at_utc", "text", "permalink",
              "relevance", "relevance_reason", "text_complete", "date_precision", "topics",
              "query_text", "matched_queries", "author_type", "edited", "collected_at_utc", "source_type")
    with (directory / "posts.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: csv_value(row.get(key)) for key in fields} for row in rows)


def purge_expired(now: datetime | None = None) -> int:
    """Purge only expired files owned by this sampler, never other source data.

    Runs on invocation, not in the background. Figure/word outputs expire with
    their source. Unsupported or damaged manifests are left for manual review.
    """
    now = now or utc_now()
    removed = 0
    owned_raw = ("posts.jsonl", "posts.csv", "posts.jsonl.tmp")
    owned_reports = ("wordcloud.png", "words.csv", "daily_observed_counts.csv", "summary.json")
    for manifest_path in RAW_ROOT.glob("web_sample_*/*/manifest.json"):
        if not manifest_path.resolve().is_relative_to(RAW_ROOT.resolve()):
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            expires = datetime.fromisoformat(manifest["expires_at_utc"])
            if manifest.get("source_type") != SOURCE or expires.tzinfo is None or expires > now:
                continue
        except (OSError, ValueError, KeyError, TypeError):
            continue
        relative = manifest_path.parent.relative_to(RAW_ROOT)
        for parent, names, root in ((manifest_path.parent, owned_raw, RAW_ROOT),
                                     (OUTPUT_ROOT / relative, owned_reports, OUTPUT_ROOT)):
            for name in names:
                target = parent / name
                if target.resolve().is_relative_to(root.resolve()) and target.is_file():
                    target.unlink()
                    removed += 1
        manifest.update(status="expired", retained_posts=0)
        write_json(manifest_path, manifest)
    return removed


def page_blocker(page) -> str | None:
    """Read only the current rendered UI; do not log page text or full URLs."""
    parts = urlsplit(page.url)
    if parts.hostname not in {"www.threads.com", "threads.com"} or "/login" in parts.path:
        return "login_required"
    if parts.path != "/search":
        return "left_search_page"
    # Generic UI messages only, not arbitrary matched words inside post bodies.
    for pattern, reason in (
        (r"^(請稍後再試|Try again later|暫時封鎖|Temporarily blocked)", "rate_limited"),
        (r"^(確認你是真人|Verify you are human|完成驗證|Complete the CAPTCHA)", "verification_required"),
        (r"^(登入以取得更多|Log in to see more)", "login_required"),
    ):
        if page.get_by_text(re.compile(pattern, re.I)).first.is_visible():
            return reason
    return None


class BrowserHandoffError(Exception):
    """A fixed, non-sensitive reason why the login handoff cannot continue."""


def pump_browser_events(context, timeout_ms: int = 100) -> None:
    # Sync Playwright dispatches events only while a Playwright call is running.
    # A context wait also works when login closes/replaces the original page.
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    try:
        context.wait_for_event("page", timeout=timeout_ms)
    except PlaywrightTimeout:
        pass  # No new tab is normal; queued navigation/close events were serviced.


def wait_for_confirmation(context, prompt: str) -> None:
    """Read stdin separately; all browser access stays on the calling thread."""
    responses = Queue()

    def read_enter():
        try:
            input(prompt)
        except (EOFError, KeyboardInterrupt) as exc:
            responses.put(exc)
        else:
            responses.put(None)

    # A daemon avoids hanging process exit if the browser closes before Enter.
    Thread(target=read_enter, daemon=True).start()
    while True:
        pump_browser_events(context)
        try:
            result = responses.get_nowait()
        except Empty:
            continue
        if result is not None:
            raise result
        return


def ready_threads_page(context, browser, timeout_ms: int = 30000):
    """Reacquire a normal Threads home/search tab after login redirects settle.

    Does not read authentication storage or claim that a valid session exists;
    rendered login/verification walls are still checked on the search page.
    """
    from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeout
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        pump_browser_events(context)
        if not browser.is_connected():
            raise BrowserHandoffError("browser_disconnected")
        pages = [page for page in context.pages if not page.is_closed()]
        if not pages:
            raise BrowserHandoffError("browser_tabs_closed")
        for page in reversed(pages):
            parts = urlsplit(page.url)
            if parts.hostname not in {"www.threads.com", "threads.com"} or parts.path not in {"/", "/search"}:
                continue
            previous_url = page.url
            try:
                page.wait_for_load_state("domcontentloaded", timeout=1000)
                page.wait_for_timeout(350)
            except PlaywrightTimeout:
                continue
            except PlaywrightError:
                if page.is_closed():
                    continue
                raise
            if not page.is_closed() and page.url == previous_url:
                page.set_default_timeout(15000)
                return page
    raise BrowserHandoffError("login_not_ready")


def safe_browser_error(exc: Exception, stage: str) -> dict:
    """Classify locally; never retain exception messages, URLs, or OAuth values."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    message = str(exc)
    codes = {"ERR_ABORTED", "ERR_CONNECTION_RESET", "ERR_NAME_NOT_RESOLVED",
             "ERR_NETWORK_CHANGED", "ERR_INTERNET_DISCONNECTED", "ERR_CONNECTION_TIMED_OUT",
             "ERR_CONNECTION_CLOSED", "ERR_FAILED", "ERR_BLOCKED_BY_CLIENT",
             "ERR_HTTP_RESPONSE_CODE_FAILURE"}
    match = re.search(r"net::(ERR_[A-Z_]+)\b", message)
    code = match[1] if match and match[1] in codes else None
    if isinstance(exc, PlaywrightTimeout):
        category = "timeout"
    elif code == "ERR_ABORTED" or "interrupted by another navigation" in message:
        category = "navigation_interrupted"
    elif "has been closed" in message:
        category = "browser_or_page_closed"
    elif code:
        category = "network_error"
    else:
        category = "playwright_error"
    result = {"stage": stage, "category": category}
    if code:
        result["network_code"] = code
    return result


def failure_explanation(manifest: dict) -> str:
    error = manifest.get("error", {})
    stages = {"browser_launch": "啟動瀏覽器", "homepage_navigation": "開啟 Threads 首頁",
              "login_wait": "等待登入", "login_handoff": "確認登入後分頁",
              "search_navigation": "開啟搜尋頁", "manual_filters": "等待日期篩選",
              "page_check": "檢查搜尋頁", "extraction": "讀取貼文", "scrolling": "捲動頁面",
              "browser_cleanup": "關閉本次瀏覽器"}
    categories = {"timeout": "頁面載入逾時", "navigation_interrupted": "頁面跳轉中斷",
                  "browser_or_page_closed": "瀏覽器連線或分頁已關閉", "network_error": "網路連線失敗",
                  "playwright_error": "瀏覽器操作失敗", "login_not_ready": "尚未返回 Threads 首頁或搜尋頁",
                  "browser_disconnected": "瀏覽器連線中斷", "browser_tabs_closed": "已無可用分頁"}
    stage, category = error.get("stage"), error.get("category", manifest["status"])
    message = f"停止步驟：{stages.get(stage, '搜尋作業')}；原因：{categories.get(category, category)}。"
    if not any(entry["snapshots"] for entry in manifest["query_runs"]):
        message += "尚未開始讀取貼文，不能判定該期間沒有資料。"
    else:
        message += "本次採集未完成，已取得的樣本會保留。"
    return message


def sample_search_page(page, query, first, last, args, entry, records, raw, manifest,
                       set_stage, budget_seconds) -> str | None:
    """Allow slow lazy loading; stop by elapsed idle time, not three snapshots."""
    started = last_new = time.monotonic()
    seen, observed_dates = set(), {}
    for iteration in range(args.max_scrolls + 1):
        remaining = budget_seconds - (time.monotonic() - started)
        if remaining <= 0:
            entry["stop_reason"] = "time_limit"
            break
        set_stage("page_check")
        page.wait_for_timeout(min(args.delay, remaining) * 1000)
        blocker = page_blocker(page)
        if blocker:
            entry["stop_reason"] = blocker
            return blocker
        set_stage("extraction")
        cards = page.evaluate(EXTRACT_JS)
        entry["snapshots"] += 1
        identities = set()
        for card in cards:
            canonical = canonical_post_url(str(card.get("permalink") or ""))
            if canonical is None:
                continue
            identities.add(canonical[0])
            _, day, _, _ = publication_time(card)
            observed_dates[canonical[0]] = day
            row = normalise_card(card, query, utc_now(), first, last)
            if row is not None and (row["post_id"] in records or len(records) < args.max_posts):
                merge_observation(records, row)
        if identities - seen:
            last_new = time.monotonic()
        seen.update(identities)
        known_dates = [day for day in observed_dates.values() if day is not None]
        in_period = sum(first <= day <= last for day in known_dates)
        entry.update(seen_cards=len(seen), elapsed_seconds=round(time.monotonic() - started, 1),
                     date_counts={"in_period": in_period, "outside_period": len(known_dates)-in_period,
                                  "unknown": len(observed_dates)-len(known_dates)},
                     observed_date_min=min(known_dates).isoformat() if known_dates else None,
                     observed_date_max=max(known_dates).isoformat() if known_dates else None)
        save_records(raw, records)
        manifest["retained_posts"] = len(records)
        write_json(raw / "manifest.json", manifest)
        if iteration % 10 == 0:
            print(f"{query}／{entry['sort']}：已讀 {len(seen)} 篇，其中期間內 {in_period} 篇；"
                  f"累計保存 {len(records)} 篇（含待核對）。", flush=True)
        if len(records) >= args.max_posts:
            entry["stop_reason"] = "sample_limit"
            break
        if time.monotonic() - started >= budget_seconds:
            entry["stop_reason"] = "time_limit"
            break
        if time.monotonic() - last_new >= args.idle_seconds:
            entry["stop_reason"] = "idle_timeout" if seen else "empty_or_unrecognised_page"
            break
        if iteration == args.max_scrolls:
            entry["stop_reason"] = "scroll_limit"
            break
        set_stage("scrolling")
        page.mouse.move(550, 600)  # inside the fixed 1100×850 result viewport
        page.mouse.wheel(0, 650)
    print(f"{query}／{entry['sort']}：停止原因 {entry['stop_reason']}；累計保存 {len(records)} 篇。", flush=True)
    return None


def collect(args) -> int:
    from playwright.sync_api import sync_playwright, Error as PlaywrightError
    from threads_web_report import build_reports

    first, last = validate_period(args.start_date, args.end_date)
    removed = purge_expired()
    print(f"清除到期資料檔案：{removed}。登入憑證不會另存或匯出。", flush=True)
    started = utc_now()
    run_id = started.strftime("%Y%m%dT%H%M%SZ_") + uuid.uuid4().hex[:8]
    folder = f"web_sample_{first.isoformat()}_{last.isoformat()}"
    raw = RAW_ROOT / folder / run_id
    output = OUTPUT_ROOT / folder / run_id
    raw.mkdir(parents=True, exist_ok=False)
    manifest = dict(source_type=SOURCE, run_id=run_id, start_date=first.isoformat(), end_date=last.isoformat(),
                    timezone="Asia/Taipei", platform_total=False, coverage="convenience_sample",
                    started_at_utc=started.isoformat(), expires_at_utc=(started+timedelta(days=RETENTION_DAYS)).isoformat(),
                    status="starting", query_runs=[], retained_posts=0, date_filter="local_timestamp_check",
                    max_scrolls_per_pass=args.max_scrolls, max_posts=args.max_posts,
                    max_minutes_per_keyword=args.max_minutes, idle_seconds=args.idle_seconds,
                    sort_mode=args.sort, delay_seconds=args.delay,
                    notes="Search ranking/visibility can omit posts. Zero observed is not zero platform discussion.")
    write_json(raw / "manifest.json", manifest)
    records, interrupted, failure = {}, False, None
    stage, entry = "browser_launch", None

    def set_stage(value):
        nonlocal stage
        stage = value
        manifest["stage"] = value
        write_json(raw / "manifest.json", manifest)

    try:
        with sync_playwright() as p:
            options = dict(headless=False)
            if args.browser != "chromium":
                options["channel"] = args.browser
            browser = p.chromium.launch(**options)
            context = browser.new_context(locale="zh-TW", timezone_id="Asia/Taipei", viewport={"width":1100,"height":850},
                                          accept_downloads=False)
            page = context.new_page()
            page.set_default_timeout(15000)
            set_stage("homepage_navigation")
            page.goto("https://www.threads.com/", wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
            print("請在新開的瀏覽器自行登入 Threads；密碼、驗證碼不要輸入終端。", flush=True)
            set_stage("login_wait")
            wait_for_confirmation(context, "登入後回到這個終端按 Enter 開始（Ctrl+C 取消）：")
            set_stage("login_handoff")
            print("正在確認登入後的 Threads 分頁…", flush=True)
            page = ready_threads_page(context, browser)
            queries = args.query or list(DEFAULT_QUERIES)
            budget_seconds = args.max_minutes * 60
            print(f"每個關鍵字只抓最相關，最多採集 {args.max_minutes:g} 分鐘；"
                  f"連續 {args.idle_seconds:g} 秒沒有新卡片才提早停止。Ctrl+C 可中止並保存。", flush=True)
            for query in queries:
                sort = "top"
                if len(records) >= args.max_posts:
                    break
                entry = dict(query_text=query, sort=sort, snapshots=0, seen_cards=0, stop_reason=None,
                             budget_seconds=budget_seconds,
                             server_date_filter="not_applied", search_parameters={})
                manifest["query_runs"].append(entry)
                set_stage("search_navigation")
                print(f"正在開啟搜尋：{query}／{sort}（最多等待45秒）…", flush=True)
                page.goto(search_url(query, sort), wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
                if args.manual_filters:
                    set_stage("manual_filters")
                    print(f"搜尋：{query}。可自行設定日期篩選，保留搜尋結果頁。", flush=True)
                    wait_for_confirmation(context, "設定完成後按 Enter（仍會逐筆檢查臺北日期）：")
                    page = ready_threads_page(context, browser)
                    entry["server_date_filter"] = "user_configured_unverified"
                entry["search_parameters"] = safe_search_description(page.url)
                if not entry["search_parameters"]["on_search_page"]:
                    entry["stop_reason"] = "not_search_page"
                    failure = "not_search_page"
                    break
                failure = sample_search_page(page, query, first, last, args, entry, records,
                                             raw, manifest, set_stage, budget_seconds)
                if failure:
                    break
            set_stage("browser_cleanup")
            context.close()
            browser.close()
    except (KeyboardInterrupt, EOFError):
        interrupted = True
    except BrowserHandoffError as exc:
        failure = str(exc)  # Only locally authored fixed reason codes.
        manifest["error"] = {"stage": stage, "category": failure}
    except PlaywrightError as exc:
        failure = "browser_error"
        manifest["error"] = safe_browser_error(exc, stage)
    finally:
        if entry is not None and entry["stop_reason"] is None and (failure or interrupted):
            entry["stop_reason"] = failure or "interrupted"
        save_records(raw, records)
        manifest.update(status=failure or ("interrupted" if interrupted else "sampled"),
                        finished_at_utc=utc_now().isoformat(), retained_posts=len(records))
        write_json(raw / "manifest.json", manifest)
    summary = build_reports(raw / "posts.jsonl", output, first.isoformat(), last.isoformat())
    print(f"原始資料：{raw}\n報表：{output}", flush=True)
    print(f"本次狀態：{manifest['status']}。這是可見搜尋樣本，不代表完整聲量。", flush=True)
    if failure:
        print(failure_explanation(manifest), flush=True)
        print(f"安全錯誤紀錄：{raw / 'manifest.json'}", flush=True)
    elif interrupted:
        print("本次採集已中止，不能判定該期間沒有資料。", flush=True)
    elif not records:
        print("本次可見搜尋樣本未保留期間內貼文；可檢查日期篩選與頁面結構，不代表全平台沒有討論。")
    return 1 if failure or interrupted else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default="2026-07-15")
    parser.add_argument("--end-date", default="2026-08-15")
    parser.add_argument("--query", action="append", help="可重複指定；預設大稻埕 煙火、大稻埕")
    parser.add_argument("--sort", choices=("top",), default="top", help="僅保留最相關排序")
    parser.add_argument("--browser", choices=("msedge", "chrome", "chromium"), default="msedge")
    parser.add_argument("--max-scrolls", type=int, default=200, help="每個關鍵字／排序最多捲動次數（最多2000）")
    parser.add_argument("--max-posts", type=int, default=2000, help="最多保留的去重貼文數（最多20000）")
    parser.add_argument("--delay", type=float, default=4.0, help="畫面讀取間隔秒數，至少2秒")
    parser.add_argument("--max-minutes", type=float, default=15, help="每個關鍵字最相關排序採集分鐘上限（1–120）")
    parser.add_argument("--idle-seconds", type=float, default=90, help="連續沒有新卡片多久才提早停止（15–600秒）")
    parser.add_argument("--manual-filters", action="store_true", help="每個搜尋先讓使用者設定頁面日期篩選")
    parser.add_argument("--plan", action="store_true", help="只顯示計畫，不開瀏覽器")
    parser.add_argument("--purge", action="store_true", help="只清除本工具已到期的原文及衍生輸出")
    args = parser.parse_args(argv)
    try:
        first, last = validate_period(args.start_date, args.end_date)
        if (not 0 <= args.max_scrolls <= 2000 or not 1 <= args.max_posts <= 20000 or not 2 <= args.delay <= 60
                or not 1 <= args.max_minutes <= 120 or not 15 <= args.idle_seconds <= 600):
            raise ValueError("捲動次數需0–2000、貼文上限1–20000、間隔2–60秒、分鐘1–120、閒置15–600秒。")
        if args.query and (len(args.query)>5 or any(not q.strip() or len(q)>100 for q in args.query)):
            raise ValueError("每次最多5個非空關鍵字，各不超過100字。")
        if args.purge:
            print(f"已清除 {purge_expired()} 個到期檔案。")
            return 0
        if args.plan:
            print(json.dumps(dict(start_date=first.isoformat(), end_date=last.isoformat(),
                                  days=(last-first).days+1, timezone="Asia/Taipei", queries=args.query or DEFAULT_QUERIES,
                                  source=SOURCE, server_date_filter="manual_optional", date_check="local",
                                  sort=args.sort, max_scrolls_per_pass=args.max_scrolls, max_posts=args.max_posts,
                                  delay_seconds=args.delay, max_minutes_per_keyword=args.max_minutes,
                                  idle_seconds=args.idle_seconds,
                                  browser=args.browser, save_login=False, raw_root=str(RAW_ROOT)), ensure_ascii=False, indent=2))
            return 0
        return collect(args)
    except ImportError:
        print("缺少套件：請執行 uv sync --group notebook --extra social --extra web。", file=sys.stderr)
        return 2
    except (ValueError, OSError) as exc:
        print(f"本機設定或檔案錯誤（{type(exc).__name__}）；請檢查日期、套件與輸出資料夾。", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
