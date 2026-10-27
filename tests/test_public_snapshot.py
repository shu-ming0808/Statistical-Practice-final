"""Public build contract: no database, private inputs, or analysis dependencies."""
import csv
import hashlib
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'app/frontend/public/data'

class PublicSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.obs=json.loads((DATA/'observations.json').read_text(encoding='utf-8'))
        cls.research=json.loads((DATA/'research.json').read_text(encoding='utf-8'))

    def test_public_bundle_hashes(self):
        manifest=json.loads((DATA/'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual({p.name for p in DATA.iterdir()},set(manifest['outputs'])|{'manifest.json'})
        for name,digest in manifest['outputs'].items():
            self.assertEqual(hashlib.sha256((DATA/name).read_bytes()).hexdigest(),digest,name)

    def test_allowlisted_observation_schema_and_unique_rows(self):
        keys=set()
        for e in self.obs['events']:
            self.assertEqual(set(e),{'event_date','event_name','event_status','announced_show_start_time','fireworks_duration_seconds'})
        for r in self.obs['rows']:
            self.assertEqual(set(r),{'event_date','hour_start','station','origin_count','destination_count_same_source_bin','origin_complete','destination_complete','baseline_origin_mean_28d','baseline_n_days'})
            self.assertIn(r['station'],['北門','大橋頭站','雙連','民權西路'])
            key=(r['event_date'],r['station'],r['hour_start'])
            self.assertNotIn(key,keys);keys.add(key)

    def test_model_metric_matches_exported_forecasts(self):
        m=self.research['model'];h=m['hourly']
        self.assertEqual(len(h),54)
        self.assertAlmostEqual(sum(abs(r['actual']-r['predicted']) for r in h)/len(h),float(m['metrics']['mae']),places=6)
        self.assertEqual({r['hour'] for r in h},set(range(18,24)))

    def test_official_tables_and_results_match_public_sources(self):
        for key in self.research['operations']:
            with (ROOT/f'result/queue_dispatch_v1/metro_{key}.csv').open(encoding='utf-8-sig') as f:
                self.assertEqual(self.research['operations'][key],list(csv.DictReader(f)))
        with (ROOT/'result/queue_dispatch_v1/scenarios.csv').open(encoding='utf-8-sig') as f:
            self.assertEqual(self.research['queue']['scenarios'],list(csv.DictReader(f)))

    def test_csv_download_matches_four_station_observations(self):
        for e in self.obs['events']:
            with (DATA/f"flows-{e['event_date']}.csv").open(encoding='utf-8-sig') as f:
                rows=list(csv.DictReader(f))
            expected=[r for r in self.obs['rows'] if r['event_date']==e['event_date']]
            self.assertEqual(len(rows),len(expected))
            for actual,source in zip(rows,expected):
                self.assertEqual(actual['origin_count'],'' if source['origin_count'] is None else str(source['origin_count']))

    def test_dispatch_snapshot_matches_source_and_has_no_wait_gate(self):
        dispatch=json.loads((DATA/'dispatch.json').read_text('utf-8'))
        source=json.loads((ROOT/'result/green_dispatch/public_summary.json').read_text('utf-8'))
        self.assertEqual(dispatch,source)
        self.assertEqual(dispatch['decision_window'],'18:00–23:00')
        self.assertTrue(dispatch['no_wait_threshold'])
        self.assertTrue(dispatch['not_global_optimum'])
        self.assertEqual(len(dispatch['comparison']),24)
        self.assertEqual(len({r['event_date'] for r in dispatch['comparison']}),8)
        for r in dispatch['comparison']:
            self.assertEqual(r['baseline_services']+r['added']-r['removed'],r['optimized_services'])
            self.assertLessEqual(r['max_train_load'],2220+1e-6)
            self.assertAlmostEqual(r['optimized_operating_twd']-r['baseline_operating_twd'],r['operating_change_twd'],places=5)
        for r in dispatch['timetable']:
            for field in ['old_beimen','new_beimen']:
                if r[field]: self.assertTrue('18:00:00'<=r[field]<'23:00:00')
        for r in dispatch['comparison']:
            station=[s for s in dispatch['station_impacts'] if s['event_date']==r['event_date'] and s['waiting_weight']==r['waiting_weight']]
            self.assertEqual({s['station'] for s in station},{'北門','大橋頭站','雙連','民權西路','其他旅次'})

    def test_dispatch_choice_uses_training_scores(self):
        with (ROOT/'result/green_dispatch/training_candidates.csv').open(encoding='utf-8-sig') as f:
            candidates=list(csv.DictReader(f))
        data=json.loads((DATA/'dispatch.json').read_text('utf-8'))
        for r in data['comparison']:
            valid=[c for c in candidates if c['event_date']==r['event_date']]
            score=lambda c:float(c['operating_change_twd'])+r['waiting_weight']*(196/60)*float(c['train_wait_person_minutes'])
            picked=next(c for c in valid if int(c['candidate'])==r['candidate'])
            self.assertAlmostEqual(score(picked),min(map(score,valid)),places=4)

    def test_dispatch_past_only_donors_and_passenger_accounting(self):
        audit=json.loads((ROOT/'result/green_dispatch/audit.json').read_text('utf-8'))
        for event in audit['events']:
            self.assertTrue(event['baseline_dates'])
            self.assertTrue(event['donor_events'])
            self.assertTrue(all(d<event['event_date'] for d in event['baseline_dates']+event['donor_events']))
            self.assertEqual(event['training_draws'],200)
            self.assertEqual(event['evaluation_draws'],300)
        data=json.loads((DATA/'dispatch.json').read_text('utf-8'))
        for row in data['comparison']:
            groups=[s for s in data['station_impacts'] if s['event_date']==row['event_date'] and s['waiting_weight']==row['waiting_weight']]
            self.assertAlmostEqual(sum(s['passengers'] for s in groups),row['modeled_passengers'],places=5)
            for prefix in ['baseline','optimized']:
                wait=sum(s['passengers']*s[f'{prefix}_mean_wait'] for s in groups)
                self.assertAlmostEqual(wait,row[f'{prefix}_wait_person_minutes'],places=3)
                for s in groups:
                    self.assertGreaterEqual(s[f'{prefix}_green_wait'],0)
                    self.assertLessEqual(s[f'{prefix}_green_wait'],s[f'{prefix}_mean_wait']+1e-8)
            times=[t for t in data['timetable'] if t['event_date']==row['event_date'] and t['waiting_weight']==row['waiting_weight']]
            for direction in ['往松山','往新店']:
                values=sorted(int(t['new_beimen'][:2])*60+int(t['new_beimen'][3:5]) for t in times if t['direction']==direction and t['new_beimen'])
                self.assertTrue(all(b-a>=2 for a,b in zip(values,values[1:])))

if __name__=='__main__': unittest.main()
