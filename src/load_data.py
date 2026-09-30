"""Read only event/feature tables through MySQL's encrypted local login path."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess

from project_paths import INTERIM

MYSQL = Path(r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe")


def mysql(sql: str, login_path: str = "codex-local") -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", login_path):
        raise ValueError("Invalid login path")
    result = subprocess.run(
        [str(MYSQL), f"--login-path={login_path}", "--default-character-set=utf8mb4",
         "--connect-timeout=10", "--local-infile=0", "--batch", "--raw",
         "--silent", "--skip-column-names"],
        input=sql.encode("utf-8"), capture_output=True, timeout=120,
        env={k: v for k, v in os.environ.items() if k.upper() != "MYSQL_PWD"},
    )
    if result.returncode:
        raise RuntimeError(f"MySQL failed (code {result.returncode}); check login path and table permissions")
    return result.stdout.decode("utf-8", errors="strict").strip()


def event_snapshot(login_path: str) -> list[dict]:
    sql = """
    SELECT JSON_OBJECT(
      'event_date', CAST(e.event_date AS CHAR), 'event_name', e.event_name,
      'event_status', e.event_status,
      'announced_show_start_time', CAST(e.announced_show_start_time AS CHAR),
      'fireworks_duration_seconds', e.fireworks_duration_seconds,
      'combined_show_duration_seconds', e.combined_show_duration_seconds,
      'has_drone_show', e.has_drone_show, 'related_activities', e.related_activities,
      'not_held_reason', e.not_held_reason, 'notes', e.notes,
      'transferred_to_date', CAST(e.transferred_to_date AS CHAR),
      'official_source_url', e.official_source_url, 'verified_on', CAST(e.verified_on AS CHAR),
      'interest_1d', f.interest_1d, 'interest_3d_avg', f.interest_3d_avg,
      'interest_7d_avg', f.interest_7d_avg, 'trends_import_id', f.import_id,
      'trends_resolution', i.source_resolution, 'trends_time_basis', i.time_basis,
      'trends_period_start', CAST(i.period_start AS CHAR),
      'trends_period_end', CAST(i.period_end AS CHAR))
    FROM taipei_metro_analysis.dadaocheng_fireworks_events e
    LEFT JOIN taipei_metro_analysis.event_trends_features f
      ON e.event_date=f.event_date AND f.query_text='大稻埕煙火' AND f.geo_code='TW'
    LEFT JOIN taipei_metro_analysis.google_trends_imports i ON f.import_id=i.import_id
    ORDER BY e.event_date;
    """
    records = [json.loads(line) for line in mysql(sql, login_path).splitlines() if line]
    if not records or len({r["event_date"] for r in records}) != len(records):
        raise ValueError("Empty or duplicate event dates")
    (INTERIM / "events_snapshot.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return records


def install_events(con, records):
    con.execute("""CREATE OR REPLACE TABLE events (
      event_date DATE PRIMARY KEY, event_name VARCHAR, event_status VARCHAR,
      announced_show_start_time TIME, fireworks_duration_seconds INTEGER,
      combined_show_duration_seconds INTEGER, has_drone_show BOOLEAN,
      interest_1d DOUBLE, interest_3d_avg DOUBLE, interest_7d_avg DOUBLE,
      trends_import_id BIGINT, trends_resolution VARCHAR, trends_time_basis VARCHAR,
      trends_period_start DATE, trends_period_end DATE)""")
    columns = [item[0] for item in con.execute("DESCRIBE events").fetchall()]
    con.executemany("INSERT INTO events VALUES (" + ",".join("?" for _ in columns) + ")",
                    [[r.get(k) for k in columns] for r in records])
