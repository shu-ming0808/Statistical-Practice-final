"""Atomic, idempotent publication of modest derived tables; no bulk-file privilege."""
from datetime import date, datetime
from decimal import Decimal
import hashlib
import math

from load_data import mysql
from project_paths import ROOT, PROCESSED
from data_preprocessing import digest

HOURLY = ["event_date","hour_start","station","origin_count",
          "destination_count_same_source_bin","origin_complete","destination_complete",
          "out_degree_excluding_self","in_degree_excluding_self",
          "baseline_origin_mean_28d","baseline_n_days","origin_lag_7d"]
WINDOW = ["event_date","station","window_start_hour","window_end_hour_exclusive",
          "expected_hours","observed_origin_hours","observed_destination_hours",
          "origin_count_window","destination_count_window","peak_origin_hour_count",
          "baseline_origin_window","baseline_min_days","origin_window_complete"]
LABEL = ["station","first_seen_date","last_seen_date","seen_as_origin","seen_as_destination"]


def sql_value(value):
    if value is None:
        return "NULL"
    if isinstance(value,bool):
        return str(int(value))
    if isinstance(value,(int,float,Decimal)):
        if not math.isfinite(value):
            raise ValueError("Non-finite numeric value")
        return str(value)
    if isinstance(value,(date,datetime)):
        value=value.isoformat(sep=" ") if isinstance(value,datetime) else value.isoformat()
    return "CONVERT(0x"+str(value).encode("utf-8").hex()+" USING utf8mb4)"


def insert_statements(table, columns, rows, keys):
    updates=",".join(f"`{c}`=VALUES(`{c}`)" for c in columns if c not in keys)
    for start in range(0,len(rows),500):
        values=",".join("("+",".join(sql_value(v) for v in row)+")" for row in rows[start:start+500])
        yield f"INSERT INTO taipei_metro_analysis.{table} ({','.join(columns)}) VALUES {values} ON DUPLICATE KEY UPDATE {updates};"


def publish(con,login_path):
    # DDL is deliberately outside the data transaction: MySQL DDL implicitly commits.
    mysql((ROOT/"mysql"/"005_analysis_tables.sql").read_text(encoding="utf-8"),login_path)
    run_hash=hashlib.sha256("".join(digest(PROCESSED/f"{t}.parquet") for t in
        ("station_labels","regression_station_hourly","regression_event_station")).encode("ascii")).hexdigest()
    specs=[("analysis_station_label","station_labels",LABEL,{"station"}),
           ("analysis_event_station_hourly","regression_station_hourly",HOURLY,{"event_date","hour_start","station"}),
           ("analysis_event_station_window","regression_event_station",WINDOW,
            {"event_date","station","window_start_hour","window_end_hour_exclusive"})]
    statements=["SET SESSION sql_mode='STRICT_TRANS_TABLES,NO_ZERO_DATE,NO_ZERO_IN_DATE,ERROR_FOR_DIVISION_BY_ZERO';",
                "START TRANSACTION;"]
    counts={}
    for target,source,columns,keys in specs:
        rows=con.execute(f"SELECT {','.join(columns)} FROM {source}").fetchall()
        columns=list(columns)
        counts[target]=len(rows)
        if target!="analysis_station_label":
            columns.append("source_run_sha256")
            rows=[(*row,run_hash) for row in rows]
        statements.extend(insert_statements(target,columns,rows,keys))
        # Replace the selected derived scope completely when a new run removes hours/labels.
        # Other events and alternative summary windows remain available.
        if target=="analysis_event_station_hourly" and rows:
            dates=sorted({row[0] for row in rows})
            scope="event_date IN ("+",".join(sql_value(d) for d in dates)+")"
            statements.append(f"DELETE FROM taipei_metro_analysis.{target} WHERE {scope} AND source_run_sha256<>{sql_value(run_hash)};")
        elif target=="analysis_event_station_window" and rows:
            scopes=sorted({(row[0],row[2],row[3]) for row in rows})
            scope=" OR ".join(f"(event_date={sql_value(d)} AND window_start_hour={start} AND window_end_hour_exclusive={end})" for d,start,end in scopes)
            statements.append(f"DELETE FROM taipei_metro_analysis.{target} WHERE ({scope}) AND source_run_sha256<>{sql_value(run_hash)};")
    statements.append("COMMIT;")
    mysql("\n".join(statements),login_path)
    for target in ("analysis_event_station_hourly","analysis_event_station_window"):
        actual=int(mysql(f"SELECT count(*) FROM taipei_metro_analysis.{target} WHERE source_run_sha256={sql_value(run_hash)};",login_path))
        if actual!=counts[target]:
            raise RuntimeError(f"MySQL publication count mismatch: {target}")
    mysql((ROOT/"mysql"/"006_analysis_views.sql").read_text(encoding="utf-8"),login_path)
    return {"source_run_sha256":run_hash,"published_rows":counts}
