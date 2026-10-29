import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from green_dispatch import simulate_direction
from green_load_trace import check_trace, selected_plan, trace_direction


class GreenLoadTraceTests(unittest.TestCase):
    def run_both(self, mass, times, exits, bounds, offsets, trains, capacity,
                 station=1, groups=None, post=None, pre_wait=None, connections=None):
        mass = np.asarray(mass, dtype=np.float32)
        nk = mass.shape[1]
        args = (mass, np.asarray(times, dtype=float), np.asarray(exits, dtype=np.int64),
                np.asarray(groups if groups is not None else [0] * nk, dtype=np.int64),
                np.asarray(post if post is not None else [-1] * nk, dtype=np.int64),
                np.asarray(pre_wait if pre_wait is not None else [0.] * nk, dtype=float),
                np.asarray(bounds, dtype=np.int64), np.asarray(offsets, dtype=float),
                np.asarray(trains, dtype=float),
                np.asarray(connections if connections is not None else [[-1.]], dtype=float),
                1., float(capacity), 100.)
        before = mass.copy()
        expected = simulate_direction(*args, True)
        actual = trace_direction(*args, station, True)
        for old, new in zip(expected, actual[:3]):
            np.testing.assert_allclose(old, new, rtol=1e-12, atol=1e-8)
        np.testing.assert_array_equal(mass, before)
        check_trace(actual[3], capacity)
        return actual

    def test_load_states_alighting_and_queue_only_after_arrival(self):
        # Eight arrive onboard; three leave. Only five B->C people can board.
        # Four remain; seven people arriving later must not count in this queue.
        _, _, _, trace = self.run_both([[3, 5, 9, 7]], [0, 0, 0, 20], [1, 2, 2, 2],
            [0, 2, 4, 4], [-1, 0, 1], [2, 21], 10)
        np.testing.assert_allclose(trace[0, 0], [8, 3, 5, 5, 10, 5, 4])
        np.testing.assert_allclose(trace[0, 1], [0, 0, 0, 10, 10, 10, 1])

    def test_tied_fractional_allocations_connections_and_unserved(self):
        self.run_both([[7.3, 5.1, 8.9, 4.2, 0.], [0., 11., 2., 20., 1.]],
            [0, 0, 0, 0, 99], [2, 1, 2, 2, 2], [0, 2, 5, 5], [-1, 0, 1], [1, 4], 10,
            groups=[0, 1, 2, 3, 4], post=[0, -1, 1, 0, -1], pre_wait=[1, 2, 3, 4, 0],
            connections=[[3, 10], [-1, -1]])

    def test_random_network_exact_equivalence(self):
        rng = np.random.default_rng(42)
        nstation = 7
        times, exits, bounds = [], [], [0]
        for station in range(nstation - 1):
            count = 25
            times.extend(sorted(rng.integers(0, 28, count)))
            exits.extend(rng.integers(station + 1, nstation, count))
            bounds.append(len(times))
        bounds.append(len(times))
        count = len(times)
        self.run_both(rng.gamma(1.5, 2., (8, count)), times, exits, bounds,
            np.arange(nstation) - 3., [0, 5, 9, 15, 20, 28, 35], 23., station=3,
            groups=rng.integers(0, 5, count), post=rng.choice([-1, 0, 1], count),
            pre_wait=rng.uniform(0, 6, count), connections=[[5, 11, 18, 29, 40], [7, 13, 25, -1, -1]])

    def test_instrumentation_no_demand(self):
        _, _, _, trace = self.run_both(np.zeros((3, 2)), [0, 0], [2, 2],
            [0, 1, 2, 2], [-1, 0, 1], [0, 5], 10.)
        np.testing.assert_allclose(trace[:, :, :5], 0.)
        np.testing.assert_allclose(trace[:, :, 5], 10.)

    def test_plan_restores_protected_services_and_removals(self):
        base = {0: [-4, 59, 62, 68, 360, 370], 1: [30, 63, 365]}
        rows = pd.DataFrame([
            dict(direction='往新店', old_beimen='18:02:00', new_beimen='18:03:00'),
            dict(direction='往新店', old_beimen='18:08:00', new_beimen=None),
            dict(direction='往新店', old_beimen=None, new_beimen='18:10:00'),
            dict(direction='往松山', old_beimen='18:03:00', new_beimen='18:03:00'),
        ])
        self.assertEqual(selected_plan(base, rows),
            {0: [-4, 59, 63., 70., 360, 370], 1: [30, 63., 365]})


if __name__ == '__main__':
    unittest.main()
