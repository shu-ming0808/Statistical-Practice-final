"""Preparation order must not change seeded simulation inputs."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from prepare_green_dispatch import FOUR, prepare_event, sample_mass


class TinyNetwork:
    """Two directions and two post-transfer connections, no external files."""
    def __init__(self):
        self.green = [f'A{i:02}' for i in range(19)]
        self.gpos = np.arange(19, dtype=float)
        self.beimen = 5
        self.paths = {}
        for origin, end in [('A00', 'A02'), ('A18', 'A16')]:
            for target in ['R1', 'R2']:
                self.paths[origin, target] = [
                    ('G', origin, end, 2.), ('walk', end, 'R0', 5.),
                    ('R', 'R0', target, 2.),
                ]

    def legs(self, path):
        return path

    def fixed_schedule(self, line, origin, dest, day):
        return np.arange(0., 550., 3. if dest == 'R1' else 5.)


class PreparationOrderTests(unittest.TestCase):
    def test_shuffled_od_keeps_connection_ids_cohorts_and_seeded_draws_identical(self):
        od = pd.DataFrame([
            (17, 'A00', 'R1', 125.), (17, 'A00', 'R2', 236.),
            (17, 'A18', 'R1', 347.), (17, 'A18', 'R2', 458.),
        ], columns=['source_hour', 'origin_station', 'destination_station', 'passengers'])
        forecasts = pd.DataFrame([
            (pd.Timestamp(day), station, hour, 100., 110.)
            for day in ['2024-07-17', '2024-08-10']
            for station in FOUR for hour in range(18, 23)
        ], columns=['event_date', 'station', 'hour', 'predicted', 'y'])
        cfg = {'simulation_start_hour': 17, 'platform_access_minutes': 5,
               'transfer_walk_minutes': 5}

        def prepare(frame):
            supplied = (frame, ['2024-08-03', '2024-07-27'], {'od_sha256': 'test'})
            with patch('prepare_green_dispatch.past_od', return_value=supplied):
                return prepare_event('2024-08-10', forecasts, TinyNetwork(), cfg)

        first = prepare(od)
        second = prepare(od.iloc[[3, 1, 2, 0]].reset_index(drop=True))
        # Different first-seen post-transfer routes used to reassign connection
        # IDs and change the sorted cohort order before drawing Poisson counts.
        np.testing.assert_array_equal(first[1], second[1])
        for left, right in zip(first[0], second[0]):
            for field in left:
                np.testing.assert_array_equal(left[field], right[field])
        self.assertEqual(first[2], second[2])
        draws1, donors1 = sample_mass(first[0], forecasts, '2024-08-10', np.random.default_rng(42), 8)
        draws2, donors2 = sample_mass(second[0], forecasts, '2024-08-10', np.random.default_rng(42), 8)
        self.assertEqual(donors1, ['2024-07-17'])
        self.assertEqual(donors1, donors2)
        for left, right in zip(draws1, draws2):
            np.testing.assert_array_equal(left, right)


if __name__ == '__main__':
    unittest.main()
