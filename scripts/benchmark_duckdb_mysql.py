"""Benchmark shared project OD data, without changing production tables.

Uses the existing encrypted MySQL login path through one persistent mysql CLI.
Creates and drops only a uniquely named, private benchmark database.
Run from any directory: python scripts/benchmark_duckdb_mysql.py
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import re
import statistics
import subprocess
import sys
import time
import uuid

import duckdb

ROOT = Path(__file__).resolve().parents[1]
MYSQL = Path(r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe")


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def file_sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class MysqlSession:
    """Persistent client: connection/process startup is outside measured queries."""

    def __init__(self, login_path, log_path):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", login_path):
            raise ValueError("Invalid login path")
        self.error_file = log_path.open("w+b")
        self.process = subprocess.Popen(
            [str(MYSQL), f"--login-path={login_path}",
             "--default-character-set=utf8mb4", "--batch", "--raw",
             "--skip-column-names", "--unbuffered", "--comments", "--local-infile=0",
             "--connect-timeout=10"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.error_file,
            env={k: v for k, v in os.environ.items() if k.upper() != "MYSQL_PWD"},
        )
        self.execute("SELECT 1")
        self.thread_id = int(self.execute(
            "SELECT THREAD_ID FROM performance_schema.threads "
            "WHERE PROCESSLIST_ID=CONNECTION_ID()")[0][0])

    def execute(self, sql, *, digest=None):
        sentinel = ("__bench_end_" + uuid.uuid4().hex).encode("ascii")
        command = sql.rstrip().rstrip(";") + ";\nSELECT " + literal(sentinel.decode()) + ";\n"
        self.process.stdin.write(command.encode("utf-8"))
        self.process.stdin.flush()
        rows = []
        count = 0
        while True:
            line = self.process.stdout.readline()
            if not line:
                self.error_file.seek(0)
                error = self.error_file.read().decode("utf-8", errors="replace")
                raise RuntimeError("MySQL benchmark client stopped: " + error)
            line = line.rstrip(b"\r\n")
            if line == sentinel:
                return count if digest is not None else rows
            count += 1
            if digest is not None:
                digest.update(line + b"\n")
            else:
                rows.append(line.decode("utf-8").split("\t"))

    def statement_metrics(self, tag):
        rows = self.execute(
            "SELECT TIMER_WAIT/1000000000,ROWS_EXAMINED,ROWS_SENT,"
            "NO_INDEX_USED,CREATED_TMP_DISK_TABLES "
            "FROM performance_schema.events_statements_history "
            f"WHERE THREAD_ID={self.thread_id} "
            f"AND SQL_TEXT LIKE {literal('SELECT /* ' + tag + ' */%')} "
            "ORDER BY EVENT_ID DESC LIMIT 1")
        if not rows:
            return {}
        return dict(zip(
            ["server_elapsed_ms", "rows_examined", "rows_sent",
             "no_index_used", "created_tmp_disk_tables"],
            [float(v) for v in rows[0]],
        ))

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            self.process.wait(timeout=30)
        self.error_file.close()


def canonical(rows):
    # Only dates, station strings and integers are returned by these queries.
    return [
        [v.isoformat() if hasattr(v, "isoformat") else str(v) for v in row]
        for row in rows
    ]


def rows_sha(rows):
    digest = hashlib.sha256()
    for row in rows:
        digest.update(("\t".join(row) + "\n").encode("utf-8"))
    return digest.hexdigest()


def show_create(mysql, table):
    rows = mysql.execute(f"SHOW CREATE TABLE {table}")
    # --raw preserves embedded newlines in SHOW CREATE TABLE's second column.
    return "\n".join(["\t".join(rows[0][1:])] + ["\t".join(row) for row in rows[1:]])


def hardware():
    command = (
        "$cpu=Get-CimInstance Win32_Processor; "
        "$mem=Get-CimInstance Win32_ComputerSystem; "
        "[pscustomobject]@{cpu=$cpu.Name;physical_cores=$cpu.NumberOfCores;"
        "logical_processors=$cpu.NumberOfLogicalProcessors;"
        "ram_bytes=$mem.TotalPhysicalMemory}|ConvertTo-Json -Compress"
    )
    run = subprocess.run(["powershell", "-NoProfile", "-Command", command],
                         capture_output=True, check=True)
    return json.loads(run.stdout.decode("utf-8-sig"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", default="202607")
    parser.add_argument("--login-path", default="codex-local")
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--index-profile", choices=["station", "workload"], default="workload",
                        help="Workload also adds indexes supporting both full-month groupings")
    args = parser.parse_args()
    if not re.fullmatch(r"20\d{4}", args.month) or args.repeats < 3 or args.warmups < 1:
        parser.error("Require YYYYMM, repeats >= 3 and warmups >= 1")
    started = datetime.now(timezone.utc)
    run_id = started.strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    private = ROOT / ".local" / "duckdb_benchmark" / run_id
    private.mkdir(parents=True, exist_ok=False)
    output = ROOT / "result" / "duckdb_benchmark"
    if args.index_profile == "station":
        output = output / "station_index"
    output.mkdir(parents=True, exist_ok=True)
    parquet = ROOT / "data" / "interim" / "clean_od" / f"{args.month}.parquet"
    manifest = json.loads((ROOT / "results" / "preprocessing" / "monthly" /
                           f"{args.month}.json").read_text(encoding="utf-8"))
    if file_sha(parquet) != manifest["output_sha256"]:
        raise ValueError("Clean Parquet hash differs from its validation manifest")
    database = "bench_duckdb_" + run_id.lower()
    table = database + ".od"
    mysql = MysqlSession(args.login_path, private / "mysql_stderr.log")
    verifier = duckdb.connect()
    verifier.execute("SET threads=4")
    verifier.execute("SET memory_limit='2GB'")
    verifier.execute("SET temp_directory=" + literal((private / "verify_tmp").as_posix()))
    created = False
    connections = {}
    report = dict(
        run_id=run_id, started_at_utc=started.isoformat(), month=args.month,
        python=sys.version, duckdb_version=duckdb.__version__,
        hardware=hardware(), source_manifest=manifest,
        parquet_relative_path=str(parquet.relative_to(ROOT)),
        parquet_bytes=parquet.stat().st_size, script_sha256=file_sha(Path(__file__)),
        setup={}, verification={}, queries={}, measurements=[],
        warmups=args.warmups, repeats=args.repeats, seed=20261020,
        timing="Persistent connections, execute plus complete result fetch to Python; "
               "exclude startup, setup, plans, verification, and Python result hashing. "
               "MySQL uses mysql CLI pipes; DuckDB uses Python API. "
               "First measured query is NOT a cold-cache benchmark.",
        scratch_database=database,
        index_profile=args.index_profile,
    )
    try:
        report["mysql_version"] = mysql.execute("SELECT VERSION()")[0][0]
        report["mysql_variables"] = dict(mysql.execute(
            "SHOW VARIABLES WHERE Variable_name IN "
            "('innodb_buffer_pool_size','innodb_page_size','tmp_table_size',"
            "'max_heap_table_size','sort_buffer_size','optimizer_switch',"
            "'version_compile_machine')"))
        report["source_mysql_schema"] = show_create(
            mysql, "taipei_metro_analysis.original_data")
        print("Preparing isolated typed MySQL table; original_data is read-only.", flush=True)
        mysql.execute(f"CREATE DATABASE {database} CHARACTER SET utf8mb4 COLLATE utf8mb4_bin")
        created = True
        mysql.execute(f"""CREATE TABLE {table} (
          source_row BIGINT NOT NULL PRIMARY KEY,
          service_date DATE NOT NULL, source_hour SMALLINT NOT NULL,
          origin_station VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
          destination_station VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
          passengers BIGINT NOT NULL
        ) ENGINE=InnoDB""")
        t0 = time.perf_counter()
        mysql.execute(f"""INSERT INTO {table}
          SELECT source_row,CAST(date_raw AS DATE),CAST(hour_raw AS SIGNED),
          TRIM(entry_station_raw),TRIM(exit_station_raw),CAST(passenger_count_raw AS SIGNED)
          FROM taipei_metro_analysis.original_data
          WHERE source_month={literal(args.month)} ORDER BY source_row""")
        report["setup"]["mysql_typed_import_seconds"] = time.perf_counter() - t0
        write_json(private / "checkpoint.json", report)
        print("Typed import complete; building station/date/hour covering index.", flush=True)
        t0 = time.perf_counter()
        mysql.execute(
            f"CREATE INDEX ix_station_date_hour_count ON {table} "
            "(origin_station,service_date,source_hour,passengers)")
        report["setup"]["mysql_index_seconds"] = time.perf_counter() - t0
        if args.index_profile == "workload":
            print("Adding indexes specifically supporting hourly and OD grouping.", flush=True)
            t0 = time.perf_counter()
            mysql.execute(
                f"CREATE INDEX ix_station_hour_count ON {table} "
                "(origin_station,source_hour,passengers)")
            mysql.execute(
                f"CREATE INDEX ix_od_count ON {table} "
                "(origin_station,destination_station,passengers)")
            report["setup"]["mysql_workload_index_seconds"] = time.perf_counter() - t0
        mysql.execute(f"ANALYZE TABLE {table}")
        report["mysql_benchmark_schema"] = show_create(mysql, table)
        report["mysql_table_storage"] = mysql.execute(
            "SELECT DATA_LENGTH,INDEX_LENGTH,DATA_FREE FROM information_schema.tables "
            f"WHERE table_schema={literal(database)} AND table_name='od'")[0]
        print("Verifying all source rows with ordered SHA-256, outside query timings.", flush=True)
        fields = "source_row,service_date,source_hour,origin_station,destination_station,passengers"
        t0 = time.perf_counter()
        mysql_digest = hashlib.sha256()
        mysql_count = mysql.execute(
            f"SELECT {fields} FROM {table} ORDER BY source_row", digest=mysql_digest)
        report["verification"]["mysql_stream_seconds"] = time.perf_counter() - t0
        t0 = time.perf_counter()
        duck_digest = hashlib.sha256()
        cursor = verifier.execute(
            f"SELECT {fields} FROM read_parquet(?) ORDER BY source_row", [str(parquet)])
        duck_count = 0
        while rows := cursor.fetchmany(20000):
            for row in canonical(rows):
                duck_digest.update(("\t".join(row) + "\n").encode("utf-8"))
            duck_count += len(rows)
        report["verification"].update(
            duckdb_stream_seconds=time.perf_counter() - t0,
            mysql_rows=mysql_count, duckdb_rows=duck_count,
            mysql_sha256=mysql_digest.hexdigest(), duckdb_sha256=duck_digest.hexdigest(),
            canonical_fields=fields, ordering="source_row ascending",
        )
        if not (mysql_count == duck_count == manifest["clean_rows"]
                and mysql_digest.digest() == duck_digest.digest()):
            raise AssertionError("Input content differs: benchmark must not proceed")
        report["verification"]["all_rows_equal"] = True
        point_date = args.month[:4] + "-" + args.month[4:] + "-29"
        queries = {
            "q1_beimen_day": {
                "label": "北門單日 18–23 時逐時人次",
                "sql": f"""SELECT source_hour,SUM(passengers) AS total
                  FROM {{table}} WHERE origin_station='北門'
                  AND service_date='{point_date}' AND source_hour BETWEEN 18 AND 23
                  GROUP BY source_hour ORDER BY source_hour""",
            },
            "q2_four_stations": {
                "label": "四站全月晚間逐日逐時人次",
                "sql": """SELECT service_date,origin_station,source_hour,SUM(passengers) AS total
                  FROM {table} WHERE origin_station IN ('北門','大橋頭站','雙連','民權西路')
                  AND source_hour BETWEEN 18 AND 23
                  GROUP BY service_date,origin_station,source_hour
                  ORDER BY service_date,origin_station,source_hour""",
            },
            "q3_network_hourly": {
                "label": "全站全月依車站與時段彙總",
                "sql": """SELECT origin_station,source_hour,SUM(passengers) AS total
                  FROM {table} GROUP BY origin_station,source_hour
                  ORDER BY origin_station,source_hour""",
            },
            "q4_top_od": {
                "label": "全月非零 OD 彙總前 20 名",
                "sql": """SELECT origin_station,destination_station,SUM(passengers) AS total
                  FROM {table} WHERE passengers>0
                  GROUP BY origin_station,destination_station
                  ORDER BY total DESC,origin_station,destination_station LIMIT 20""",
            },
        }
        for name, config in [("duckdb_1t_128mib", (1, "128MiB")),
                             ("duckdb_4t_2gb", (4, "2GB"))]:
            con = duckdb.connect()
            con.execute(f"SET threads={config[0]}")
            con.execute("SET memory_limit=" + literal(config[1]))
            con.execute("SET temp_directory=" + literal((private / name).as_posix()))
            con.execute("CREATE VIEW od AS SELECT * FROM read_parquet(" +
                        literal(parquet.as_posix()) + ")")
            connections[name] = con
        report["duckdb_configurations"] = {
            name: dict(con.execute(
                "SELECT name,value FROM duckdb_settings() WHERE name IN "
                "('threads','memory_limit','preserve_insertion_order')").fetchall())
            for name, con in connections.items()
        }
        plans = {}
        for key, item in queries.items():
            mysql_sql = item["sql"].format(table=table)
            duck_sql = item["sql"].format(table="od")
            report["queries"][key] = dict(
                label=item["label"], mysql_sql=mysql_sql, duckdb_sql=duck_sql)
            plans[key] = {
                "mysql": mysql.execute("EXPLAIN FORMAT=JSON " + mysql_sql),
                "duckdb": connections["duckdb_4t_2gb"].execute("EXPLAIN " + duck_sql).fetchall(),
            }
        write_json(output / "query_plans.json", plans)
        write_json(private / "checkpoint.json", report)
        reference = {}
        engines = ["mysql_indexed", *connections]
        rng = random.Random(report["seed"])
        # All engines warmed per query; then randomize the engine order for each repetition.
        for key, item in queries.items():
            print("Benchmarking " + key + ": " + item["label"], flush=True)
            for phase, repeats in [("warmup", args.warmups), ("measured", args.repeats)]:
                for iteration in range(repeats):
                    order = engines.copy()
                    rng.shuffle(order)
                    for position, engine in enumerate(order):
                        tag = f"bench{key}{phase}{iteration}"
                        sql = report["queries"][key][
                            "mysql_sql" if engine == "mysql_indexed" else "duckdb_sql"]
                        sql = sql.replace("SELECT ", f"SELECT /* {tag} */ ", 1)
                        t0 = time.perf_counter()
                        if engine == "mysql_indexed":
                            rows = mysql.execute(sql)
                        else:
                            raw_rows = connections[engine].execute(sql).fetchall()
                        elapsed = (time.perf_counter() - t0) * 1000
                        if engine != "mysql_indexed":
                            rows = canonical(raw_rows)
                        digest = rows_sha(rows)
                        if key not in reference:
                            reference[key] = (len(rows), digest)
                        if reference[key] != (len(rows), digest):
                            raise AssertionError(f"Output mismatch: {key}/{engine}")
                        metrics = mysql.statement_metrics(tag) if engine == "mysql_indexed" else {}
                        record = dict(query=key, engine=engine, phase=phase,
                                      iteration=iteration + 1, order=position + 1,
                                      elapsed_ms=elapsed, rows=len(rows), sha256=digest,
                                      **metrics)
                        report["measurements"].append(record)
                        print(f"  {phase} {iteration+1}: {engine} {elapsed:.3f} ms", flush=True)
                        write_json(private / "checkpoint.json", report)
        summaries = []
        grouped = defaultdict(list)
        for record in report["measurements"]:
            if record["phase"] == "measured":
                grouped[(record["query"], record["engine"])].append(record["elapsed_ms"])
        for key in queries:
            baseline = statistics.median(grouped[(key, "mysql_indexed")])
            for engine in engines:
                values = grouped[(key, engine)]
                median = statistics.median(values)
                summaries.append(dict(
                    query=key, label=queries[key]["label"], engine=engine, n=len(values),
                    median_ms=median, minimum_ms=min(values), maximum_ms=max(values),
                    mysql_over_engine=baseline / median,
                    returned_rows=reference[key][0], result_sha256=reference[key][1],
                ))
        report["summaries"] = summaries
        report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        report["all_query_results_equal"] = True
    finally:
        for con in connections.values():
            con.close()
        verifier.close()
        if created:
            # Only the unique database created by this invocation is eligible for cleanup.
            if not re.fullmatch(r"bench_duckdb_\d{8}t\d{6}z_[a-f0-9]{8}", database):
                raise RuntimeError("Refusing to clean unexpected database name")
            mysql.execute(f"DROP DATABASE {database}")
            report["scratch_database_removed"] = True
            print("Removed only this run's isolated benchmark database.", flush=True)
        mysql.close()
        write_json(private / "checkpoint.json", report)
    if report.get("all_query_results_equal") and report.get("scratch_database_removed"):
        import csv
        write_json(output / "benchmark.json", report)
        for name, records in [("timings.csv", report["measurements"]),
                              ("summary.csv", report["summaries"])]:
            keys = list(dict.fromkeys(k for record in records for k in record))
            with (output / name).open("w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.DictWriter(stream, fieldnames=keys)
                writer.writeheader()
                writer.writerows(records)
        print(json.dumps({"output": str(output), "summaries": summaries}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
