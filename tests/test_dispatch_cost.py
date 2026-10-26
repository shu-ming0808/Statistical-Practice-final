import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from dispatch_optimization import DirectionQueue, optimize_insertions
from run_dispatch_cost import causal_ratio_pool, score_family, select_plan_index, queue_metrics, mileage_reference
from metro_operations import sha
from run_queue_uncertainty import donor_ratios
from queueing import simulate_bulk_queue


class CostDispatchTests(unittest.TestCase):
    def test_sourced_mileage_and_return_multiplier_checked(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            path = root/'distance.csv'
            path.write_text('route_name,service_km\nroute,18.77\n',encoding='utf-8')
            cfg = dict(route_mileage_csv='distance.csv',route_mileage_sha256=sha(path),
                       route_name='route',service_km=18.77,charged_route_multipliers=[1,2],
                       incremental_km_per_train_scenarios=[18.77,37.54])
            with patch('run_dispatch_cost.ROOT',root):
                self.assertEqual(mileage_reference(cfg)['service_km'],18.77)
                with self.assertRaises(ValueError):
                    mileage_reference(dict(cfg,incremental_km_per_train_scenarios=[18.77,30]))
                path.write_text('route_name,service_km\nroute,16.81\n',encoding='utf-8')
                with self.assertRaises(ValueError):
                    mileage_reference(cfg)

    def test_past_only_error_pool_and_no_self_donation(self):
        ratios = pd.DataFrame([[1]*6, [2]*6, [99]*6],
                              index=['2024-07-17', '2024-08-10', '2026-08-15'])
        pool = causal_ratio_pool(ratios, '2024-08-10')
        rng = np.random.default_rng(12)
        for _ in range(10):
            event, ratio = donor_ratios(pool, '2024-08-10', rng)
            self.assertEqual(event, '2024-07-17')
            np.testing.assert_array_equal(ratio, np.ones(6))
        self.assertEqual(len(causal_ratio_pool(ratios, '2024-07-17')), 1)

    def test_cost_can_prefer_no_train_or_one_train(self):
        metrics = [dict(wait_mean=1000., residual_mean=0.), dict(wait_mean=500., residual_mean=0.)]
        low = score_family(metrics, km=1, cost_per_km=600, time_value=1, weight=1, residual_penalty=60)
        high = score_family(metrics, km=1, cost_per_km=600, time_value=1, weight=2, residual_penalty=60)
        self.assertEqual(select_plan_index(low), 0)
        self.assertEqual(select_plan_index(high), 1)
        self.assertEqual(low[1]['objective_twd'], 1100)

    def test_training_choice_not_replaced_by_evaluation_winner(self):
        train = [dict(wait_mean=1000., residual_mean=0.), dict(wait_mean=500., residual_mean=0.)]
        evaluation = [dict(wait_mean=1000., residual_mean=0.), dict(wait_mean=990., residual_mean=0.)]
        score = lambda x: score_family(x, 1, 100, 1, 1, 60)
        selected = select_plan_index(score(train))
        self.assertEqual(selected, 1)
        self.assertGreater(score(evaluation)[selected]['objective_twd'], score(evaluation)[0]['objective_twd'])

    def test_common_horizon_includes_waiting_after_last_departure(self):
        # 10 arrivals in [0,1], 2 board at t=1; 8 then wait until t=5.
        q = DirectionQueue([[10, 0]], 2, 5, include_terminal_wait=True)
        wait, residual, _ = q.evaluate([1])
        self.assertAlmostEqual(wait[0], 5 + 8*4)
        self.assertEqual(residual[0], 8)
        ref = simulate_bulk_queue([10, 0, 0, 0, 0], [1], [2])
        self.assertAlmostEqual(wait[0], ref['waiting_person_minutes'])
        metrics = queue_metrics({'d': q}, {'d': [1]}, {'d': []})
        self.assertEqual(score_family([metrics], 1, 1, 1, 1, 60)[0]['objective_twd'], 37+480)

    def test_no_train_wait_area_not_zero(self):
        q = DirectionQueue([[10, 0]], 0, 5, include_terminal_wait=True)
        wait, residual, _ = q.evaluate([])
        self.assertEqual(wait[0], 45)
        self.assertEqual(residual[0], 10)

    def test_pre_window_train_blocks_insert_and_relocation(self):
        q = {'d': DirectionQueue([[100, 100, 100, 100]], 100, 8, include_terminal_wait=True)}
        plans = optimize_insertions(q, {'d': [4, 8]}, 2, 2, (0, 8), feasibility_trains={'d': [-1, 4, 8]})
        for plan in plans:
            times = sorted([-1, 4, 8] + plan['d'])
            self.assertTrue(all(b-a >= 2 for a, b in zip(times, times[1:])))
            self.assertNotIn(0, plan['d'])

    def test_ties_prefer_fewer_and_nonfinite_cost_rejected(self):
        scores = [dict(n_extra=0, objective_twd=1), dict(n_extra=1, objective_twd=1)]
        self.assertEqual(select_plan_index(scores), 0)
        with self.assertRaises(ValueError):
            score_family([], 1, float('nan'), 1, 1, 60)


if __name__ == '__main__':
    unittest.main()
