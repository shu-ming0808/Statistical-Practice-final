import sys
import unittest
from pathlib import Path
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from green_dispatch import simulate_direction, validate_plan, operating_change, search_plans, compact_scenarios


class GreenDispatchTests(unittest.TestCase):
    def simulate(self, mass, times, exits, groups, bounds, offsets, trains, cap,
                 post=None, connections=None):
        return simulate_direction(np.array([mass],dtype=np.float32),np.array(times,dtype=float),
            np.array(exits),np.array(groups),np.array(post if post is not None else [-1]*len(times)),
            np.zeros(len(times)),np.array(bounds),np.array(offsets,dtype=float),np.array(trains,dtype=float),
            np.array(connections if connections is not None else [[-1.]],dtype=float),0.,cap,100.,True)

    def test_onboard_passengers_use_seats_until_their_destination(self):
        # 8 board at A for C. B has only 2 seats, not a fresh capacity of 10.
        stats,_,load=self.simulate([8,5],[0,0],[2,2],[0,1],[0,1,2,2],[0,1,2],[1],10)
        self.assertAlmostEqual(load,10)
        self.assertEqual(stats[0,1,2],3)
        self.assertEqual(stats[0,:,1].sum(),13)

    def test_alighting_releases_capacity(self):
        stats,_,load=self.simulate([8,5],[0,0],[1,2],[0,1],[0,1,2,2],[0,1,2],[1],10)
        self.assertEqual(stats[0,:,2].sum(),0)
        self.assertEqual(load,8)

    def test_tied_arrivals_share_capacity_proportionally(self):
        stats,_,_=self.simulate([10,10],[0,0],[1,1],[0,1],[0,2,2],[0,1],[1],10)
        self.assertEqual(stats[0,0,2],5)
        self.assertEqual(stats[0,1,2],5)

    def test_fractional_cohort_capacity_with_float32_precision(self):
        n=1000
        stats,_,load=self.simulate([7.3]*n,[0]*n,[1]*n,[0]*n,[0,n,n],[0,1],[1],2220)
        self.assertAlmostEqual(load,2220,delta=1e-6)
        boarded=stats[0,0,1]-stats[0,0,2]
        self.assertAlmostEqual(boarded,2220,delta=1e-6)

    def test_empty_cohort_compaction_keeps_capacity_and_waits(self):
        p=dict(times=np.array([0.,0.,3.,0.,2.]),exits=np.array([2,1,2,2,2]),
               groups=np.array([0,0,0,1,1]),post_keys=np.array([-1]*5),pre_wait=np.zeros(5),
               station_bounds=np.array([0,3,5,5]),offsets=np.array([0.,1.,2.]))
        mass=np.array([[5,0,5,0,6],[0,0,5,0,6]],dtype=np.float32)
        cp,cm=compact_scenarios([p],[mass])
        self.assertEqual(cm[0].shape,(2,3))
        def run(q,m):
            return simulate_direction(m,q['times'],q['exits'],q['groups'],q['post_keys'],q['pre_wait'],q['station_bounds'],q['offsets'],np.array([1.,5.,9.]),np.array([[-1.]]),0.,10.,100.,True)
        before,after=run(p,mass),run(cp[0],cm[0])
        for a,b in zip(before,after):np.testing.assert_allclose(a,b,atol=1e-9)

    def test_removal_changes_other_station_waiting(self):
        args=([5,5],[0,0],[1,2],[0,1],[0,1,2,2],[0,1,2])
        base=self.simulate(*args,[1,5],10)[0]
        removed=self.simulate(*args,[5],10)[0]
        self.assertGreater(removed[0,1,0],base[0,1,0])

    def test_fixed_connecting_departure_can_be_missed(self):
        args=([10],[0],[1],[2],[0,1,1],[0,2])
        first=self.simulate(*args,[1],10,post=[0],connections=[[3,10]])[0]
        later=self.simulate(*args,[2],10,post=[0],connections=[[3,10]])[0]
        self.assertEqual(first[0,2,0],10)
        self.assertEqual(later[0,2,0],80)

    def test_no_boarding_before_arrival(self):
        stats,_,_=self.simulate([3],[2],[1],[0],[0,1,1],[0,1],[1,4],10)
        self.assertEqual(stats[0,0,0],6)

    def test_window_protection_and_neighbours(self):
        b={0:[-1,3,8,11]}
        with self.assertRaises(ValueError): validate_plan(b,{0:[3,8,11]},(0,10),2)
        with self.assertRaises(ValueError): validate_plan(b,{0:[-1,0,3,8,11]},(0,10),2)
        validate_plan(b,{0:[-1,4,11]},(0,10),2)

    def test_retime_cost_and_asymmetric_return_cost(self):
        cfg={'service_km':10,'variable_cost_per_train_km':2,'extra_empty_return_multiplier':1}
        base={0:[0,10,20]}
        self.assertEqual(operating_change(base,{0:[0,11,20]},cfg),(0,0,0,1))
        self.assertEqual(operating_change(base,{0:[0,20]},cfg),(-20,0,1,0))
        self.assertEqual(operating_change(base,{0:[0,10,20,30]},cfg),(40,1,0,0))
        # Greedy nearest-neighbour matching would misclassify this as an added train.
        self.assertEqual(operating_change({0:[0,5]},{0:[4,9]},cfg),(0,0,0,2))

    def test_search_really_can_remove_and_retime(self):
        cfg=dict(service_km=1,variable_cost_per_train_km=1,extra_empty_return_multiplier=0,
                 maximum_added_services=1,minimum_headway_minutes=2,waiting_weights=[1],
                 time_value_per_minute=1,search_passes=1,shift_offsets_minutes=[-1,1],proposal_add_step_minutes=2)
        base={0:[-2,3,8,12]}
        def waiting(p): return min(abs(t-4)*100 for t in p[0])
        plans,_=search_plans(base,waiting,cfg,(0,10))
        self.assertTrue(any(4 in p[0] for p in plans))
        self.assertTrue(any(len(p[0])<len(base[0]) for p in plans))
        for p in plans: validate_plan(base,p,(0,10),2)

if __name__=='__main__': unittest.main()
