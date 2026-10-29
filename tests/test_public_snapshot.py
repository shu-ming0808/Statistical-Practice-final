"""Public build contract: no database, private inputs, or analysis dependencies."""
import csv
import copy
import hashlib
import json
from pathlib import Path
import unittest
from app.export_dispatch import validate_slot_counts, validate_candidate_families
from app.export_dispatch_diagnostics import validate_points, validate_loads, validate_provenance, read_rows, POINT_FIELDS, LOAD_FIELDS

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
        validate_slot_counts(dispatch)
        validate_candidate_families(dispatch)
        for name in ['slot_counts','candidate_family_comparison']:
            self.assertEqual((DATA/f'dispatch-{name}.csv').read_bytes(),
                             (ROOT/f'result/green_dispatch/{name}.csv').read_bytes())
        self.assertEqual((DATA/'dispatch-slot_heatmap.png').read_bytes(),
                         (ROOT/'result/green_dispatch/figures/slot_heatmap.png').read_bytes())
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

class DispatchDiagnosticContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main=json.loads((DATA/'dispatch.json').read_text('utf-8'))
        cls.diagnostics=json.loads((DATA/'dispatch-diagnostics.json').read_text('utf-8'))
        cls.loads=json.loads((DATA/'dispatch-load-profile.json').read_text('utf-8'))
        cls.load_audit=json.loads((ROOT/'result/green_load_profile/audit.json').read_text('utf-8'))
        cls.diagnostic_audit=json.loads((ROOT/'result/green_dispatch_diagnostics/audit.json').read_text('utf-8'))
        cls.events={r['event_date'] for r in cls.main['comparison']}

    def test_diagnostics_are_complete_unadopted_and_match_csv(self):
        points=self.diagnostics['tradeoff_points']
        self.assertEqual(len(points),48)
        self.assertFalse(self.diagnostics['diagnostic_probes_adopted'])
        self.assertTrue(self.diagnostics['not_global_frontier'])
        self.assertEqual(points,read_rows(ROOT/'result/green_dispatch_diagnostics/tradeoff_points.csv',POINT_FIELDS))
        validate_points(points,self.main)
        for row in points: self.assertEqual(set(row),POINT_FIELDS)

    def test_missing_probe_mislabeled_adoption_and_changed_winner_rejected(self):
        for mutation in ('missing','kind','adopted','winner','headway'):
            with self.subTest(mutation=mutation):
                points=copy.deepcopy(self.diagnostics['tradeoff_points'])
                probe=next(r for r in points if r['kind']=='reduction_probe')
                if mutation=='missing': points.remove(probe)
                elif mutation=='kind': probe['kind']='baseline'
                elif mutation=='adopted': probe['diagnostic_only']=False
                elif mutation=='headway': probe['min_changed_headway_minutes']=1.9
                else: next(r for r in points if r['kind']=='current')['mean_wait']+=1
                with self.assertRaises(ValueError): validate_points(points,self.main)

    def test_loads_are_simulated_allowlisted_conserved_and_match_csv(self):
        self.assertTrue(self.loads['simulation_not_observed'])
        self.assertEqual(self.loads['evaluation_draws'],300)
        rows=self.loads['rows']
        self.assertEqual(len(rows),3336)
        self.assertEqual(rows,read_rows(ROOT/'result/green_load_profile/beimen_load_profile.csv',LOAD_FIELDS))
        validate_loads(rows,self.events,self.load_audit['expected_departures'])
        validate_provenance(self.load_audit,self.diagnostic_audit,rows,self.main)
        for row in rows: self.assertEqual(set(row),LOAD_FIELDS)
        with (ROOT/'result/green_load_profile/reconciliation.csv').open(encoding='utf-8-sig') as f:
            reconciliation=list(csv.DictReader(f))
        self.assertEqual(len(reconciliation),16)

    def test_missing_departure_or_stale_main_results_cannot_publish(self):
        with self.assertRaisesRegex(ValueError,'departure set'):
            validate_loads(self.loads['rows'][1:],self.events,self.load_audit['expected_departures'])
        for field in ('source_timetable_sha256','source_comparison_sha256','source_audit_sha256','profile_csv_sha256'):
            with self.subTest(field=field):
                audit=copy.deepcopy(self.load_audit);audit[field]='0'*64
                with self.assertRaises(ValueError):
                    validate_provenance(audit,self.diagnostic_audit,self.loads['rows'],self.main)

    def test_corrupted_capacity_clock_quantiles_and_duplicate_rejected(self):
        for mutation in ('capacity','clock','conservation','quantile','nonfinite','duplicate'):
            with self.subTest(mutation=mutation):
                rows=copy.deepcopy(self.loads['rows']);row=rows[0]
                if mutation=='capacity': row['capacity_persons']=3000
                elif mutation=='clock': row['departure_time']='25:00:00'
                elif mutation=='conservation': row['remaining_capacity_mean']-=10
                elif mutation=='quantile': row['remaining_capacity_p05']=2221
                elif mutation=='nonfinite': row['boarded_mean']=float('nan')
                else: rows.append(copy.deepcopy(row))
                with self.assertRaises(ValueError): validate_loads(rows,self.events)

    def test_published_downloads_match_the_research_artifacts(self):
        for published,source in [
            ('dispatch-tradeoff_points.csv','green_dispatch_diagnostics/tradeoff_points.csv'),
            ('dispatch-selected_removals.csv','green_dispatch_diagnostics/selected_removals.csv'),
            ('dispatch-beimen_load_profile.csv','green_load_profile/beimen_load_profile.csv'),
            ('dispatch-tradeoff.png','green_dispatch_diagnostics/figures/cost_wait_tradeoff.png'),
            ('dispatch-load-profile.png','green_dispatch_diagnostics/figures/beimen_remaining_capacity.png')]:
            self.assertEqual((DATA/published).read_bytes(),(ROOT/'result'/source).read_bytes())


