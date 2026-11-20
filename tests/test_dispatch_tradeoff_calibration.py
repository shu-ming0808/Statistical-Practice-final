"""Small accounting and chronology checks for dispatch diagnostic reports."""
import unittest

import pandas as pd

from scripts.calibrate_beimen_capacity import calibrate_past_only, match
from scripts.report_dispatch_tradeoff_split import build


class CalibrationChecks(unittest.TestCase):
    def test_diagnostic_wait_groups_reconcile(self):
        frame = build()
        group_delta = (frame.beimen_delta_person_minutes + frame.peer_delta_person_minutes
                       + frame.background_delta_person_minutes)
        self.assertTrue(((group_delta - frame.all_delta_person_minutes).abs() < 0.1).all())
        self.assertEqual(len(frame), 48)

    def test_matching_refuses_duplicate_observation_of_one_train(self):
        model = pd.DataFrame([dict(event_date="2024-08-10", direction="往松山",
            within_decision_window=True, departure_time="21:00:00",
            preboard_load_mean=900, preboard_load_p05=800, preboard_load_p95=1000,
            capacity_persons=2220)])
        observed = pd.DataFrame([
            dict(event_date="2024-08-10", direction="往松山", departure_time="21:00:20",
                 observed_preboard_load=1000),
            dict(event_date="2024-08-10", direction="往松山", departure_time="21:00:40",
                 observed_preboard_load=1000)])
        with self.assertRaisesRegex(ValueError, "Multiple observations"):
            match(observed, model, 2)

    def test_calibration_uses_only_earlier_events(self):
        dates = ["2024-08-10", "2025-08-30", "2026-08-15"]
        rows = []
        for date, observed in zip(dates, [100, 100, 200]):
            rows.append(dict(event_date=date, direction="往松山",
                matched_model_departure_time="21:00:00", observed_preboard_load=observed,
                simulated_preboard_load=100, capacity_persons=2220))
        result = calibrate_past_only(pd.DataFrame(rows))
        self.assertEqual(len(result), 1)
        self.assertEqual(result.event_date.iloc[0], dates[-1])
        self.assertEqual(result.fitted_scale.iloc[0], 1.0)
        self.assertEqual(result.calibrated_preboard_load.iloc[0], 100)


if __name__ == "__main__":
    unittest.main()
