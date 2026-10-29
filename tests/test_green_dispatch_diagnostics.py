import sys
import unittest
from pathlib import Path
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from diagnose_green_dispatch import (fixed_headway_plan,single_removals,stats_summary,
    select_training_removal,break_even_weight)
from green_dispatch import operating_change


class DispatchDiagnosticTests(unittest.TestCase):
    def test_fixed_probe_preserves_boundaries_and_checks_neighbours(self):
        base={0:[-1,3,8,11]}
        plan=fixed_headway_plan(base,4,(0,10),2)
        self.assertEqual(plan,{0:[-1,4,8,11]})
        with self.assertRaises(ValueError):fixed_headway_plan(base,1,(0,10),2)

    def test_single_cancels_do_not_remove_protected_departures(self):
        base={0:[-2,3,8,12],1:[-2,2,6,12]}
        plans=list(single_removals(base,(0,10),2))
        self.assertEqual(len(plans),4)
        cfg={'service_km':10,'variable_cost_per_train_km':2,'extra_empty_return_multiplier':1}
        for _,_,p in plans:
            self.assertEqual(operating_change(base,p,cfg),(-20,0,1,0))
            self.assertEqual(p[0][0],-2);self.assertEqual(p[1][-1],12)

    def test_summary_weights_passengers_not_scenario_averages(self):
        s=np.zeros((2,5,4));s[0,0,:3]=[10,1,0];s[1,0,:3]=[20,10,2]
        r=stats_summary(s,100)
        self.assertAlmostEqual(r['mean_wait'],30/11)
        self.assertEqual(r['wait_person_minutes'],15)
        self.assertFalse(r['fully_served']);self.assertEqual(r['unserved_max'],2)

    def test_selection_cannot_peek_at_evaluation_and_rejects_unserved(self):
        a=dict(scenario_id='a',train_fully_served=True,operating_change_twd=-20,
            train_delta_wait_person_minutes=5,evaluation_delta_objective=1e6)
        b=dict(scenario_id='b',train_fully_served=True,operating_change_twd=-20,
            train_delta_wait_person_minutes=6,evaluation_delta_objective=-1e6)
        c=dict(scenario_id='c',train_fully_served=False,operating_change_twd=-100,
            train_delta_wait_person_minutes=0)
        self.assertEqual(select_training_removal([a,b,c],1,2)['scenario_id'],'a')
        self.assertIsNone(select_training_removal([c],1,2))

    def test_break_even_correct_units_and_no_finite_cutoff(self):
        self.assertEqual(break_even_weight(-20,10,2),1)
        self.assertIsNone(break_even_weight(-20,-10,2))
        self.assertIsNone(break_even_weight(20,10,2))


if __name__=='__main__':unittest.main()
