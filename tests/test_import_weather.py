import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from import_weather import HEADER, parse_csv, sql_value


class WeatherImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "weather.csv"
        self.row = dict.fromkeys(HEADER, "")
        self.row.update(event_date="2026/7/25", station_id="466920",
                        station_name="臺北", county="臺北市", station_role="primary",
                        distance_to_dadaocheng_km="2.2", datetime_tst="2026/7/25 23:59",
                        air_temperature_c="29.7", hourly_precipitation_mm="0",
                        precipitation_raw_value="0", precipitation_status="observed",
                        mean_wind_speed_m_s="1.5", total_cloud_amount_tenths="0",
                        cloud_measurement_type="satellite_retrieved", cloud_status="observed",
                        source="CWA CODiS")

    def tearDown(self):
        self.tmp.cleanup()

    def parse(self, *rows):
        with self.path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=HEADER)
            writer.writeheader()
            writer.writerows(rows)
        return parse_csv(self.path)[0]

    def test_source_time_and_zero_preserved(self):
        obs = self.parse(self.row)["weather_observation"][0]
        self.assertEqual(obs["observed_at"], datetime(2026, 7, 25, 23, 59))
        self.assertEqual(obs["datetime_tst_raw"], "2026/7/25 23:59")
        self.assertEqual(obs["time_alignment_status"], "end_of_day_unverified")
        self.assertEqual(obs["total_cloud_amount_tenths"], Decimal(0))
        self.assertIsNone(obs["temperature_quality_flag"])
        self.assertFalse(obs["cloud_method_review_required"])

    def test_duplicate_observation_key_rejected_even_if_identical(self):
        with self.assertRaisesRegex(ValueError, "Duplicate observation PK"):
            self.parse(self.row, self.row)

    def test_different_station_same_time_allowed_and_metadata_normalized(self):
        second = dict(self.row, station_id="C0AI30", station_name="三重", county="新北市",
                      station_role="nearby cross-river robustness station from 2019-08-02",
                      total_cloud_amount_tenths="", cloud_status="not_applicable",
                      cloud_measurement_type="station_does_not_observe_cloud")
        tables = self.parse(self.row, second)
        self.assertEqual(len(tables["weather_observation"]), 2)
        self.assertEqual(tables["event_weather_station"][1]["station_role"], "robustness")
        self.assertIsNone(tables["weather_observation"][1]["total_cloud_amount_tenths"])

    def test_two_primary_stations_rejected(self):
        with self.assertRaisesRegex(ValueError, "exactly one primary"):
            self.parse(self.row, dict(self.row, station_id="C0AI30"))

    def test_conflicting_station_metadata_rejected(self):
        with self.assertRaisesRegex(ValueError, "Conflicting station metadata"):
            self.parse(self.row, dict(self.row, station_name="另一站", datetime_tst="2026/7/25 22:00"))

    def test_special_rain_codes_retained_with_null(self):
        for status, raw in [("trace_T", "-9.8"), ("accumulated_later_ampersand", "-999.6")]:
            with self.subTest(status=status):
                obs = self.parse(dict(self.row, hourly_precipitation_mm="",
                                      precipitation_raw_value=raw, precipitation_status=status))["weather_observation"][0]
                self.assertIsNone(obs["hourly_precipitation_mm"])
                self.assertEqual(obs["precipitation_raw_value"], raw)

    def test_invalid_weather_values_rejected(self):
        for field, value in [("hourly_precipitation_mm", "-9.8"),
                             ("total_cloud_amount_tenths", "11"),
                             ("mean_wind_speed_m_s", "NaN"),
                             ("air_temperature_c", "1.2345")]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.parse(dict(self.row, **{field: value}))

    def test_value_status_contradictions_rejected(self):
        for changes in [dict(cloud_status="missing"), dict(precipitation_status="trace_T")]:
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "status/value mismatch"):
                self.parse(dict(self.row, **changes))

    def test_unconfirmed_date_change_rejected(self):
        with self.assertRaisesRegex(ValueError, "Event/time date mismatch"):
            self.parse(dict(self.row, datetime_tst="2026/7/26 00:00"))

    def test_historical_satellite_marked_without_rewriting_method(self):
        obs = self.parse(dict(self.row, event_date="2023/7/5", datetime_tst="2023/7/5 01:00"))["weather_observation"][0]
        self.assertTrue(obs["cloud_method_review_required"])
        self.assertEqual(obs["cloud_measurement_type"], "satellite_retrieved")

    def test_sql_text_is_hex_encoded(self):
        value = "'\\;DROP TABLE test; -- 中文"
        self.assertEqual(sql_value(value), "CONVERT(0x" + value.encode("utf-8").hex() + " USING utf8mb4)")
        self.assertEqual(sql_value(None), "NULL")


if __name__ == "__main__":
    unittest.main()