class DispatchAggregateContractTests(unittest.TestCase):
    def retimed_fixture(self):
        event='2024-08-10';weight=1
        data={'comparison':[dict(event_date=event,waiting_weight=weight,
                                 baseline_services=1,optimized_services=1,added=0,removed=0,retimed=1)],
              'timetable':[dict(event_date=event,waiting_weight=weight,direction='往松山',
                                action='移時',old_beimen='18:14:00',new_beimen='18:16:00')],
              'slot_counts':[]}
        for direction in ['往松山','往新店']:
            for start in range(1080,1380,15):
                old=int(direction=='往松山' and start==1080)
                new=int(direction=='往松山' and start==1095)
                data['slot_counts'].append(dict(event_date=event,waiting_weight=weight,direction=direction,
                    slot_start=f'{start//60:02}:{start%60:02}',
                    slot_end=f'{(start+15)//60:02}:{(start+15)%60:02}',
                    baseline_count=old,optimized_count=new,net_change=new-old,
                    added_services=0,removed_services=0,retimed_in=new,retimed_out=old))
        return data

    def test_cross_slot_retiming_is_not_an_added_or_removed_service(self):
        data=self.retimed_fixture()
        validate_slot_counts(data)
        self.assertEqual(sum(max(r['net_change'],0) for r in data['slot_counts']),1)
        self.assertEqual(data['comparison'][0]['added'],0)
        self.assertEqual(sum(r['net_change'] for r in data['slot_counts']),0)

    def test_departure_exactly_on_slot_boundary_goes_to_following_slot(self):
        data=self.retimed_fixture()
        data['timetable'][0]['new_beimen']='18:15:00'
        validate_slot_counts(data)
        data['timetable'][0]['new_beimen']='18:14:59'
        with self.assertRaisesRegex(ValueError,'do not match'):
            validate_slot_counts(data)

    def test_decision_end_is_excluded_from_heatmap(self):
        data=self.retimed_fixture()
        data['timetable'][0]['new_beimen']='23:00:00'
        with self.assertRaisesRegex(ValueError,'only covers'):
            validate_slot_counts(data)

    def test_missing_duplicate_and_private_slots_are_rejected(self):
        for mutation in ['missing','duplicate','private']:
            with self.subTest(mutation=mutation):
                data=self.retimed_fixture()
                if mutation=='missing': data['slot_counts'].pop()
                elif mutation=='duplicate': data['slot_counts'].append(copy.deepcopy(data['slot_counts'][0]))
                else: data['slot_counts'][0]['raw_od']=[123]
                with self.assertRaises(ValueError): validate_slot_counts(data)

    def family_fixture(self):
        values={'added':0,'removed':0,'retimed':1,'services':1,'operating_change_twd':0,
                'mean_wait':2,'wait_person_minutes':200,'unserved_mean':0,'max_train_load':100,
                'min_changed_headway_minutes':2,'train_objective':100,'evaluation_total_cost_change':-100}
        row=dict(event_date='2024-08-10',waiting_weight=1,original_candidate=0,expanded_candidate=1,
                 original_family='original',expanded_family='slot',selected_by_training=True,
                 baseline_services=1,baseline_mean_wait=3,original_headway_valid=True,expanded_headway_valid=True,
                 evaluation_total_cost_difference=0,evaluation_wait_difference=0)
        for prefix in ['original','expanded']:
            row.update({f'{prefix}_{k}':v for k,v in values.items()})
        comparison=dict(event_date=row['event_date'],waiting_weight=1,candidate=1,candidate_family='slot',
                        headway_valid=True,baseline_services=1,baseline_mean_wait=3,**values)
        for old,new in [('services','optimized_services'),('mean_wait','optimized_mean_wait'),
                        ('wait_person_minutes','optimized_wait_person_minutes')]:
            comparison[new]=comparison.pop(old)
        return dict(comparison=[comparison],candidate_family_comparison=[row])

    def test_family_comparison_uses_published_training_winner(self):
        data=self.family_fixture()
        validate_candidate_families(data)
        data['candidate_family_comparison'][0]['selected_by_training']=False
        with self.assertRaisesRegex(ValueError,'training'): validate_candidate_families(data)

    def test_expanded_search_cannot_discard_better_original_training_solution(self):
        data=self.family_fixture()
        data['candidate_family_comparison'][0]['expanded_train_objective']=101
        data['comparison'][0]['train_objective']=101
        with self.assertRaisesRegex(ValueError,'lost the original'):
            validate_candidate_families(data)

    def test_family_capacity_headway_and_selected_metrics_are_checked(self):
        for key,value in [('expanded_max_train_load',2221),('original_min_changed_headway_minutes',1.9),
                          ('expanded_mean_wait',3),('raw_od',[123])]:
            with self.subTest(field=key):
                data=self.family_fixture();data['candidate_family_comparison'][0][key]=value
                with self.assertRaises(ValueError): validate_candidate_families(data)

    def test_no_changed_pair_has_null_not_invented_headway(self):
        data=self.family_fixture()
        row=data['candidate_family_comparison'][0]
        for prefix in ['original','expanded']:
            row[f'{prefix}_retimed']=0
            row[f'{prefix}_min_changed_headway_minutes']=None
        data['comparison'][0].update(retimed=0,min_changed_headway_minutes=None)
        validate_candidate_families(data)
        row['original_removed']=1
        with self.assertRaisesRegex(ValueError,'measured headway'):
            validate_candidate_families(data)


if __name__=='__main__': unittest.main()
