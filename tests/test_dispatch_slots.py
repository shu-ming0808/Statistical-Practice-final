import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from dispatch_slots import counts_by_slot


class DispatchSlotTests(unittest.TestCase):
    def build(self, times, baseline, optimized, added=0, removed=0, retimed=0):
        frame = pd.DataFrame([dict(event_date='2024-08-10', waiting_weight=1,
            direction='往松山', action=action, old_beimen=old, new_beimen=new)
            for action, old, new in times])
        comparisons = pd.DataFrame([dict(event_date='2024-08-10', waiting_weight=1,
            baseline_services=baseline, optimized_services=optimized,
            added=added, removed=removed, retimed=retimed)])
        return counts_by_slot(frame, comparisons)

    def test_retiming_across_slot_boundary_is_not_added_service(self):
        slots = self.build([('移時', '18:14:59', '18:15:00')], 1, 1, retimed=1)
        north = slots[slots.direction == '往松山'].set_index('slot_start')
        self.assertEqual(north.loc['18:00', 'net_change'], -1)
        self.assertEqual(north.loc['18:15', 'net_change'], 1)
        self.assertEqual(slots.net_change.sum(), 0)
        self.assertEqual(slots.added_services.sum(), 0)
        self.assertEqual(slots.removed_services.sum(), 0)
        self.assertEqual(slots.retimed_in.sum(), 1)
        self.assertEqual(slots.retimed_out.sum(), 1)
        self.assertEqual(len(slots), 40)

    def test_exact_end_is_excluded_and_empty_direction_kept(self):
        slots = self.build([('保留', '18:00:00', '18:00:00'),
                            ('保留', '22:59:59', '22:59:59'),
                            ('保留', '23:00:00', '23:00:00')], 2, 2)
        self.assertEqual(slots.baseline_count.sum(), 2)
        self.assertEqual(slots.optimized_count.sum(), 2)
        self.assertEqual(slots[slots.direction == '往新店'].baseline_count.sum(), 0)
        self.assertEqual(slots.slot_end.max(), '23:00')

    def test_add_remove_counts_distinct_from_net(self):
        slots = self.build([('減班', '18:01:00', ''), ('增班', '', '18:10:00')],
                           1, 1, added=1, removed=1)
        first = slots[(slots.direction == '往松山') & (slots.slot_start == '18:00')].iloc[0]
        self.assertEqual(first.net_change, 0)
        self.assertEqual(first.added_services, 1)
        self.assertEqual(first.removed_services, 1)

    def test_inconsistent_evaluated_totals_rejected(self):
        with self.assertRaisesRegex(ValueError, 'baseline_count differs'):
            self.build([('保留', '18:00', '18:00')], 2, 1)

    def test_duplicate_departure_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate departure'):
            self.build([('保留', '18:00', '18:00'), ('保留', '18:00', '18:00')], 2, 2)


if __name__ == '__main__':
    unittest.main()
