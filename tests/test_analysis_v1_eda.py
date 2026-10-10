from pathlib import Path
import sys
import unittest

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from analysis_v1.eda import summarise_events


class EdaTests(unittest.TestCase):
    def fixture(self):
        rows = []
        for station in ("北門", "大橋頭站", "雙連", "民權西路"):
            for hour in range(18, 24):
                rows.append(dict(event_date=pd.Timestamp("2024-07-20"), year=2024, station=station,
                    hour=hour, y=100, baseline_hourly_56d=50, is_weekend=1, day_of_year=202,
                    year_index=7, temperature_at_report_time_c=30, precipitation_hour_ending_mm=0,
                    precipitation_status="observed", observed_has_drone_show=None,
                    observed_fireworks_seconds=300))
        return pd.DataFrame(rows)

    def test_weather_not_counted_four_times_and_event_counts_are_preserved(self):
        event, missing = summarise_events(self.fixture())
        self.assertEqual(event.loc[0, "origin_total"], 2400)
        self.assertEqual(event.loc[0, "baseline_total"], 1200)
        row = missing.set_index("variable").loc["temperature_at_report_time_c"]
        self.assertEqual(row.available_unique_values, 6)
        self.assertEqual(row.complete_events, 1)
        self.assertTrue(pd.isna(event.loc[0, "observed_has_drone_show"]))

    def test_trace_zero_is_not_treated_as_numeric_hourly_rain(self):
        frame = self.fixture()
        frame.loc[frame.hour.eq(18), "precipitation_status"] = "trace_T"
        # Even if upstream supplied numeric zero alongside T, EDA excludes it.
        event, missing = summarise_events(frame)
        self.assertTrue(pd.isna(event.loc[0, "rain_mean"]))
        self.assertEqual(event.loc[0, "rain_trace_bins"], 1)
        self.assertEqual(missing.set_index("variable").loc["precipitation_hour_ending_mm", "complete_events"], 0)

    def test_contradictory_shared_weather_is_not_silently_deduplicated(self):
        frame = self.fixture()
        frame.loc[0, "temperature_at_report_time_c"] = 99
        with self.assertRaisesRegex(ValueError, "Shared EDA attribute"):
            summarise_events(frame)


if __name__ == "__main__":
    unittest.main()
