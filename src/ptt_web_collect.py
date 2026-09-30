"""Collect a bounded PTT board-title-search sample from public HTML.

No login, saved browser credentials, age-gate cookies, private APIs, or automatic
retries. Article publication headers determine the date; URL timestamps only
reduce unnecessary article requests. Comments are deliberately excluded.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.robotparser import RobotFileParser
import uuid

from bs4 import BeautifulSoup
import requests

ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = ROOT / "data/original_data/ptt"
OUTPUT_ROOT = ROOT / "results/ptt"
ORIGIN = "https://www.ptt.cc"
SOURCE = "ptt_public_web"
TAIPEI = timezone(timedelta(hours=8))
DEFAULT_BOARDS = ("Gossiping", "MRT", "WomenTalk", "Taipei")
USER_AGENT = "DadaochengResearch/1.0 (public article research; low-rate requests)"
RETENTION_DAYS = 90
MAX_RESPONSE_BYTES = 3 * 1024 * 1024
ARTICLE_PATH = re.compile(r"/bbs/([A-Za-z0-9_-]+)/((?:M|G)\.(\d{9,11})\.A\.[A-Za-z0-9]+)\.html")
MONTHS = {name: number for number, name in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}


def utc_now():
    return datetime.now(timezone.utc)


def validate_period(start, end):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first.isoformat() != start or last.isoformat() != end or not 0 <= (last-first).days <= 366:
        raise ValueError("日期需 YYYY-MM-DD，依序設定且單次不超過367天。")
    return first, last


def safe_ptt_url(value, board=None):
    """Only public PTT pages; never follow an external or credential-bearing URL."""
    try:
        parsed = urlsplit(urljoin(ORIGIN, value))
        if (parsed.scheme != "https" or parsed.hostname != "www.ptt.cc" or parsed.username
                or parsed.password or parsed.port not in (None, 443)):
            return None
    except ValueError:
        return None
    if parsed.path == "/robots.txt" and board is None:
        return ORIGIN + "/robots.txt"
    if not re.fullmatch(r"/bbs/[A-Za-z0-9_-]+/(?:search|[MG]\.\d{9,11}\.A\.[A-Za-z0-9]+\.html)", parsed.path):
        return None
    if board is not None and parsed.path.split("/")[2] != board:
        return None
    if parsed.path.endswith("/search"):
        params = parse_qs(parsed.query)
        if set(params) - {"q", "page"} or any(len(v) != 1 for v in params.values()):
            return None
        if "page" in params and not re.fullmatch(r"\d{1,6}", params["page"][0]):
            return None
        return ORIGIN + parsed.path + ("?" + urlencode({k:v[0] for k,v in params.items()}) if params else "")
    return ORIGIN + parsed.path


def article_identity(url):
    safe = safe_ptt_url(url)
    match = ARTICLE_PATH.fullmatch(urlsplit(safe).path) if safe else None
    return (match[1], match[2], int(match[3])) if match else None


def search_url(board, query):
    return f"{ORIGIN}/bbs/{board}/search?" + urlencode({"q": query})


def parse_search(html, board, query):
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one('form[action*="over18"]'):
        raise CollectionStop("age_confirmation_required")
    if soup.select_one(".r-list-container") is None:
        raise CollectionStop("unrecognised_search_page")
    urls = []
    for anchor in soup.select(".r-ent .title a[href]"):
        url = safe_ptt_url(anchor["href"], board)
        if url and article_identity(url) and url not in urls:
            urls.append(url)
    older = None
    for anchor in soup.select(".btn-group-paging a[href]"):
        if "上頁" not in anchor.get_text():
            continue
        candidate = safe_ptt_url(anchor["href"], board)
        if candidate and parse_qs(urlsplit(candidate).query).get("q") == [query]:
            older = candidate
            break
    return urls, older


def parse_header_time(value):
    # Locale-independent: Windows may not use English month names by default.
    match = re.fullmatch(r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+([A-Z][a-z]{2})\s+(\d{1,2})\s+"
                         r"(\d{2}):(\d{2}):(\d{2})\s+(\d{4})", value.strip())
    if not match or match[1] not in MONTHS:
        return None
    try:
        return datetime(int(match[6]), MONTHS[match[1]], int(match[2]), int(match[3]),
                        int(match[4]), int(match[5]), tzinfo=TAIPEI)
    except ValueError:
        return None


def parse_article(html, url, query, collected):
    identity = article_identity(url)
    if identity is None:
        raise CollectionStop("invalid_article_url")
    board, aid, _ = identity
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one('form[action*="over18"]'):
        raise CollectionStop("age_confirmation_required")
    main = soup.select_one("#main-content")
    if main is None:
        raise CollectionStop("unrecognised_article_page")
    metadata = {}
    for line in main.select(".article-metaline"):
        key, value = line.select_one(".article-meta-tag"), line.select_one(".article-meta-value")
        if key and value:
            metadata[key.get_text(strip=True)] = value.get_text(strip=True)
    stamp = parse_header_time(metadata.get("時間", ""))
    title = metadata.get("標題", "")
    for element in main.select(".article-metaline, .article-metaline-right, .push, .f2, .richcontent, script, style, noscript"):
        element.decompose()
    body = main.get_text("\n", strip=True)
    body = re.split(r"\n--\s*\n", body, maxsplit=1)[0]
    body = re.sub(r"(?m)^※\s*(?:發信站|文章網址|編輯|轉錄者|轉錄至).*$", "", body)
    body = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "", body)
    text = title + "\n" + body
    related = "大稻埕" in text and bool(re.search(r"煙火|烟火|花火|夏日節|夏日节", text))
    relevance = "include" if related else "review" if "大稻埕" in text else "exclude"
    reason = "event_terms_in_title_or_body" if related else (
        "place_context_requires_review" if relevance == "review" else "not_event_related")
    complete = bool(title and body)
    if stamp is None or not complete:
        relevance, reason = "review", "unknown_date" if stamp is None else "missing_title_or_body"
    return dict(post_id=f"{board}/{aid}", board=board, title=title, text=text.strip(),
                permalink=safe_ptt_url(url), published_at_utc=stamp.astimezone(timezone.utc).isoformat() if stamp else None,
                published_date_taipei=stamp.date().isoformat() if stamp else None,
                date_precision="exact_timestamp" if stamp else "uncertain",
                date_display_raw=metadata.get("時間", ""), collected_at_utc=collected.isoformat(),
                query_text=query, matched_queries=[query], text_complete=complete,
                relevance=relevance, relevance_reason=reason, source_type=SOURCE,
                text_processing="title_and_body_without_comments_metadata_signature")


class CollectionStop(Exception):
    """Contains only a fixed local reason code; never raw network error text."""


class PTTClient:
    def __init__(self, delay, deadline):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.delay, self.deadline, self.last_request = delay, deadline, None
        self.robots, self.requests_made = None, 0

    def get(self, url, *, robots_request=False):
        target = safe_ptt_url(url)
        if target is None:
            raise CollectionStop("unsafe_url")
        for _ in range(3):
            if not robots_request and self.robots and not self.robots.can_fetch(USER_AGENT, target):
                raise CollectionStop("robots_disallowed")
            delay = max(0, self.delay - (time.monotonic()-self.last_request)) if self.last_request else 0
            if time.monotonic()+delay >= self.deadline:
                raise CollectionStop("time_limit")
            if delay:
                time.sleep(delay)
            self.last_request = time.monotonic()
            self.requests_made += 1
            try:
                with self.session.get(target, timeout=(10, 20), allow_redirects=False, stream=True) as response:
                    if 300 <= response.status_code < 400:
                        destination = urljoin(target, response.headers.get("Location", ""))
                        if urlsplit(destination).path.startswith("/ask/over18"):
                            raise CollectionStop("age_confirmation_required")
                        target = safe_ptt_url(destination)
                        if target is None:
                            raise CollectionStop("external_or_unexpected_redirect")
                        continue
                    if response.status_code == 404:
                        return None
                    if response.status_code == 429:
                        raise CollectionStop("rate_limited")
                    if response.status_code in (401, 403):
                        raise CollectionStop("access_restricted")
                    if response.status_code != 200:
                        raise CollectionStop("http_error")
                    pieces, size = [], 0
                    for chunk in response.iter_content(65536):
                        size += len(chunk)
                        if size > MAX_RESPONSE_BYTES:
                            raise CollectionStop("response_too_large")
                        if time.monotonic() >= self.deadline:
                            raise CollectionStop("time_limit")
                        pieces.append(chunk)
                    return b"".join(pieces).decode("utf-8", errors="replace")
            except requests.RequestException:
                raise CollectionStop("network_error") from None
        raise CollectionStop("redirect_limit")

    def check_robots(self):
        content = self.get(ORIGIN+"/robots.txt", robots_request=True)
        self.robots = RobotFileParser()
        self.robots.parse((content or "User-agent: *\nDisallow:").splitlines())
        return "not_found_404" if content is None else "checked"

    def close(self):
        self.session.close()


def write_json(path, value):
    temp = path.with_suffix(path.suffix+".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def save_records(folder, records):
    folder.mkdir(parents=True, exist_ok=True)
    rows = sorted(records.values(), key=lambda r:(r.get("published_date_taipei") or "", r["post_id"]))
    temp = folder / "posts.jsonl.tmp"
    temp.write_text("".join(json.dumps(row, ensure_ascii=False)+"\n" for row in rows), encoding="utf-8")
    temp.replace(folder / "posts.jsonl")
    fields = ("post_id", "board", "title", "text", "permalink", "published_at_utc", "published_date_taipei",
              "date_precision", "relevance", "relevance_reason", "text_complete", "matched_queries",
              "collected_at_utc", "source_type")
    with (folder / "posts.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            clean = {}
            for key in fields:
                value = row.get(key)
                if isinstance(value, list):
                    value = json.dumps(value, ensure_ascii=False)
                if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                    value = "'" + value
                clean[key] = value
            writer.writerow(clean)


def purge_expired(now=None):
    now, removed = now or utc_now(), 0
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
        report_dir = OUTPUT_ROOT / manifest_path.parent.relative_to(RAW_ROOT)
        for folder, names, root in ((manifest_path.parent, ("posts.jsonl", "posts.csv", "posts.jsonl.tmp"), RAW_ROOT),
                                   (report_dir, ("wordcloud.png", "words.csv", "daily_observed_counts.csv", "summary.json"), OUTPUT_ROOT)):
            for name in names:
                path = folder / name
                if path.resolve().is_relative_to(root.resolve()) and path.is_file():
                    path.unlink()
                    removed += 1
        manifest.update(status="expired", retained_posts=0)
        write_json(manifest_path, manifest)
    return removed


def collect(args):
    from threads_web_report import build_reports
    first, last = validate_period(args.start_date, args.end_date)
    boards, queries = args.board or list(DEFAULT_BOARDS), args.query or ["大稻埕"]
    purge_expired()
    started = utc_now()
    run_id = started.strftime("%Y%m%dT%H%M%SZ_") + uuid.uuid4().hex[:8]
    relative = Path(f"web_sample_{args.start_date}_{args.end_date}") / run_id
    raw, output = RAW_ROOT / relative, OUTPUT_ROOT / relative
    raw.mkdir(parents=True, exist_ok=False)
    manifest = dict(source_type=SOURCE, run_id=run_id, start_date=args.start_date, end_date=args.end_date,
                    timezone="Asia/Taipei", started_at_utc=started.isoformat(),
                    expires_at_utc=(started+timedelta(days=RETENTION_DAYS)).isoformat(), status="running",
                    boards=boards, queries=queries, query_runs=[], retained_posts=0, platform_total=False,
                    search_scope="selected_board_title_search", comments_included=False,
                    date_check="article_header_taipei", url_timestamp_prefilter_padding_days=1,
                    max_pages=args.max_pages, max_articles=args.max_articles,
                    delay_seconds=args.delay, max_minutes=args.max_minutes)
    records, visited_articles = {}, set()
    client = PTTClient(args.delay, time.monotonic()+args.max_minutes*60)
    failure, entry = None, None
    write_json(raw / "manifest.json", manifest)
    try:
        manifest["robots"] = client.check_robots()
        for board in boards:
            for query in queries:
                entry = dict(board=board, query=query, search_pages=0, search_links=0, article_requests=0,
                             url_prefilter_skipped=0, missing_articles=0, header_outside_period=0,
                             retained_posts=0, stop_reason=None)
                manifest["query_runs"].append(entry)
                page_url, seen_pages = search_url(board, query), set()
                for _ in range(args.max_pages):
                    if page_url in seen_pages:
                        entry["stop_reason"] = "pagination_cycle"
                        break
                    seen_pages.add(page_url)
                    try:
                        html = client.get(page_url)
                        if html is None:
                            entry["stop_reason"] = "board_or_search_not_found"
                            break
                        links, older = parse_search(html, board, query)
                    except CollectionStop as exc:
                        if str(exc) in {"age_confirmation_required", "robots_disallowed", "unrecognised_search_page"}:
                            entry["stop_reason"] = str(exc)
                            break
                        raise
                    entry["search_pages"] += 1
                    entry["search_links"] += len(links)
                    for url in links:
                        identity = article_identity(url)
                        post_id = f"{identity[0]}/{identity[1]}"
                        if post_id in records:
                            records[post_id]["matched_queries"] = sorted(set(records[post_id]["matched_queries"]+[query]))
                            continue
                        if url in visited_articles:
                            continue
                        # This is a request-saving prefilter, never the final date label.
                        try:
                            hint = datetime.fromtimestamp(identity[2], TAIPEI).date() if identity[1].startswith("M.") else None
                        except (OverflowError, OSError, ValueError):
                            hint = None
                        if hint and not first-timedelta(days=1) <= hint <= last+timedelta(days=1):
                            entry["url_prefilter_skipped"] += 1
                            continue
                        if len(visited_articles) >= args.max_articles:
                            raise CollectionStop("article_limit")
                        visited_articles.add(url)
                        entry["article_requests"] += 1
                        try:
                            article_html = client.get(url)
                            if article_html is None:
                                entry["missing_articles"] += 1
                                continue
                            row = parse_article(article_html, url, query, utc_now())
                        except CollectionStop as exc:
                            if str(exc) in {"age_confirmation_required", "unrecognised_article_page", "robots_disallowed"}:
                                entry.setdefault("article_omissions", Counter())[str(exc)] += 1
                                continue
                            raise
                        day = row["published_date_taipei"]
                        if day and not args.start_date <= day <= args.end_date:
                            entry["header_outside_period"] += 1
                            continue
                        records[post_id] = row
                        entry["retained_posts"] += 1
                        save_records(raw, records)
                        manifest["retained_posts"] = len(records)
                        write_json(raw / "manifest.json", manifest)
                    write_json(raw / "manifest.json", manifest)
                    print(f"{board}／{query}：搜尋第 {entry['search_pages']} 頁，累計保存 {len(records)} 篇。", flush=True)
                    if older is None:
                        entry["stop_reason"] = "search_end"
                        break
                    page_url = older
                if entry["stop_reason"] is None:
                    entry["stop_reason"] = "page_limit"
                print(f"{board}：{entry['stop_reason']}。", flush=True)
    except (KeyboardInterrupt, EOFError):
        failure = "interrupted"
    except CollectionStop as exc:
        failure = str(exc)
    finally:
        client.close()
        if entry is not None and entry["stop_reason"] is None and failure:
            entry["stop_reason"] = failure
        incomplete = any(e["stop_reason"] != "search_end" or e.get("article_omissions")
                         or e.get("missing_articles") for e in manifest["query_runs"])
        manifest.update(status=failure or ("partial_sample" if incomplete else "sampled"),
                        retained_posts=len(records), requests_made=client.requests_made,
                        finished_at_utc=utc_now().isoformat())
        save_records(raw, records)
        write_json(raw / "manifest.json", manifest)
    summary = build_reports(raw / "posts.jsonl", output, args.start_date, args.end_date, source_type=SOURCE)
    print(f"PTT 原始資料：{raw}\nPTT 報表：{output}", flush=True)
    print(f"狀態 {manifest['status']}；保存 {len(records)} 篇，可用 {summary['sample_post_count']} 篇。", flush=True)
    if not summary["wordcloud_created"]:
        print("沒有可用詞彙，因此未製作空白或虛構文字雲。", flush=True)
    print("這是指定看板的標題搜尋樣本，不代表全站；貼文內推噓文未納入。", flush=True)
    return 1 if failure else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default="2026-07-15")
    parser.add_argument("--end-date", default="2026-08-15")
    parser.add_argument("--board", action="append", help="可重複指定，預設 Gossiping、MRT、WomenTalk、Taipei")
    parser.add_argument("--query", action="append", help="可重複指定，預設大稻埕；搜尋看板文章標題")
    parser.add_argument("--max-pages", type=int, default=20, help="每個看板／關鍵字的搜尋頁上限")
    parser.add_argument("--max-articles", type=int, default=500, help="整次讀取文章頁上限")
    parser.add_argument("--delay", type=float, default=2, help="每次請求至少間隔2秒")
    parser.add_argument("--max-minutes", type=float, default=15, help="整次採集時間上限")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--purge", action="store_true")
    args = parser.parse_args(argv)
    try:
        validate_period(args.start_date, args.end_date)
        if (not 1 <= args.max_pages <= 100 or not 1 <= args.max_articles <= 2000
                or not 2 <= args.delay <= 60 or not 1 <= args.max_minutes <= 120):
            raise ValueError("頁數1–100、文章1–2000、間隔2–60秒、分鐘1–120。")
        if args.board and (len(args.board) > 12 or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", b) for b in args.board)):
            raise ValueError("看板最多12個，只接受英數、底線與連字號。")
        if args.query and (len(args.query) > 5 or any(not q.strip() or len(q) > 100 for q in args.query)):
            raise ValueError("最多5個非空關鍵字，各100字內。")
        if args.purge:
            print(f"已清除 {purge_expired()} 個 PTT 到期檔案。")
            return 0
        if args.plan:
            print(json.dumps(dict(start_date=args.start_date, end_date=args.end_date,
                                  boards=args.board or DEFAULT_BOARDS, queries=args.query or ["大稻埕"],
                                  source_type=SOURCE, comments_included=False, max_pages=args.max_pages,
                                  max_articles=args.max_articles, delay_seconds=args.delay,
                                  max_minutes=args.max_minutes), ensure_ascii=False, indent=2))
            return 0
        return collect(args)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"PTT 作業未完成（{type(exc).__name__}）；請檢查設定、套件與輸出路徑。")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
