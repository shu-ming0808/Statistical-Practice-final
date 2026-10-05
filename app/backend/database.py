"""Fixed, bounded read queries using the existing encrypted MySQL login path.

The website does not call ETL/publication functions or read a password into Python.
MySQL itself opens the local login path, just as src/load_data.py does.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from datetime import date
import json
import os
from pathlib import Path
import re
import subprocess
from threading import Lock
from time import monotonic
from typing import Callable


MYSQL_DEFAULT = r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe"
DATABASE_MESSAGE = "目前無法讀取 MySQL。請確認資料庫已啟動，以及本機 codex-local 登入設定與讀取權限。"


class DatabaseUnavailable(RuntimeError):
    """A safe error that deliberately omits subprocess output and credentials."""


def _read_json(sql: str) -> list[dict]:
    login_path = os.environ.get("APP_MYSQL_LOGIN_PATH", "codex-local")
    mysql_bin = Path(os.environ.get("APP_MYSQL_BIN", MYSQL_DEFAULT))
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", login_path) or not mysql_bin.is_file():
        raise DatabaseUnavailable(DATABASE_MESSAGE)
    # Bound connection, each SELECT, and the whole process independently.
    query = (
        "SET SESSION MAX_EXECUTION_TIME=8000;\n"
        "START TRANSACTION READ ONLY;\n" + sql + "\nROLLBACK;\n"
    )
    try:
        result = subprocess.run(
            [str(mysql_bin), f"--login-path={login_path}",
             "--default-character-set=utf8mb4", "--connect-timeout=5",
             "--local-infile=0", "--batch", "--raw", "--silent", "--skip-column-names"],
            input=query.encode("utf-8"), capture_output=True, timeout=15,
            env={k: v for k, v in os.environ.items() if k.upper() != "MYSQL_PWD"},
        )
        if result.returncode:
            raise DatabaseUnavailable(DATABASE_MESSAGE)
        return [json.loads(line) for line in result.stdout.decode("utf-8").splitlines() if line]
    except (OSError, subprocess.TimeoutExpired, UnicodeError, ValueError) as exc:
        raise DatabaseUnavailable(DATABASE_MESSAGE) from exc


def _text(value: str) -> str:
    # Hex literals avoid SQL quoting/encoding ambiguity, including apostrophes.
    return "CONVERT(0x" + value.encode("utf-8").hex() + " USING utf8mb4)"


class Repository:
    def __init__(self, reader: Callable[[str], list[dict]] | None = None):
        self.reader = reader or _read_json
        self._cache: OrderedDict[tuple, tuple[float, list[dict]]] = OrderedDict()
        self._lock = Lock()

    def _cached(self, key: tuple, sql: str, ttl: int = 30) -> list[dict]:
        now = monotonic()
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] > now:
                self._cache.move_to_end(key)
                return deepcopy(cached[1])
        rows = self.reader(sql)
        with self._lock:
            self._cache[key] = (monotonic() + ttl, deepcopy(rows))
            self._cache.move_to_end(key)
            while len(self._cache) > 128:
                self._cache.popitem(last=False)
        return rows

    def health(self) -> bool:
        # Check the three tables actually used by this app, not merely SELECT 1.
        rows = self._cached(("health",), """
            SELECT JSON_OBJECT('tables', COUNT(*))
            FROM information_schema.tables
            WHERE table_schema='taipei_metro_analysis' AND table_name IN
            ('dadaocheng_fireworks_events', 'analysis_station_label', 'analysis_event_station_hourly');
        """, ttl=5)
        if not rows or rows[0].get("tables") != 3:
            raise DatabaseUnavailable(DATABASE_MESSAGE)
        return True

    def events(self) -> list[dict]:
        return self._cached(("events",), """
            SELECT JSON_OBJECT(
              'event_date', CAST(event_date AS CHAR), 'event_name', event_name,
              'event_status', event_status,
              'announced_show_start_time', CAST(announced_show_start_time AS CHAR),
              'fireworks_duration_seconds', fireworks_duration_seconds,
              'combined_show_duration_seconds', combined_show_duration_seconds,
              'has_drone_show', has_drone_show, 'not_held_reason', not_held_reason,
              'transferred_to_date', CAST(transferred_to_date AS CHAR),
              'official_source_url', official_source_url)
            FROM taipei_metro_analysis.dadaocheng_fireworks_events
            ORDER BY event_date DESC LIMIT 500;
        """, ttl=60)

    def stations(self) -> list[dict]:
        return self._cached(("stations",), """
            SELECT JSON_OBJECT('station', station)
            FROM taipei_metro_analysis.analysis_station_label
            ORDER BY station LIMIT 500;
        """, ttl=60)

    def flows(self, event_date: date, stations: list[str], start_hour: int, end_hour: int) -> list[dict]:
        # Callers validate dates/stations against the database catalog first.
        date_value = _text(event_date.isoformat())
        station_values = ",".join(_text(station) for station in stations)
        rows = self._cached(("flows", event_date.isoformat(), tuple(sorted(stations)), start_hour, end_hour), f"""
            SELECT JSON_OBJECT(
              'event_date', CAST(event_date AS CHAR),
              'hour_start', DATE_FORMAT(hour_start, '%Y-%m-%dT%H:%i:%s'),
              'station', station, 'origin_count', origin_count,
              'destination_count_same_source_bin', destination_count_same_source_bin,
              'origin_complete', origin_complete, 'destination_complete', destination_complete,
              'baseline_origin_mean_28d', baseline_origin_mean_28d,
              'baseline_n_days', baseline_n_days)
            FROM taipei_metro_analysis.analysis_event_station_hourly
            WHERE event_date={date_value} AND station IN ({station_values})
              AND hour_start >= TIMESTAMPADD(HOUR, {int(start_hour)}, CAST({date_value} AS DATETIME))
              AND hour_start < TIMESTAMPADD(HOUR, {int(end_hour)}, CAST({date_value} AS DATETIME))
            ORDER BY hour_start, station LIMIT 481;
        """)
        # Twenty selected stations x 24 hours is the request's maximum shape.
        if len(rows) > 480:
            raise DatabaseUnavailable("資料筆數超出預期，請檢查活動分時資料是否重複。")
        for row in rows:
            row["origin_complete"] = bool(row["origin_complete"])
            row["destination_complete"] = bool(row["destination_complete"])
        return rows
