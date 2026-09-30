"""Automatically fetch preceding 7 days for held/cancelled/postponed fireworks.

    python mysql/trends_features.py plan
    python mysql/trends_features.py fetch --commit
    python mysql/trends_features.py ingest --commit  # saved JSON only
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import quote, urlencode

from trends_web import (
    DailySeries, GoogleTrendsClient, MAX_RESPONSE_BYTES, QUANTUM,
    RateLimited, SourceError, parse_series, query_spec,
)

ROOT = Path(__file__).resolve().parents[1]
MYSQL = Path(r'C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe')
DEFAULT_KEYWORD = '大稻埕煙火'
DEFAULT_GEO = 'TW'
WINDOW_DAYS = 7
EVENT_STATUSES = {'held', 'cancelled', 'postponed'}


@dataclass(frozen=True)
class Event:
    event_date: date
    status: str


@dataclass(frozen=True)
class ExportPlan:
    event: Event
    start: date
    end: date
    url: str

    @property
    def year(self) -> int:
        return self.event.event_date.year


@dataclass(frozen=True)
class ImportData:
    plan: ExportPlan
    sha256: str
    series: DailySeries


@dataclass(frozen=True)
class Feature:
    event_date: date
    interest_1d: Decimal
    interest_3d_avg: Decimal
    interest_7d_avg: Decimal


def mysql(sql: str, login_path: str) -> str:
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', login_path):
        raise ValueError('login path 只能含英數字、底線或連字號')
    if not MYSQL.is_file():
        raise RuntimeError('找不到 MySQL CLI；請修改程式開頭的 MYSQL 路徑')
    try:
        result = subprocess.run(
            [str(MYSQL), f'--login-path={login_path}', '--default-character-set=utf8mb4',
             '--connect-timeout=10', '--local-infile=0', '--batch', '--raw', '--silent', '--skip-column-names'],
            input=sql.encode('utf-8'), capture_output=True, timeout=60, check=False,
            env={key: value for key, value in os.environ.items() if key.upper() != 'MYSQL_PWD'},
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError('MySQL 執行逾時') from None
    if result.returncode:
        raise RuntimeError(f'MySQL 執行失敗（代碼 {result.returncode}）；請檢查 login path、權限與資料表版本')
    return result.stdout.decode('utf-8', errors='strict').strip()


def read_events(login_path: str) -> list[Event]:
    rows = mysql(
        "SELECT DATE_FORMAT(event_date, '%Y-%m-%d'), event_status "
        'FROM taipei_metro_analysis.dadaocheng_fireworks_events ORDER BY event_date;', login_path,
    )
    events = []
    for row in rows.splitlines():
        day, status = row.split('\t')
        if status not in EVENT_STATUSES:
            raise ValueError('MySQL 出現尚未支援的活動狀態')
        events.append(Event(date.fromisoformat(day), status))
    return events


def make_plans(events: list[Event], keyword: str, geo: str) -> list[ExportPlan]:
    if not keyword or len(keyword) > 120 or any(ord(ch) < 32 for ch in keyword):
        raise ValueError('關鍵字須為 1–120 個字且不可含控制字元')
    if not re.fullmatch(r'[A-Z]{2}(?:-[A-Z0-9]{1,5})?', geo):
        raise ValueError('地區代碼格式不正確，例如 TW')
    plans = []
    for event in sorted(events, key=lambda item: item.event_date):
        start = event.event_date - timedelta(days=7)
        end = event.event_date - timedelta(days=1)
        url = 'https://trends.google.com/trends/explore?' + urlencode(
            {'date': f'{start} {end}', 'geo': geo, 'q': keyword, 'hl': 'zh-TW'}, quote_via=quote,
        )
        plans.append(ExportPlan(event, start, end, url))
    return plans


def compute_features(item: ImportData) -> Feature:
    event = item.plan.event.event_date
    values = [item.series.scores[event - timedelta(days=n)] for n in range(1, 8)]
    avg3 = (sum(values[:3]) / 3).quantize(QUANTUM, rounding=ROUND_HALF_UP)
    avg7 = (sum(values) / 7).quantize(QUANTUM, rounding=ROUND_HALF_UP)
    return Feature(event, values[0], avg3, avg7)


def sql_text(value: str) -> str:
    # CLI has no prepared-statement interface; text is a hex literal, not SQL.
    return 'CONVERT(0x' + value.encode('utf-8').hex() + ' USING utf8mb4)'


def build_insert_sql(items: list[ImportData], keyword: str, geo: str) -> str:
    statements = ['START TRANSACTION;']
    for item in items:
        plan, series = item.plan, item.series
        statements.append(
            'INSERT INTO taipei_metro_analysis.google_trends_imports '
            '(query_year, query_text, geo_code, period_start, period_end, source_sha256, source_url, '
            'time_basis, source_kind, source_resolution) VALUES '
            f'({plan.year}, {sql_text(keyword)}, {sql_text(geo)}, '
            f"'{plan.start}', '{plan.end}', '{item.sha256}', {sql_text(plan.url)}, "
            f"{sql_text(series.time_basis)}, 'explore_web_json', {sql_text(series.source_resolution)}) "
            'ON DUPLICATE KEY UPDATE import_id = LAST_INSERT_ID(import_id);'
        )
        statements.append('SET @trends_import_id := LAST_INSERT_ID();')
        daily_rows = ',\n'.join(
            f"(@trends_import_id, '{day}', {score}, {series.samples_in_day[day]})"
            for day, score in sorted(series.scores.items())
        )
        statements.append(
            'INSERT INTO taipei_metro_analysis.google_trends_daily '
            '(import_id, search_date, interest_score, samples_in_day) VALUES\n' + daily_rows + '\n'
            'ON DUPLICATE KEY UPDATE interest_score = VALUES(interest_score), samples_in_day = VALUES(samples_in_day);'
        )
        feature = compute_features(item)
        statements.append(
            'INSERT INTO taipei_metro_analysis.event_trends_features '
            '(event_date, query_text, geo_code, import_id, interest_1d, interest_3d_avg, interest_7d_avg) VALUES '
            f"('{feature.event_date}', {sql_text(keyword)}, {sql_text(geo)}, @trends_import_id, "
            f'{feature.interest_1d}, {feature.interest_3d_avg}, {feature.interest_7d_avg}) '
            'ON DUPLICATE KEY UPDATE import_id = VALUES(import_id), interest_1d = VALUES(interest_1d), '
            'interest_3d_avg = VALUES(interest_3d_avg), interest_7d_avg = VALUES(interest_7d_avg);'
        )
    statements.extend(['COMMIT;', 'SELECT COUNT(*) FROM taipei_metro_analysis.event_trends_features;'])
    return '\n'.join(statements)


def cache_path(cache_dir: Path, plan: ExportPlan, query: dict) -> Path:
    key = hashlib.sha256(json.dumps(query, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()[:20]
    return cache_dir / f'trends_{plan.event.event_date:%Y%m%d}_{key}.json'


def atomic_write(path: Path, raw: bytes) -> None:
    if path.is_symlink():
        raise ValueError('不覆寫符號連結')
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='trends_', suffix='.tmp', delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(raw)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_cache(path: Path, query: dict, plan: ExportPlan) -> ImportData:
    if path.is_symlink() or not 0 < path.stat().st_size <= MAX_RESPONSE_BYTES:
        raise ValueError('快取檔案類型或大小不正確')
    raw = path.read_bytes()
    payload = json.loads(raw.decode('utf-8'))
    if not isinstance(payload, dict):
        raise ValueError('快取 JSON 格式不正確')
    return ImportData(plan, hashlib.sha256(raw).hexdigest(), parse_series(payload, query))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('plan', 'fetch', 'ingest'))
    parser.add_argument('--keyword', default=DEFAULT_KEYWORD)
    parser.add_argument('--geo', default=DEFAULT_GEO)
    parser.add_argument('--login-path', default='codex-local')
    parser.add_argument('--year', type=int, action='append')
    parser.add_argument('--event-date', type=date.fromisoformat, action='append')
    parser.add_argument('--status', choices=sorted(EVENT_STATUSES), action='append', help='預設納入所有狀態')
    parser.add_argument('--cache-dir', type=Path, default=ROOT / 'data' / 'trends' / 'web')
    parser.add_argument('--delay', type=float, default=15, help='請求間隔秒數，10–60')
    parser.add_argument('--commit', action='store_true', help='每場驗證通過後自動寫入 MySQL')
    args = parser.parse_args()
    if args.command == 'plan' and args.commit:
        parser.error('plan 不接受 --commit')
    if not 10 <= args.delay <= 60:
        parser.error('--delay 須為 10–60 秒')
    events = [event for event in read_events(args.login_path)
              if (not args.year or event.event_date.year in args.year)
              and (not args.event_date or event.event_date in args.event_date)
              and (not args.status or event.status in args.status)]
    if not events:
        raise ValueError('MySQL 沒有符合條件的活動日期')
    plans = make_plans(events, args.keyword, args.geo)
    print(f'共 {len(events)} 個原訂／實際日期：' + '、'.join(
        f'{status}={sum(event.status == status for event in events)}' for status in sorted(EVENT_STATUSES)
    ), flush=True)
    if args.command == 'plan':
        for plan in plans:
            print(f'\n{plan.event.event_date} [{plan.event.status}]：{plan.start}～{plan.end}')
            print(plan.url)
        return 0
    if args.commit:
        mysql('SELECT source_sha256, source_resolution FROM taipei_metro_analysis.google_trends_imports LIMIT 0;', args.login_path)
    directory = args.cache_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    client = GoogleTrendsClient(args.delay) if args.command == 'fetch' else None
    report = []
    failed, stopped = False, False
    for number, plan in enumerate(plans, 1):
        query = query_spec(args.keyword, args.geo, plan.start, plan.end)
        path = cache_path(directory, plan, query)
        result = {'event_date': str(plan.event.event_date), 'event_status': plan.event.status}
        print(f'[{number}/{len(plans)}] {plan.event.event_date} [{plan.event.status}]', flush=True)
        try:
            if not path.exists():
                if client is None:
                    raise SourceError('缺少快取；請先執行 fetch')
                payload = client.fetch(query)
                parse_series(payload, query)
                raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')
                atomic_write(path, raw)
            item = read_cache(path, query, plan)
            feature = compute_features(item)
            if args.commit:
                mysql(build_insert_sql([item], args.keyword, args.geo), args.login_path)
            result.update({'state': 'imported' if args.commit else 'validated', 'cache': path.name,
                           'source_resolution': item.series.source_resolution, 'time_basis': item.series.time_basis})
            print(f'  {item.series.source_resolution}/{item.series.time_basis}  '
                  f'1日={feature.interest_1d:.4f} 3日均={feature.interest_3d_avg} 7日均={feature.interest_7d_avg}', flush=True)
        except RateLimited as exc:
            failed, stopped = True, True
            result.update({'state': 'rate_limited', 'reason': str(exc)})
            print(f'  {exc}', flush=True)
        except (SourceError, ValueError, OSError) as exc:
            failed = True
            result.update({'state': 'failed', 'reason': str(exc)})
            print(f'  未取得可用資料：{exc}', flush=True)
        report.append(result)
        atomic_write(directory / 'last_run.json', json.dumps({
            'keyword': args.keyword, 'geo': args.geo, 'commit': args.commit,
            'selected_events': len(plans), 'remaining_events': len(plans) - number,
            'results': report,
        }, ensure_ascii=False, indent=2).encode('utf-8'))
        if stopped:
            break
    completed = sum(row['state'] in {'imported', 'validated'} for row in report)
    print(f'完成 {completed}/{len(plans)} 場；' + (
        f'本次成功寫入 {completed} 場。' if args.commit else '未寫入 MySQL，加 --commit 可寫入。'
    ))
    return 2 if failed else 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError) as exc:
        print(f'處理失敗：{exc}', file=sys.stderr)
        raise SystemExit(1)
