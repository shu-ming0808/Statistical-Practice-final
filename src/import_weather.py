"""Import event weather without replacing existing observations or changing OD tables.

Run: python -X utf8 src/import_weather.py [--check-only] [--csv PATH]
Times remain Asia/Taipei source labels; 23:59 is NOT converted to an hourly bin.
The schema is kept here so teammates can recreate it while *.sql stays ignored.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
from pathlib import Path
import re

from load_data import mysql
from project_paths import ROOT

DB = "taipei_metro_analysis"
DEFAULT_CSV = ROOT / "data/original_data/大稻埕周圍雲量資料.csv"
HEADER = """event_date station_id station_name county station_role
distance_to_dadaocheng_km datetime_tst air_temperature_c temperature_quality_flag
hourly_precipitation_mm precipitation_raw_value precipitation_status
precipitation_quality_flag mean_wind_speed_m_s wind_speed_quality_flag
total_cloud_amount_tenths cloud_measurement_type cloud_status source""".split()

SCHEMA_SQL = f"""-- Generated from src/import_weather.py; schema only, no source data.
CREATE TABLE IF NOT EXISTS {DB}.weather_station (
  station_id VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
  station_name VARCHAR(64) NOT NULL,
  county VARCHAR(64) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
  COMMENT='氣象測站；代碼不可連到捷運站名表';

CREATE TABLE IF NOT EXISTS {DB}.weather_observation (
  station_id VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  observed_at DATETIME NOT NULL COMMENT 'Asia/Taipei 原檔時間標籤；未確認各要素的代表區間',
  datetime_tst_raw VARCHAR(32) NOT NULL,
  time_alignment_status ENUM('source_label','end_of_day_unverified') NOT NULL,
  air_temperature_c DECIMAL(7,3) NULL,
  temperature_quality_flag VARCHAR(64) NULL,
  hourly_precipitation_mm DECIMAL(9,3) NULL,
  precipitation_raw_value VARCHAR(64) NULL,
  precipitation_status VARCHAR(64) NOT NULL,
  precipitation_quality_flag VARCHAR(64) NULL,
  mean_wind_speed_m_s DECIMAL(7,3) NULL,
  wind_speed_quality_flag VARCHAR(64) NULL,
  total_cloud_amount_tenths DECIMAL(5,3) NULL,
  cloud_measurement_type VARCHAR(64) NOT NULL,
  cloud_status VARCHAR(64) NOT NULL,
  cloud_method_review_required BOOLEAN NOT NULL COMMENT '2026年前衛星標記待查來源；不表示數值錯誤',
  source VARCHAR(255) NOT NULL,
  source_file_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  source_row_number INT UNSIGNED NOT NULL COMMENT 'CSV資料列序號；不含表頭，從1開始',
  imported_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(station_id,observed_at),
  KEY ix_weather_observed_at(observed_at),
  CONSTRAINT fk_weather_observation_station FOREIGN KEY(station_id)
    REFERENCES {DB}.weather_station(station_id),
  CONSTRAINT chk_weather_rain CHECK(hourly_precipitation_mm >= 0),
  CONSTRAINT chk_weather_wind CHECK(mean_wind_speed_m_s >= 0),
  CONSTRAINT chk_weather_cloud CHECK(total_cloud_amount_tenths BETWEEN 0 AND 10),
  CONSTRAINT chk_weather_cloud_review CHECK(cloud_method_review_required IN (0,1)),
  CONSTRAINT chk_weather_source_row CHECK(source_row_number > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
  COMMENT='每測站每時間一筆；缺值保留NULL，不推定為0';

CREATE TABLE IF NOT EXISTS {DB}.event_weather_station (
  event_date DATE NOT NULL,
  station_id VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  station_role ENUM('primary','robustness') NOT NULL,
  station_role_raw VARCHAR(255) NOT NULL,
  distance_to_dadaocheng_km DECIMAL(8,3) NOT NULL,
  primary_event_date DATE GENERATED ALWAYS AS
    (CASE WHEN station_role='primary' THEN event_date ELSE NULL END) STORED,
  PRIMARY KEY(event_date,station_id),
  UNIQUE KEY uq_weather_primary_event(primary_event_date),
  CONSTRAINT fk_weather_event FOREIGN KEY(event_date)
    REFERENCES {DB}.dadaocheng_fireworks_events(event_date),
  CONSTRAINT fk_weather_event_station FOREIGN KEY(station_id)
    REFERENCES {DB}.weather_station(station_id),
  CONSTRAINT chk_weather_distance CHECK(distance_to_dadaocheng_km >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
  COMMENT='活動採用測站；每活動最多一個primary，觀測表可獨立存一般日';
"""

KEYS = {"weather_station": ["station_id"],
        "weather_observation": ["station_id", "observed_at"],
        "event_weather_station": ["event_date", "station_id"]}
PROVENANCE = {"source_file_sha256", "source_row_number"}


def number(value: str, field: str, minimum=None, maximum=None):
    if not value:
        return None
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid number in {field}") from exc
    if not result.is_finite() or result != result.quantize(Decimal("0.001")):
        raise ValueError(f"Invalid precision or non-finite number in {field}")
    if (minimum is not None and result < minimum) or (maximum is not None and result > maximum):
        raise ValueError(f"Out-of-range value in {field}")
    return result


def parse_csv(path: Path):
    content = path.read_bytes()
    sha = hashlib.sha256(content).hexdigest()
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline=""))
    if reader.fieldnames != HEADER:
        raise ValueError("CSV columns/order do not match the weather source format")
    stations, observations, mappings = {}, {}, {}
    for line, raw in enumerate(reader, 1):
        if None in raw or any(v is None for v in raw.values()):
            raise ValueError(f"Malformed CSV row {line}")
        r = {k: v.strip() for k, v in raw.items()}
        station = r["station_id"]
        if not re.fullmatch(r"[A-Z0-9]{1,10}", station):
            raise ValueError(f"Invalid station_id on row {line}")
        day = datetime.strptime(r["event_date"], "%Y/%m/%d").date()
        observed = datetime.strptime(r["datetime_tst"], "%Y/%m/%d %H:%M")
        if observed.date() != day:
            raise ValueError(f"Event/time date mismatch on row {line}; review cross-midnight mapping first")
        for field in ["station_name", "county", "station_role", "precipitation_status",
                      "cloud_measurement_type", "cloud_status", "source"]:
            if not r[field]:
                raise ValueError(f"Missing {field} on row {line}")
        station_row = {k: r[k] for k in ["station_id", "station_name", "county"]}
        if station in stations and stations[station] != station_row:
            raise ValueError(f"Conflicting station metadata: {station}")
        stations[station] = station_row
        role = r["station_role"]
        if role != "primary" and not (role == "robustness" or "robustness station" in role):
            raise ValueError(f"Unrecognized station_role on row {line}")
        distance = number(r["distance_to_dadaocheng_km"], "distance", 0, 99999)
        if distance is None:
            raise ValueError(f"Missing distance on row {line}")
        mapping = dict(event_date=day, station_id=station,
                       station_role="primary" if role == "primary" else "robustness",
                       station_role_raw=role, distance_to_dadaocheng_km=distance)
        key = (day, station)
        if key in mappings and mappings[key] != mapping:
            raise ValueError(f"Conflicting event/station mapping: {key}")
        mappings[key] = mapping
        key = (station, observed)
        if key in observations:
            raise ValueError(f"Duplicate observation PK on row {line}: {key}")
        obs = dict(station_id=station, observed_at=observed,
                   datetime_tst_raw=r["datetime_tst"],
                   time_alignment_status="end_of_day_unverified" if observed.hour == 23 and observed.minute == 59 else "source_label")
        for field, low, high in [("air_temperature_c", -9999, 9999),
                                 ("hourly_precipitation_mm", 0, 999999),
                                 ("mean_wind_speed_m_s", 0, 9999),
                                 ("total_cloud_amount_tenths", 0, 10)]:
            obs[field] = number(r[field], field, low, high)
        for field in ["temperature_quality_flag", "precipitation_raw_value", "precipitation_status",
                      "precipitation_quality_flag", "wind_speed_quality_flag",
                      "cloud_measurement_type", "cloud_status", "source"]:
            obs[field] = r[field] or None
        # Raw special codes are preserved; never turn trace/missing/accumulated rain into zero.
        rain_status = obs["precipitation_status"]
        if rain_status not in {"observed", "trace_T", "accumulated_later_ampersand", "missing"}:
            raise ValueError(f"Unrecognized precipitation_status on row {line}")
        if (rain_status == "observed") != (obs["hourly_precipitation_mm"] is not None):
            raise ValueError(f"Rain status/value mismatch on row {line}")
        cloud_status = obs["cloud_status"]
        if cloud_status not in {"observed", "missing", "not_applicable"}:
            raise ValueError(f"Unrecognized cloud_status on row {line}")
        if (cloud_status == "observed") != (obs["total_cloud_amount_tenths"] is not None):
            raise ValueError(f"Cloud status/value mismatch on row {line}")
        obs["cloud_method_review_required"] = observed.year < 2026 and r["cloud_measurement_type"] == "satellite_retrieved"
        obs.update(source_file_sha256=sha, source_row_number=line)
        observations[key] = obs
    if not observations:
        raise ValueError("Empty weather CSV")
    for day in {k[0] for k in mappings}:
        if sum(r["station_role"] == "primary" for r in mappings.values() if r["event_date"] == day) != 1:
            raise ValueError(f"Expected exactly one primary station for {day}")
    return {"weather_station": list(stations.values()),
            "weather_observation": list(observations.values()),
            "event_weather_station": list(mappings.values())}, sha


def sql_value(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, (int, Decimal)):
        return str(value)
    if isinstance(value, datetime):
        value = value.isoformat(sep=" ")
    # Hex literals avoid quote/backslash handling and SQL-mode-dependent escaping.
    return "CONVERT(0x" + str(value).encode("utf-8").hex() + " USING utf8mb4)"


def import_sql(tables):
    """One data transaction; guard failures close the CLI connection and roll it back."""
    statements = [f"USE {DB};",
                  "SET SESSION sql_mode='STRICT_ALL_TABLES,NO_ZERO_DATE,NO_ZERO_IN_DATE,ERROR_FOR_DIVISION_BY_ZERO';",
                  "CREATE TEMPORARY TABLE weather_import_guard (check_name VARCHAR(100), ok TINYINT NOT NULL CHECK(ok=1));"]
    for table in tables:
        statements.append(f"CREATE TEMPORARY TABLE incoming_{table} LIKE {table};")
    statements.append("START TRANSACTION;")
    for table, rows in tables.items():
        columns = list(rows[0])
        for start in range(0, len(rows), 200):
            values = ",".join("(" + ",".join(sql_value(r[c]) for c in columns) + ")" for r in rows[start:start+200])
            statements.append(f"INSERT INTO incoming_{table} ({','.join(columns)}) VALUES {values};")
        on = " AND ".join(f"d.{k}=s.{k}" for k in KEYS[table])
        same = " AND ".join(f"d.{c} <=> s.{c}" for c in columns if c not in PROVENANCE)
        statements.append(f"INSERT INTO weather_import_guard SELECT '{table}_no_conflicts', COUNT(*)=0 "
                          f"FROM {table} d JOIN incoming_{table} s ON {on} WHERE NOT ({same});")
    statements.append("INSERT INTO weather_import_guard SELECT 'all_event_dates_exist', COUNT(*)=0 "
                      "FROM incoming_event_weather_station s LEFT JOIN dadaocheng_fireworks_events e "
                      "ON e.event_date=s.event_date WHERE e.event_date IS NULL;")
    for table, rows in tables.items():
        columns = list(rows[0])
        on = " AND ".join(f"d.{k}=s.{k}" for k in KEYS[table])
        same = " AND ".join(f"d.{c} <=> s.{c}" for c in columns if c not in PROVENANCE)
        statements.append(f"INSERT INTO {table} ({','.join(columns)}) SELECT "
                          + ",".join(f"s.{c}" for c in columns)
                          + f" FROM incoming_{table} s LEFT JOIN {table} d ON {on} WHERE d.{KEYS[table][0]} IS NULL;")
        statements.append(f"SET @added_{table}=ROW_COUNT();")
        statements.append(f"INSERT INTO weather_import_guard SELECT '{table}_reconciled', COUNT(*)={len(rows)} "
                          f"FROM {table} d JOIN incoming_{table} s ON {on} WHERE {same};")
    statements.append("COMMIT;")
    statements.append("SELECT JSON_OBJECT(" + ",".join(f"'{t}',@added_{t}" for t in tables) + ");")
    return "\n".join(statements)


def audit_database(login_path):
    counts = json.loads(mysql(f"""SELECT JSON_OBJECT(
      'weather_station',(SELECT COUNT(*) FROM {DB}.weather_station),
      'weather_observation',(SELECT COUNT(*) FROM {DB}.weather_observation),
      'event_weather_station',(SELECT COUNT(*) FROM {DB}.event_weather_station),
      'primary_mappings',(SELECT COUNT(*) FROM {DB}.event_weather_station WHERE station_role='primary'),
      'end_of_day_unverified',(SELECT COUNT(*) FROM {DB}.weather_observation WHERE time_alignment_status='end_of_day_unverified'),
      'cloud_method_review_required',(SELECT COUNT(*) FROM {DB}.weather_observation WHERE cloud_method_review_required=1),
      'cloud_values',(SELECT COUNT(total_cloud_amount_tenths) FROM {DB}.weather_observation),
      'rain_values',(SELECT COUNT(hourly_precipitation_mm) FROM {DB}.weather_observation));""", login_path))
    missing = mysql(f"""SELECT e.event_date FROM {DB}.dadaocheng_fireworks_events e
      LEFT JOIN {DB}.event_weather_station ew ON ew.event_date=e.event_date AND ew.station_role='primary'
      WHERE ew.event_date IS NULL ORDER BY e.event_date;""", login_path).splitlines()
    counts["events_without_primary_weather_mapping"] = missing
    # This is a cardinality check, not a claim that weather/source OD time windows align.
    join = mysql(f"""SELECT
      (SELECT COUNT(*) FROM {DB}.analysis_event_station_hourly), COUNT(*)
      FROM {DB}.analysis_event_station_hourly m
      LEFT JOIN {DB}.event_weather_station ew ON ew.event_date=m.event_date AND ew.station_role='primary'
      LEFT JOIN {DB}.weather_observation w ON w.station_id=ew.station_id AND w.observed_at=m.hour_start;""", login_path)
    before, after = map(int, join.split("\t"))
    if before != after:
        raise RuntimeError("Weather join multiplied the OD analysis rows")
    counts["od_rows_before_join"] = before
    counts["od_rows_after_join"] = after
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--login-path", default="codex-local")
    parser.add_argument("--check-only", action="store_true", help="Validate CSV and existing event dates; no writes")
    args = parser.parse_args()
    tables, sha = parse_csv(args.csv)
    db_dates = set(mysql(f"SELECT event_date FROM {DB}.dadaocheng_fireworks_events;", args.login_path).splitlines())
    source_dates = {str(r["event_date"]) for r in tables["event_weather_station"]}
    unknown = sorted(source_dates - db_dates)
    if unknown:
        raise ValueError(f"CSV refers to unknown activity dates: {unknown}")
    report = dict(source_file=str(args.csv.resolve()), source_file_sha256=sha,
                  source_rows={t: len(rows) for t, rows in tables.items()},
                  activity_dates_without_source_weather=sorted(db_dates-source_dates))
    if not args.check_only:
        # MySQL DDL commits implicitly; table creation is separate from atomic data import.
        (ROOT / "mysql/007_weather.sql").write_text(SCHEMA_SQL, encoding="utf-8")
        mysql(SCHEMA_SQL, args.login_path)
        report["inserted_rows"] = json.loads(mysql(import_sql(tables), args.login_path))
        report["database_audit"] = audit_database(args.login_path)
        destination = ROOT / "results/weather/import_summary.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
