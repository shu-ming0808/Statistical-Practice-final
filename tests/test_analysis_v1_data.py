from datetime import timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from analysis_v1.data import HOURS, STATIONS, load_analysis_data, validate_event_grid, validate_selected_candidates


class DataLoaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.processed = self.root / "data/processed"
        (self.processed / "calendar_baseline").mkdir(parents=True)
        self.day = pd.Timestamp("2024-07-20")
        self.history_days = [self.day - timedelta(days=7), self.day - timedelta(days=14)]
        self.context, self.candidates, self.source = [], [], []
        for station in STATIONS:
            for lag, history_day in zip([7, 14], self.history_days):
                self.candidates.append(dict(event_date=self.day, station=station, candidate_date=history_day,
                                            lag_days=lag, selected=True, candidate_origin_total=600 if lag == 7 else 1200))
                for hour in HOURS:
                    self.source.append(dict(service_date=history_day, station=station, source_hour=hour,
                                            origin_count=100 if lag == 7 else 200, origin_complete=True))
            for hour in HOURS:
                self.context.append(dict(event_date=self.day, station=station,
                    hour_start=self.day + timedelta(hours=hour), event_status="held", origin_count=300,
                    baseline_window_mean_56d=900, baseline_n_days_56d=2,
                    baseline_dates_56d=json.dumps([d.strftime("%Y-%m-%d") for d in self.history_days]),
                    precipitation_hour_ending_mm=None, precipitation_raw="T", precipitation_status="trace_T"))
                self.source.append(dict(service_date=self.day, station=station, source_hour=hour,
                                        origin_count=300, origin_complete=True))

    def write_fixture(self):
        pd.DataFrame(self.context).to_csv(self.processed / "event_station_hourly_context.csv", index=False)
        pd.DataFrame(self.candidates).to_csv(self.processed / "calendar_baseline/candidate_audit.csv", index=False)
        frame = pd.DataFrame(self.source)
        path = (self.processed / "station_hourly.parquet").as_posix().replace("'", "''")
        with duckdb.connect() as con:
            con.register("fixture", frame)
            con.execute(f"COPY fixture TO '{path}' (FORMAT PARQUET)")

    def test_reconstructs_hourly_baseline_and_preserves_unknown_rain(self):
        self.write_fixture()
        frame, meta = load_analysis_data(self.root)
        self.assertEqual(len(frame), 24)
        self.assertTrue(frame.baseline_hourly_56d.eq(150).all())
        self.assertTrue(frame.y.eq(300).all())
        self.assertTrue(frame.precipitation_hour_ending_mm.isna().all())
        self.assertTrue(frame.precipitation_status.eq("trace_T").all())
        self.assertEqual(meta["activity_counts"]["held_dates"], 1)
        self.assertFalse(meta["audit"]["d1_deployable"])
        self.assertEqual(len(meta["manifest"]), 3)
        self.assertNotIn("observed_has_drone_show", frame)

    def test_future_comparison_date_rejected(self):
        self.candidates[0]["candidate_date"] = self.day + timedelta(days=7)
        self.write_fixture()
        with self.assertRaisesRegex(ValueError, "before the event"):
            load_analysis_data(self.root)

    def test_missing_historical_hour_rejected(self):
        self.source.pop(0)
        self.write_fixture()
        with self.assertRaisesRegex(ValueError, "six complete hours"):
            load_analysis_data(self.root)

    def test_target_and_source_disagreement_rejected(self):
        self.context[0]["origin_count"] = 301
        self.write_fixture()
        with self.assertRaisesRegex(ValueError, "context counts disagree"):
            load_analysis_data(self.root)

    def test_negative_or_fractional_target_rejected(self):
        for value in [-1, 0.5, float("nan")]:
            with self.subTest(value=value):
                self.context[0]["origin_count"] = value
                self.write_fixture()
                with self.assertRaises(ValueError):
                    load_analysis_data(self.root)

    def test_context_baseline_total_disagreement_rejected(self):
        self.context[0]["baseline_window_mean_56d"] = 901
        self.write_fixture()
        with self.assertRaisesRegex(ValueError, "six-hour baseline total"):
            load_analysis_data(self.root)

    def test_context_baseline_date_disagreement_rejected(self):
        self.context[0]["baseline_dates_56d"] = '["2024-07-13", "2024-06-29"]'
        self.write_fixture()
        with self.assertRaisesRegex(ValueError, "comparison dates disagree"):
            load_analysis_data(self.root)

    def test_incomplete_source_flag_rejected(self):
        self.source[0]["origin_complete"] = False
        self.write_fixture()
        with self.assertRaisesRegex(ValueError, "incomplete origin hours"):
            load_analysis_data(self.root)

    def test_optional_snapshot_is_eda_only(self):
        path = self.root / ".local/research_review/2026-10-07-refresh/statistics_snapshot.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"events": [{"event_date": "2024-07-20", "has_drone_show": None,
                                               "fireworks_duration_seconds": 300}]}), encoding="utf-8")
        self.write_fixture()
        frame, meta = load_analysis_data(self.root)
        self.assertTrue(frame.observed_has_drone_show.isna().all())
        self.assertNotIn("observed_fireworks_seconds", meta["audit"]["model_feature_whitelist"])
        self.assertEqual(len(meta["manifest"]), 4)

    def test_duplicate_grid_key_rejected(self):
        self.context.append(self.context[0].copy())
        self.write_fixture()
        with self.assertRaisesRegex(ValueError, "duplicate event/station/hour"):
            load_analysis_data(self.root)

    def test_whole_missing_station_rejected(self):
        self.context = self.context[:18]
        self.write_fixture()
        with self.assertRaisesRegex(ValueError, "four-station/six-hour"):
            load_analysis_data(self.root)

    def test_event_date_never_eligible_as_historical_control(self):
        selected = pd.DataFrame(self.candidates)
        with self.assertRaisesRegex(ValueError, "listed activity date"):
            validate_selected_candidates(selected, [self.day, self.history_days[0]])


if __name__ == "__main__":
    unittest.main()
