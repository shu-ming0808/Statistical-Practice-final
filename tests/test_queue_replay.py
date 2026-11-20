import json
from pathlib import Path
import unittest

import numpy as np

from scripts.report_queue_replay import ROOT, paired_arrivals


class QueueReplayTests(unittest.TestCase):
    def test_hindsight_keeps_forecast_chosen_minute_shape(self):
        predicted = np.array([100, 120, 300, 200, 90, 50], dtype=float)
        observed = np.array([220, 500, 180, 170, 80, 70], dtype=float)
        pred, actual = paired_arrivals(predicted, observed, "concentrated", 5, 5)
        np.testing.assert_allclose(pred[5:].reshape(6, 60).sum(axis=1), predicted)
        np.testing.assert_allclose(actual[5:].reshape(6, 60).sum(axis=1), observed)
        np.testing.assert_allclose(pred[5 + 2 * 60:5 + 3 * 60] / predicted[2],
                                   actual[5 + 2 * 60:5 + 3 * 60] / observed[2])
        # The largest observed hour is 19:00, but its shape remains uniform.
        self.assertAlmostEqual(float(actual[5 + 60:5 + 120].max()), observed[1] / 60)

    def test_published_replay_is_explicitly_hindsight(self):
        payload = json.loads((ROOT / "app/frontend/public/data/queue-replay.json").read_text(encoding="utf-8"))
        self.assertEqual(len(payload["events"]), 9)
        self.assertEqual(len(payload["sensitivity"]), 81)
        self.assertTrue(payload["assumptions"]["actual_is_hindsight"])
        self.assertFalse(payload["assumptions"]["historical_queue_reconstruction"])


if __name__ == "__main__":
    unittest.main()
