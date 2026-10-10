"""Read and verify retrospective event-hour inputs without database access.

The historical observation dates precede each event. Their public release dates
have NOT been verified, so this loader does not certify D-1 deployability.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

STATIONS = ("北門", "大橋頭站", "雙連", "民權西路")
HOURS = tuple(range(18, 24))
MODEL_FEATURES = ("baseline_hourly_56d", "station", "hour", "is_weekend", "day_of_year", "year_index")
EDA_COLUMNS = (
    "temperature_at_report_time_c", "wind_preceding_10min_mean_m_s",
    "precipitation_hour_ending_mm", "precipitation_raw", "precipitation_status",
    "cloud_at_report_time_tenths", "cloud_measurement_type", "cloud_method_verified",
    "weather_report_hour_end", "weather_station_id",
)


def _require(frame: pd.DataFrame, columns, label: str) -> None:
    absent = sorted(set(columns) - set(frame.columns))
    if absent:
        raise ValueError(f"{label}: missing columns {absent}")


def _dates(values: pd.Series, label: str) -> pd.Series:
    result = pd.to_datetime(values, errors="raise")
    if result.isna().any() or isinstance(result.dtype, pd.DatetimeTZDtype):
        raise ValueError(f"{label}: missing or timezone-aware dates")
    if not result.eq(result.dt.normalize()).all():
        raise ValueError(f"{label}: expected dates at midnight")
    return result


def _counts(values: pd.Series, label: str) -> pd.Series:
    result = pd.to_numeric(values, errors="raise")
    if not np.isfinite(result.to_numpy(dtype=float)).all():
        raise ValueError(f"{label}: missing or non-finite counts")
    if (result < 0).any() or not result.eq(np.floor(result)).all():
        raise ValueError(f"{label}: expected nonnegative integer counts")
    return result


def _booleans(values: pd.Series, label: str) -> pd.Series:
    parsed = values.astype(str).str.strip().str.lower().map(
        {"true": True, "false": False, "1": True, "0": False, "1.0": True, "0.0": False})
    if parsed.isna().any():
        raise ValueError(f"{label}: unknown boolean value")
    return parsed.astype(bool)


def validate_event_grid(frame: pd.DataFrame) -> None:
    """Require one complete four-station/six-hour grid for each retained event."""
    _require(frame, ("event_date", "station", "hour"), "event grid")
    keys = ["event_date", "station", "hour"]
    if frame.empty or frame[keys].isna().any().any():
        raise ValueError("event grid: empty or missing keys")
    if frame.duplicated(keys).any():
        raise ValueError("event grid: duplicate event/station/hour key")
    expected = {(station, hour) for station in STATIONS for hour in HOURS}
    for day, group in frame.groupby("event_date"):
        actual = set(zip(group.station, group.hour))
        if len(group) != 24 or actual != expected:
            raise ValueError(f"event grid: {day} lacks the complete four-station/six-hour grid")


def validate_selected_candidates(selected: pd.DataFrame, event_dates) -> None:
    """Reject future, same-day, event-day and non-weekly comparison dates."""
    _require(selected, ("event_date", "station", "candidate_date", "lag_days"), "candidates")
    if selected.empty or selected.duplicated(["event_date", "station", "candidate_date"]).any():
        raise ValueError("candidates: empty or duplicate selected comparison dates")
    actual_lag = (selected.event_date - selected.candidate_date).dt.days
    declared_lag = pd.to_numeric(selected.lag_days, errors="raise")
    if not (actual_lag.between(7, 56) & actual_lag.mod(7).eq(0) & actual_lag.eq(declared_lag)).all():
        raise ValueError("candidates: comparison dates must be 7..56 days before the event in whole weeks")
    if selected.candidate_date.isin(set(event_dates)).any():
        raise ValueError("candidates: listed activity date used as a comparison date")
    sizes = selected.groupby(["event_date", "station"]).size()
    if not sizes.between(2, 4).all():
        raise ValueError("candidates: each event/station needs two to four comparison dates")


def _manifest_entry(root: Path, path: Path) -> dict:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": path.relative_to(root).as_posix(), "sha256": digest.hexdigest(), "bytes": path.stat().st_size}


def load_analysis_data(root: Path) -> tuple[pd.DataFrame, dict]:
    """Return verified event-hour targets, retrospective features and audit metadata.

    EDA-only columns remain on the returned frame. Callers MUST select inputs via
    MODEL_FEATURES (or a documented subset), never all numeric columns.
    """
    root = Path(root).resolve()
    context_path = root / "data/processed/event_station_hourly_context.csv"
    candidates_path = root / "data/processed/calendar_baseline/candidate_audit.csv"
    hourly_path = root / "data/processed/station_hourly.parquet"
    paths = [context_path, candidates_path, hourly_path]
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)
    manifest = [_manifest_entry(root, path) for path in paths]
    context = pd.read_csv(context_path)
    _require(context, ("event_date", "station", "hour_start", "event_status", "origin_count",
                       "baseline_window_mean_56d", "baseline_dates_56d", "baseline_n_days_56d"), "context")
    context["event_date"] = _dates(context.event_date, "context event_date")
    event_dates = set(context.event_date)
    if context.groupby("event_date").event_status.nunique(dropna=False).gt(1).any():
        raise ValueError("context: contradictory activity status within one date")
    timestamps = pd.to_datetime(context.hour_start, errors="raise")
    if timestamps.isna().any() or isinstance(timestamps.dtype, pd.DatetimeTZDtype):
        raise ValueError("context: missing or timezone-aware hour_start")
    if not (timestamps.eq(timestamps.dt.floor("h")) & timestamps.dt.normalize().eq(context.event_date)).all():
        raise ValueError("context: hour_start must be an exact hour of event_date")
    context["hour"] = timestamps.dt.hour.astype(int)
    target = context.loc[context.event_status.eq("held") & context.station.isin(STATIONS)
                         & context.hour.isin(HOURS)].copy()
    validate_event_grid(target)
    target["y"] = _counts(target.origin_count, "target origin_count").astype(float)

    candidates = pd.read_csv(candidates_path)
    _require(candidates, ("event_date", "station", "candidate_date", "lag_days", "selected"), "candidate audit")
    candidates["event_date"] = _dates(candidates.event_date, "candidate event_date")
    candidates["candidate_date"] = _dates(candidates.candidate_date, "candidate_date")
    candidates["selected"] = _booleans(candidates.selected, "candidate selected")
    selected = candidates.loc[candidates.selected & candidates.event_date.isin(target.event_date)
                              & candidates.station.isin(STATIONS)].copy()
    validate_selected_candidates(selected, event_dates)
    pairs = ["event_date", "station"]
    expected_pairs = set(map(tuple, target[pairs].drop_duplicates().to_numpy()))
    if set(map(tuple, selected[pairs].drop_duplicates().to_numpy())) != expected_pairs:
        raise ValueError("candidates: selected event/station coverage differs from targets")

    # Restrict the already aggregated Parquet to required dates/stations only.
    history_keys = selected[["candidate_date", "station"]].rename(columns={"candidate_date": "day"})
    target_keys = target[["event_date", "station"]].rename(columns={"event_date": "day"})
    read_keys = pd.concat([history_keys, target_keys]).drop_duplicates()
    with duckdb.connect() as connection:
        connection.register("needed_days", read_keys)
        source = connection.execute("""SELECT s.service_date, s.station, s.source_hour,
            s.origin_count, s.origin_complete FROM read_parquet(?) s
            JOIN needed_days k ON s.service_date=k.day AND s.station=k.station
            WHERE s.source_hour BETWEEN 18 AND 23""", [str(hourly_path)]).df()
    source["service_date"] = _dates(source.service_date, "source service_date")
    if source.duplicated(["service_date", "station", "source_hour"]).any():
        raise ValueError("source: duplicate station/date/hour")
    source["origin_count"] = _counts(source.origin_count, "source origin_count")
    if not _booleans(source.origin_complete, "source origin_complete").all():
        raise ValueError("source: incomplete origin hours")

    checked_target = target.merge(source, left_on=["event_date", "station", "hour"],
                                  right_on=["service_date", "station", "source_hour"],
                                  how="left", validate="one_to_one", suffixes=("", "_source"))
    if checked_target.origin_count_source.isna().any() or not checked_target.y.eq(checked_target.origin_count_source).all():
        raise ValueError("target: context counts disagree with source Parquet")
    history = selected.merge(source, left_on=["candidate_date", "station"],
                             right_on=["service_date", "station"], how="left", validate="many_to_many")
    for key, group in history.groupby(["event_date", "station", "candidate_date"]):
        if len(group) != 6 or set(group.source_hour) != set(HOURS) or group.origin_count.isna().any():
            raise ValueError(f"history: selected comparison {key} lacks six complete hours")
        if "candidate_origin_total" in group and not np.allclose(group.origin_count.sum(), group.candidate_origin_total, rtol=0, atol=1e-8):
            raise ValueError("history: candidate total disagrees with source Parquet")

    for key, group in target.groupby(pairs):
        chosen = selected.loc[selected.event_date.eq(key[0]) & selected.station.eq(key[1])]
        expected_dates = sorted(chosen.candidate_date.dt.strftime("%Y-%m-%d").tolist())
        for row in group.itertuples():
            try:
                declared = json.loads(row.baseline_dates_56d)
            except (TypeError, ValueError) as exc:
                raise ValueError("context: invalid baseline_dates_56d JSON") from exc
            if not isinstance(declared, list) or sorted(declared) != expected_dates:
                raise ValueError("context: selected comparison dates disagree with baseline_dates_56d")
            if row.baseline_n_days_56d != len(expected_dates):
                raise ValueError("context: baseline_n_days_56d disagrees with selected dates")
    baseline = history.groupby(["event_date", "station", "source_hour"], as_index=False).origin_count.mean()
    baseline = baseline.rename(columns={"source_hour": "hour", "origin_count": "baseline_hourly_56d"})
    target = target.merge(baseline, on=["event_date", "station", "hour"], how="left", validate="one_to_one")
    totals = target.groupby(pairs).baseline_hourly_56d.transform("sum")
    declared_totals = pd.to_numeric(target.baseline_window_mean_56d, errors="raise")
    if not np.allclose(totals, declared_totals, rtol=1e-10, atol=1e-6, equal_nan=False):
        raise ValueError("context: sum of hourly baselines disagrees with six-hour baseline total")
    if target.baseline_hourly_56d.isna().any() or not np.isfinite(target.baseline_hourly_56d).all() or target.baseline_hourly_56d.lt(0).any():
        raise ValueError("core: invalid baseline_hourly_56d")

    target["year"] = target.event_date.dt.year.astype(int)
    target["is_weekend"] = target.event_date.dt.dayofweek.ge(5).astype(int)
    target["day_of_year"] = target.event_date.dt.dayofyear.astype(int)
    year_origin = int(target.year.min())
    target["year_index"] = target.year - year_origin
    columns = ["event_date", "year", "station", "hour", "y", *MODEL_FEATURES[:1],
               "is_weekend", "day_of_year", "year_index"]
    columns += [name for name in EDA_COLUMNS if name in target]
    output = target[columns].copy()
    optional_path = root / ".local/research_review/2026-10-07-refresh/statistics_snapshot.json"
    optional_status = "missing; observed drone and actual duration omitted"
    if optional_path.is_file():
        events = pd.DataFrame(json.loads(optional_path.read_text(encoding="utf-8-sig"))["events"])
        _require(events, ("event_date", "has_drone_show", "fireworks_duration_seconds"), "optional activity snapshot")
        events["event_date"] = _dates(events.event_date, "optional snapshot event_date")
        if events.event_date.duplicated().any():
            raise ValueError("optional activity snapshot: duplicate dates")
        events = events[["event_date", "has_drone_show", "fireworks_duration_seconds"]].rename(columns={
            "has_drone_show": "observed_has_drone_show", "fireworks_duration_seconds": "observed_fireworks_seconds"})
        output = output.merge(events, on="event_date", how="left", validate="many_to_one")
        manifest.append(_manifest_entry(root, optional_path))
        optional_status = "loaded for EDA only; actual outcomes are excluded from MODEL_FEATURES"
    output = output.sort_values(["event_date", "station", "hour"]).reset_index(drop=True)
    validate_event_grid(output)
    activity = target[["event_date", "year"]].drop_duplicates().sort_values("event_date")
    eligibility = {name: {"use": "retrospective_model", "d1_deployable": False if name == "baseline_hourly_56d" else True}
                   for name in MODEL_FEATURES}
    eligibility.update({
        "observed_weather": {"use": "EDA_only", "reason": "Measured after forecast cutoff; no historical forecast vintage supplied."},
        "observed_has_drone_show": {"use": "EDA_only", "reason": "Sparse actual event status; not a verified pre-event announcement."},
        "observed_fireworks_seconds": {"use": "EDA_only", "reason": "Actual duration, not verified planned duration available at D-1."},
        "annual_finance": {"use": "excluded", "reason": "Annual-level sparse funding and unverified release timing."},
        "social_trends": {"use": "excluded", "reason": "No comparable complete historical D-1 series."},
    })
    metadata = {
        "manifest": manifest,
        "audit": {"evaluation_status": "retrospective_non_deployable", "d1_deployable": False,
                  "reason": "Historical OD publication/available_at timestamps are unverified; chronological observation dates alone do not prove D-1 availability.",
                  "baseline_policy": "same_weekday_calendar_56d_nearest4_v1", "selected_dates_strictly_past": True,
                  "baseline_source_lag_days": [int(selected.lag_days.min()), int(selected.lag_days.max())],
                  "calendar_note": "Existing selected-date audit uses a retrospective government calendar; historical calendar vintages are not verified.",
                  "target_definition": "Origin trips in source hourly bins; not verified platform arrivals or physical passenger trajectories.",
                  "model_feature_whitelist": list(MODEL_FEATURES), "year_index_origin": year_origin,
                  "optional_activity_snapshot": optional_status, "source_target_counts_match": True,
                  "hourly_and_window_baseline_match": True, "comparison_date_lists_match": True},
        "missing_summary": {name: int(output[name].isna().sum()) for name in output.columns},
        "feature_eligibility": eligibility,
        "activity_counts": {"context_dates": len(event_dates), "held_dates": len(activity),
                            "rows": len(output), "years": int(activity.year.nunique()),
                            "dates": activity.event_date.dt.strftime("%Y-%m-%d").tolist(),
                            "by_year": {str(year): int(n) for year, n in activity.groupby("year").size().items()},
                            "by_status": {str(status): int(n) for status, n in context[["event_date", "event_status"]].drop_duplicates().groupby("event_status").size().items()}},
    }
    return output, metadata
