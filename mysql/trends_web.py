"""Small, unauthenticated client for Google Trends' website data endpoints.

These endpoints are not Google's supported Trends API. No browser credentials,
proxy rotation, CAPTCHA bypass, or TLS-verification override is used.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import http.cookiejar
import json
import re
import ssl
import time as clock
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import (
    Request, build_opener, ProxyHandler, HTTPSHandler,
    HTTPRedirectHandler, HTTPCookieProcessor,
)

TAIPEI = timezone(timedelta(hours=8))
TZ_MINUTES = -480
MAX_RESPONSE_BYTES = 2_000_000
QUANTUM = Decimal('0.0001')


class SourceError(RuntimeError):
    pass


class RateLimited(SourceError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GoogleTrendsClient:
    def __init__(self, delay_seconds: float = 15):
        if not 10 <= delay_seconds <= 60:
            raise ValueError('請求間隔須為 10–60 秒')
        self.delay_seconds = delay_seconds
        self.last_request = None
        self.opener = build_opener(
            ProxyHandler({}),
            HTTPSHandler(context=ssl.create_default_context()),
            NoRedirect(),
            HTTPCookieProcessor(http.cookiejar.CookieJar()),
        )

    def get_json(self, path: str, params: dict) -> dict:
        if path not in {'/trends/api/explore', '/trends/api/widgetdata/multiline'}:
            raise ValueError('不允許的 Google Trends 路徑')
        if self.last_request is not None:
            wait = self.delay_seconds - (clock.monotonic() - self.last_request)
            if wait > 0:
                clock.sleep(wait)
        self.last_request = clock.monotonic()
        request = Request(
            'https://trends.google.com' + path + '?' + urlencode(params),
            headers={
                'Accept': 'application/json',
                'User-Agent': 'DadaochengResearch/1.0',
            },
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                content_type = response.headers.get_content_type()
                if content_type not in {'application/json', 'application/javascript', 'text/javascript'}:
                    raise SourceError('Google 沒有回傳 JSON，可能需要人工驗證；已停止')
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            code = exc.code
            retry_after = exc.headers.get('Retry-After', '')
            exc.close()
            if code == 429:
                suffix = f'；伺服器要求至少等 {retry_after} 秒' if retry_after.isdigit() else ''
                raise RateLimited('Google 回傳 HTTP 429，停止後續請求並保留已完成快取' + suffix) from None
            if code in {401, 403} or 300 <= code < 400:
                raise RateLimited(f'Google 回傳 HTTP {code}，停止請求；不嘗試繞過存取或驗證限制') from None
            raise SourceError(f'Google 回傳 HTTP {code}，未取得資料') from None
        except (URLError, TimeoutError, OSError):
            # Do not echo exception URLs: query tokens can be part of those URLs.
            raise SourceError('Google 連線、TLS 驗證或讀取逾時；未取得資料') from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise SourceError('Google 回傳內容超過大小上限')
        try:
            text = raw.decode('utf-8')
            if text.startswith(")]}'"):
                text = text.split('\n', 1)[1]
            result = json.loads(text)
        except (ValueError, UnicodeError, IndexError):
            raise SourceError('Google JSON 格式已變更，停止解析') from None
        if not isinstance(result, dict):
            raise SourceError('Google JSON 根節點格式不符')
        return result

    def fetch(self, query: dict) -> dict:
        comparison = {
            'comparisonItem': [{
                'keyword': query['keyword'], 'geo': query['geo'],
                'time': query['start'] + ' ' + query['end'],
            }],
            'category': 0, 'property': '',
        }
        explore = self.get_json('/trends/api/explore', {
            'hl': 'en-US', 'tz': TZ_MINUTES,
            'req': json.dumps(comparison, ensure_ascii=False),
        })
        widgets = explore.get('widgets', [])
        widget = next((w for w in widgets if isinstance(w, dict) and w.get('id') == 'TIMESERIES'), None)
        if widget is None:
            raise SourceError('Google 未提供時間序列；不能以 0 代替缺資料')
        source_request = widget.get('request')
        token = widget.get('token')
        if not isinstance(source_request, dict) or not isinstance(token, str):
            raise SourceError('Google 圖表參數格式已變更')
        resolution = source_request.get('resolution')
        if resolution not in {'DAY', 'HOUR'}:
            raise SourceError(f'Google 回傳的時間粒度不支援：{resolution!r}')
        series = self.get_json('/trends/api/widgetdata/multiline', {
            'hl': 'en-US', 'tz': TZ_MINUTES,
            'req': json.dumps(source_request, ensure_ascii=False), 'token': token,
        })
        # Tokens and cookies are intentionally omitted from the saved artifact.
        default = series.get('default')
        if not isinstance(default, dict):
            raise SourceError('Google 時間序列 JSON 格式已變更')
        return {
            'schema_version': 1,
            'query': query,
            'fetched_at_utc': datetime.now(timezone.utc).isoformat(),
            'source_resolution': resolution,
            'timeline_data': default.get('timelineData', []),
        }


@dataclass(frozen=True)
class DailySeries:
    scores: dict[date, Decimal]
    samples_in_day: dict[date, int]
    time_basis: str
    source_resolution: str


def query_spec(keyword: str, geo: str, start: date, end: date) -> dict:
    return {
        'keyword': keyword, 'geo': geo,
        'start': start.isoformat(), 'end': end.isoformat(),
        'timezone_offset_minutes': TZ_MINUTES, 'category': 0, 'property': '',
    }


def parse_series(payload: dict, expected_query: dict) -> DailySeries:
    if payload.get('schema_version') != 1 or payload.get('query') != expected_query:
        raise ValueError('快取的查詢字詞、地區、日期或時區不符')
    start, end = (date.fromisoformat(expected_query[key]) for key in ('start', 'end'))
    if (end - start).days != 6:
        raise ValueError('查詢必須恰好涵蓋活動前 7 個日期')
    expected_days = {start + timedelta(days=i) for i in range(7)}
    resolution = payload.get('source_resolution')
    rows = payload.get('timeline_data')
    if not isinstance(rows, list) or not rows or len(rows) > 2000:
        raise SourceError('Google 未提供有效時間序列；不能以 0 代替缺資料')
    samples = {}
    for row in rows:
        if not isinstance(row, dict) or not re.fullmatch(r'\d{9,12}', str(row.get('time', ''))):
            raise ValueError('來源時間戳記格式不正確')
        stamp = int(row['time'])
        values = row.get('value')
        if not isinstance(values, list) or len(values) != 1 or type(values[0]) is not int or not 0 <= values[0] <= 100:
            raise ValueError('來源必須只有一個關鍵字，且聲量為 0–100 整數')
        if stamp in samples:
            raise ValueError('來源有重複時間戳記')
        partial = row.get('isPartial', False)
        if partial is True or (isinstance(partial, list) and any(partial)):
            raise SourceError('來源含未完成時段，暫不計算平均')
        has_data = row.get('hasData', [True])
        if has_data is False or (isinstance(has_data, list) and not all(has_data)):
            raise SourceError('來源含無資料時段，暫不計算平均')
        samples[stamp] = values[0]
    scores, counts = {}, {}
    if resolution == 'DAY':
        moments = [datetime.fromtimestamp(stamp, timezone.utc) for stamp in samples]
        if all(moment.astimezone(TAIPEI).time() == time(0) for moment in moments):
            basis, zone = 'Asia/Taipei', TAIPEI
        elif all(moment.time() == time(0) for moment in moments):
            basis, zone = 'UTC', timezone.utc
        else:
            raise ValueError('無法確認來源日資料的日界線，停止避免錯配日期')
        for stamp, value in samples.items():
            day = datetime.fromtimestamp(stamp, zone).date()
            if day in scores:
                raise ValueError('來源日資料有重複日期')
            scores[day] = Decimal(value)
            counts[day] = 1
        if set(scores) != expected_days:
            raise ValueError('來源不是完整的 7 日資料；不把週資料或缺日補成日資料')
    elif resolution == 'HOUR':
        basis = 'Asia/Taipei'
        first = int(datetime.combine(start, time(0), tzinfo=TAIPEI).timestamp())
        expected_stamps = {first + 3600 * i for i in range(168)}
        if set(samples) != expected_stamps:
            raise ValueError('小時資料未完整涵蓋臺灣時間前 7 日的 168 小時，停止計算')
        for day in sorted(expected_days):
            values = [value for stamp, value in samples.items() if datetime.fromtimestamp(stamp, TAIPEI).date() == day]
            scores[day] = (Decimal(sum(values)) / 24).quantize(QUANTUM, rounding=ROUND_HALF_UP)
            counts[day] = 24
    else:
        raise ValueError('只接受 DAY 或 HOUR 資料')
    return DailySeries(scores, counts, basis, resolution)
