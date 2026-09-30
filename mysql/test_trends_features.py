"""Date boundaries, aggregation, caching, and rate-limit safety checks."""
from contextlib import redirect_stdout
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from email.message import Message
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from trends_features import Event, ImportData, cache_path, compute_features, main, make_plans, read_events, sql_text
from trends_web import GoogleTrendsClient, RateLimited, SourceError, TAIPEI, parse_series, query_spec


def fixture(hourly=False, status='held'):
    event = Event(date(2017, 8, 26), status)
    plan = make_plans([event], '大稻埕煙火', 'TW')[0]
    query = query_spec('大稻埕煙火', 'TW', plan.start, plan.end)
    rows = []
    for index in range(168 if hourly else 7):
        if hourly:
            moment = datetime.combine(plan.start, time(0), tzinfo=TAIPEI) + timedelta(hours=index)
            score = 70 - (index // 24) * 10
        else:
            moment = datetime.combine(plan.start + timedelta(days=index), time(0), tzinfo=timezone.utc)
            score = 70 - index * 10
        rows.append({'time': str(int(moment.timestamp())), 'value': [score], 'hasData': [True]})
    payload = {'schema_version': 1, 'query': query, 'source_resolution': 'HOUR' if hourly else 'DAY', 'timeline_data': rows}
    return plan, query, payload


class TrendsFeaturesTests(unittest.TestCase):
    def test_all_event_statuses_are_read(self):
        with patch('trends_features.mysql', return_value='2020-08-22\tpostponed\n2020-08-29\theld\n2023-07-26\tcancelled'):
            self.assertEqual({event.status for event in read_events('codex-local')}, {'held', 'cancelled', 'postponed'})

    def test_seven_day_window_and_averages(self):
        plan, query, payload = fixture()
        self.assertEqual(plan.start, date(2017, 8, 19))
        self.assertEqual(plan.end, date(2017, 8, 25))
        series = parse_series(payload, query)
        result = compute_features(ImportData(plan, 'f' * 64, series))
        self.assertEqual((result.interest_1d, result.interest_3d_avg, result.interest_7d_avg), (10, 20, 40))
        self.assertEqual(series.time_basis, 'UTC')

    def test_hourly_values_use_taiwan_calendar_and_keep_decimals(self):
        plan, query, payload = fixture(hourly=True)
        payload['timeline_data'][-1]['value'] = [11]
        series = parse_series(payload, query)
        self.assertEqual(series.time_basis, 'Asia/Taipei')
        self.assertEqual(series.scores[plan.end], Decimal('10.0417'))
        self.assertTrue(all(count == 24 for count in series.samples_in_day.values()))

    def test_missing_hour_is_not_filled_with_zero(self):
        _, query, payload = fixture(hourly=True)
        payload['timeline_data'].pop()
        with self.assertRaisesRegex(ValueError, '168'):
            parse_series(payload, query)

    def test_missing_day_is_rejected(self):
        _, query, payload = fixture()
        payload['timeline_data'].pop()
        with self.assertRaisesRegex(ValueError, '完整的 7 日'):
            parse_series(payload, query)

    def test_duplicate_and_partial_periods_are_rejected(self):
        _, query, payload = fixture()
        payload['timeline_data'].append(payload['timeline_data'][0])
        with self.assertRaisesRegex(ValueError, '重複'):
            parse_series(payload, query)
        _, query, payload = fixture()
        payload['timeline_data'][0]['isPartial'] = True
        with self.assertRaises(SourceError):
            parse_series(payload, query)

    def test_explicit_missing_data_is_not_a_real_zero(self):
        _, query, payload = fixture()
        payload['timeline_data'][0]['hasData'] = [False]
        with self.assertRaises(SourceError):
            parse_series(payload, query)

    def test_rate_limit_stops_without_immediate_retry(self):
        headers = Message()
        headers['Retry-After'] = '120'
        client = GoogleTrendsClient()
        client.opener = Mock()
        client.opener.open.side_effect = HTTPError('https://trends.google.com/', 429, 'rate limit', headers, None)
        with self.assertRaisesRegex(RateLimited, '120'):
            client.get_json('/trends/api/explore', {})
        self.assertEqual(client.opener.open.call_count, 1)

    def test_download_snapshot_does_not_store_chart_token(self):
        _, query, payload = fixture()
        client = GoogleTrendsClient()
        client.get_json = Mock(side_effect=[
            {'widgets': [{'id': 'TIMESERIES', 'token': 'transient-chart-token', 'request': {'resolution': 'DAY'}}]},
            {'default': {'timelineData': payload['timeline_data']}},
        ])
        saved = client.fetch(query)
        self.assertNotIn('transient-chart-token', json.dumps(saved))
        self.assertEqual(len(parse_series(saved, query).scores), 7)
        self.assertEqual(client.get_json.call_count, 2)

    def test_cached_event_does_not_request_again(self):
        plan, query, payload = fixture()
        with tempfile.TemporaryDirectory() as folder:
            path = cache_path(Path(folder), plan, query)
            path.write_text(json.dumps(payload), encoding='utf-8')
            with patch('sys.argv', ['trends_features.py', 'fetch', '--cache-dir', folder]), \
                 patch('trends_features.read_events', return_value=[plan.event]), \
                 patch('trends_features.GoogleTrendsClient.fetch') as fetch, redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)
                fetch.assert_not_called()

    def test_untrusted_text_is_not_sql(self):
        self.assertNotIn('DROP TABLE', sql_text("x'); DROP TABLE original_data; --"))


if __name__ == '__main__':
    unittest.main()
