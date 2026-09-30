"""Build station panels and a directed OD demand graph from the same clean fact."""
from __future__ import annotations

import json
from pathlib import Path

from data_preprocessing import literal
from load_data import install_events


def export_table(con, name, directory, csv=True):
    parquet = directory / f"{name}.parquet"
    part = parquet.with_suffix(".part")
    con.execute(f"COPY {name} TO {literal(part.as_posix())} (FORMAT PARQUET, COMPRESSION ZSTD)")
    part.replace(parquet)
    if csv:
        destination = directory / f"{name}.csv"
        part = destination.with_suffix(".csv.part")
        con.execute(f"COPY {name} TO {literal(part.as_posix())} (FORMAT CSV, HEADER, NULL '')")
        part.replace(destination)


def build_analysis(con, clean_paths: list[Path], records: list[dict], output: Path,
                   start_hour=18, end_hour=24):
    if not 0 <= start_hour < end_hour <= 48:
        raise ValueError("Require 0 <= start_hour < end_hour <= 48; end is exclusive")
    if not clean_paths:
        raise ValueError("No clean files")
    output.mkdir(parents=True, exist_ok=True)
    path_list = "[" + ",".join(literal(p.as_posix()) for p in clean_paths) + "]"
    con.execute(f"CREATE OR REPLACE VIEW od AS SELECT * FROM read_parquet({path_list})")
    install_events(con, records)
    con.execute("""CREATE OR REPLACE TABLE station_labels AS
      SELECT station, min(service_date) AS first_seen_date, max(service_date) AS last_seen_date,
      bool_or(role='origin') AS seen_as_origin, bool_or(role='destination') AS seen_as_destination
      FROM (SELECT origin_station AS station,service_date,'origin' AS role FROM od
            UNION ALL SELECT destination_station,service_date,'destination' FROM od)
      GROUP BY station ORDER BY station""")
    # Rectangular origin/destination universes are legitimate in these source files.
    con.execute("""CREATE OR REPLACE TEMP TABLE bin_coverage AS
      SELECT service_date,source_hour,count(DISTINCT origin_station) AS n_origins,
      count(DISTINCT destination_station) AS n_destinations,count(*) AS n_pairs
      FROM od GROUP BY ALL""")
    con.execute("""CREATE OR REPLACE TABLE station_hourly AS
      WITH origins AS (
        SELECT service_date,source_hour,origin_station AS station,
          sum(passengers)::BIGINT AS n,count(*) AS pairs,
          count(*) FILTER(WHERE passengers>0 AND origin_station<>destination_station) AS degree
        FROM od GROUP BY ALL), destinations AS (
        SELECT service_date,source_hour,destination_station AS station,
          sum(passengers)::BIGINT AS n,count(*) AS pairs,
          count(*) FILTER(WHERE passengers>0 AND origin_station<>destination_station) AS degree
        FROM od GROUP BY ALL)
      SELECT coalesce(o.service_date,d.service_date) AS service_date,
        coalesce(o.source_hour,d.source_hour) AS source_hour,
        coalesce(o.station,d.station) AS station,
        CASE WHEN o.pairs=b.n_destinations THEN o.n END AS origin_count,
        CASE WHEN d.pairs=b.n_origins THEN d.n END AS destination_count_same_source_bin,
        coalesce(o.pairs=b.n_destinations,false) AS origin_complete,
        coalesce(d.pairs=b.n_origins,false) AS destination_complete,
        o.pairs AS origin_pair_count,d.pairs AS destination_pair_count,
        o.degree AS out_degree_excluding_self,d.degree AS in_degree_excluding_self
      FROM origins o FULL OUTER JOIN destinations d USING(service_date,source_hour,station)
      JOIN bin_coverage b ON b.service_date=coalesce(o.service_date,d.service_date)
        AND b.source_hour=coalesce(o.source_hour,d.source_hour)
      ORDER BY service_date,source_hour,station""")
    con.execute("""CREATE OR REPLACE TEMP TABLE station_month AS
      SELECT DISTINCT strftime(service_date,'%Y%m') AS source_month,station FROM station_hourly""")
    # Always retain the whole event date; optionally extend into the following date.
    con.execute(f"""CREATE OR REPLACE TEMP TABLE event_grid AS
      WITH event_stations AS (
        SELECT DISTINCT e.event_date,m.station FROM events e JOIN station_month m
        ON m.source_month IN (strftime(e.event_date,'%Y%m'),
          strftime(e.event_date+{max(24,int(end_hour))-1}*INTERVAL '1 hour','%Y%m')))
      SELECT e.*,r.i AS hour_offset, e.event_date + r.i * INTERVAL '1 hour' AS hour_start,
        r.i >= {int(start_hour)} AND r.i < {int(end_hour)} AS in_target_window,
        m.station
      FROM events e CROSS JOIN range(0,{max(24,int(end_hour))}) r(i)
      JOIN event_stations m ON m.event_date=e.event_date""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE baseline AS
      WITH eligible AS (
        SELECT b.* FROM station_hourly b WHERE b.origin_complete
          AND NOT EXISTS(SELECT 1 FROM events x WHERE x.event_date=b.service_date)
          AND NOT EXISTS(SELECT 1 FROM events x WHERE x.event_date=b.service_date-1
            AND b.source_hour<{max(0,int(end_hour)-24)}))
      SELECT g.event_date,g.hour_offset,g.station,
        avg(b.origin_count) AS baseline_origin_mean_28d,
        count(b.origin_count)::INTEGER AS baseline_n_days,
        max(b.origin_count) FILTER(WHERE date_diff('day',b.service_date,g.hour_start::DATE)=7)
          AS origin_lag_7d
      FROM event_grid g CROSS JOIN range(1,5) k(i)
      LEFT JOIN eligible b ON b.station=g.station
        AND b.source_hour=hour(g.hour_start)
        AND b.service_date<g.event_date
        AND b.service_date=(g.hour_start::DATE-k.i*7*INTERVAL '1 day')::DATE
      GROUP BY g.event_date,g.hour_offset,g.station""")
    con.execute("""CREATE OR REPLACE TABLE regression_station_hourly AS
      SELECT g.event_date,g.hour_start,g.station,g.hour_offset,
        hour(g.hour_start)::SMALLINT AS source_hour,g.in_target_window,
        year(g.event_date)::SMALLINT AS event_year,month(g.event_date)::SMALLINT AS event_month,
        isodow(g.event_date)::SMALLINT AS event_weekday,
        isodow(g.event_date) IN (6,7) AS is_weekend,
        g.event_name,g.event_status AS observed_event_status,
        g.announced_show_start_time,g.fireworks_duration_seconds AS observed_fireworks_seconds,
        g.combined_show_duration_seconds AS observed_combined_show_seconds,
        g.has_drone_show AS observed_has_drone_show,
        s.origin_count,s.destination_count_same_source_bin,
        coalesce(s.origin_complete,false) AS origin_complete,
        coalesce(s.destination_complete,false) AS destination_complete,
        s.out_degree_excluding_self,s.in_degree_excluding_self,
        b.baseline_origin_mean_28d,b.baseline_n_days,b.origin_lag_7d,
        s.origin_count-b.baseline_origin_mean_28d AS origin_excess_vs_baseline,
        g.interest_1d,g.interest_3d_avg,g.interest_7d_avg,
        g.trends_import_id,g.trends_resolution,g.trends_time_basis,
        g.interest_7d_avg IS NULL AS trends_missing,
        false AS trends_cross_event_calibrated
      FROM event_grid g LEFT JOIN station_hourly s ON s.service_date=g.hour_start::DATE
        AND s.source_hour=hour(g.hour_start) AND s.station=g.station
      JOIN baseline b ON b.event_date=g.event_date AND b.hour_offset=g.hour_offset
        AND b.station=g.station
      ORDER BY g.event_date,g.hour_start,g.station""")
    con.execute(f"""CREATE OR REPLACE TABLE regression_event_station AS
      WITH totals AS (
        SELECT event_date,station,
          {int(start_hour)}::SMALLINT AS window_start_hour,
          {int(end_hour)}::SMALLINT AS window_end_hour_exclusive,
          count(*)::INTEGER AS expected_hours,
          count(origin_count)::INTEGER AS observed_origin_hours,
          count(destination_count_same_source_bin)::INTEGER AS observed_destination_hours,
          CASE WHEN count(origin_count)=count(*) THEN sum(origin_count)::BIGINT END
            AS origin_count_window,
          CASE WHEN count(destination_count_same_source_bin)=count(*)
            THEN sum(destination_count_same_source_bin)::BIGINT END AS destination_count_window,
          CASE WHEN count(origin_count)=count(*) THEN max(origin_count)::BIGINT END
            AS peak_origin_hour_count,
          CASE WHEN count(baseline_origin_mean_28d)=count(*)
            THEN sum(baseline_origin_mean_28d) END AS baseline_origin_window,
          min(baseline_n_days)::INTEGER AS baseline_min_days,
          bool_and(origin_complete) AS origin_window_complete
        FROM regression_station_hourly WHERE in_target_window GROUP BY event_date,station)
      SELECT t.*,e.event_name,e.event_status AS observed_event_status,
        e.has_drone_show AS observed_has_drone_show,
        isodow(e.event_date)::SMALLINT AS event_weekday,
        isodow(e.event_date) IN (6,7) AS is_weekend,
        e.interest_1d,e.interest_3d_avg,e.interest_7d_avg,
        e.interest_7d_avg IS NULL AS trends_missing,
        false AS trends_cross_event_calibrated,
        t.origin_count_window-t.baseline_origin_window AS origin_excess_vs_baseline
      FROM totals t JOIN events e USING(event_date) ORDER BY event_date,station""")
    con.execute(f"""CREATE OR REPLACE TABLE flow_edges_hourly AS
      SELECT e.event_date,o.hour_start,o.source_hour,
        o.origin_station AS source,o.destination_station AS target,
        o.passengers AS weight,o.origin_station=o.destination_station AS is_self_loop,
        date_diff('hour',e.event_date::TIMESTAMP,o.hour_start)>={int(start_hour)}
          AND date_diff('hour',e.event_date::TIMESTAMP,o.hour_start)<{int(end_hour)} AS in_target_window
      FROM od o JOIN events e ON o.hour_start>=e.event_date
        AND o.hour_start<e.event_date+{max(24,int(end_hour))} * INTERVAL '1 hour'
      WHERE o.passengers>0 ORDER BY e.event_date,o.hour_start,source,target""")
    con.execute("""CREATE OR REPLACE TABLE flow_nodes_hourly AS
      SELECT event_date,hour_start,station AS node,origin_count AS out_strength,
        destination_count_same_source_bin AS in_strength,
        origin_count-destination_count_same_source_bin AS net_out_strength,
        out_degree_excluding_self,in_degree_excluding_self,
        origin_complete,destination_complete,in_target_window
      FROM regression_station_hourly ORDER BY event_date,hour_start,node""")
    # Verify aggregation before publishing files. Each trip has exactly one origin and destination.
    od_total = int(con.execute("SELECT sum(passengers) FROM od").fetchone()[0])
    origin_total, dest_total = con.execute(
        "SELECT sum(origin_count),sum(destination_count_same_source_bin) FROM station_hourly").fetchone()
    incomplete_bins = con.execute(
        "SELECT count(*) FROM bin_coverage WHERE n_pairs<>n_origins*n_destinations").fetchone()[0]
    if not incomplete_bins and (od_total != origin_total or od_total != dest_total):
        raise ValueError("Passenger conservation failed")
    edge_total=con.execute("SELECT coalesce(sum(weight),0) FROM flow_edges_hourly").fetchone()[0]
    node_out,node_in=con.execute("SELECT coalesce(sum(out_strength),0),coalesce(sum(in_strength),0) FROM flow_nodes_hourly").fetchone()
    if not incomplete_bins and (edge_total!=node_out or edge_total!=node_in):
        raise ValueError("Event graph passenger conservation failed")
    tables = ["station_labels","station_hourly","regression_station_hourly",
              "regression_event_station","flow_edges_hourly","flow_nodes_hourly"]
    for table in tables:
        export_table(con,table,output,csv=table not in {"station_hourly","flow_edges_hourly"})
    con.execute("""CREATE OR REPLACE TABLE beimen_event_hourly AS
      SELECT * FROM regression_station_hourly WHERE station='北門' ORDER BY event_date,hour_start""")
    export_table(con,"beimen_event_hourly",output)
    report = dict(events=len(records),window_start_hour=start_hour,window_end_hour_exclusive=end_hour,
      od_passengers=od_total,origin_passengers=int(origin_total),destination_passengers=int(dest_total),
      incomplete_od_bins=incomplete_bins,
      event_graph_passengers=int(edge_total),
      row_counts={t:con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables},
      events_without_od=[str(r[0]) for r in con.execute("""SELECT e.event_date FROM events e
        WHERE NOT EXISTS(SELECT 1 FROM station_hourly s WHERE s.service_date=e.event_date)""").fetchall()],
      complete_event_station_windows=con.execute(
        "SELECT count(*) FROM regression_event_station WHERE origin_window_complete").fetchone()[0],
      missing_trends_events=con.execute("SELECT count(*) FROM events WHERE interest_7d_avg IS NULL").fetchone()[0])
    return report
