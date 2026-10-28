"""Publish only the audited dispatch aggregates; does not read MySQL or OD."""
from pathlib import Path
from collections import Counter
import hashlib
import json
import math
import shutil

ROOT=Path(__file__).resolve().parents[1]


def validate_slot_counts(data):
    """Verify the public 15-minute grid against full old/new timetables.

    Bin deltas describe where departures moved, not actual added/removed
    services. In particular, a retiming across a boundary contributes -1/+1
    to the grid while the whole-window service count stays unchanged.
    """
    fields={'event_date','waiting_weight','direction','slot_start','slot_end',
            'baseline_count','optimized_count','net_change',
            'added_services','removed_services','retimed_in','retimed_out'}
    directions={'往松山','往新店'}
    comparison={(r['event_date'],r['waiting_weight']):r for r in data['comparison']}
    if len(comparison)!=len(data['comparison']):
        raise ValueError('Duplicate dispatch comparison')
    expected={(event,weight,d,start) for event,weight in comparison
              for d in directions for start in range(1080,1380,15)}
    counts={'old_beimen':Counter(),'new_beimen':Counter()}
    action_counts={field:Counter() for field in ['added_services','removed_services','retimed_in','retimed_out']}
    action_field={('增班','new_beimen'):'added_services',('減班','old_beimen'):'removed_services',
                  ('移時','new_beimen'):'retimed_in',('移時','old_beimen'):'retimed_out'}
    for row in data['timetable']:
        group=(row['event_date'],row['waiting_weight'])
        if group not in comparison or row['direction'] not in directions:
            raise ValueError('Unknown timetable comparison or direction')
        for field in counts:
            value=row[field]
            if not value: continue
            parts=value.split(':')
            if len(parts)!=3: raise ValueError('Expected HH:MM:SS departure')
            hour,minute,second=map(float,parts)
            if not (hour.is_integer() and minute.is_integer() and 0<=minute<60 and 0<=second<60):
                raise ValueError('Invalid departure clock')
            when=hour*60+minute+second/60
            if not 1080<=when<1380:
                raise ValueError('Heatmap only covers [18:00,23:00)')
            start=1080+int((when-1080)//15)*15
            key=(*group,row['direction'],start)
            counts[field][key]+=1
            target=action_field.get((row['action'],field))
            if target: action_counts[target][key]+=1
    seen=set();totals={group:[0,0,0] for group in comparison}
    for row in data['slot_counts']:
        if set(row)!=fields: raise ValueError('Unexpected slot fields; only aggregate counts may be published')
        def minute(value):
            if len(value)!=5 or value[2]!=':': raise ValueError('Expected HH:MM slot clock')
            h,m=map(int,value.split(':'))
            if not 0<=h<24 or not 0<=m<60: raise ValueError('Invalid slot clock')
            return h*60+m
        start,end=minute(row['slot_start']),minute(row['slot_end'])
        key=(row['event_date'],row['waiting_weight'],row['direction'],start)
        if key not in expected or key in seen or end!=start+15:
            raise ValueError('Incomplete, duplicate, or misplaced 15-minute slot')
        seen.add(key)
        old,new,delta=(row[c] for c in ['baseline_count','optimized_count','net_change'])
        if any(type(v) is not int for v in (old,new,delta)) or old<0 or new<0 or new-old!=delta:
            raise ValueError('Invalid slot counts or net change')
        if old!=counts['old_beimen'][key] or new!=counts['new_beimen'][key]:
            raise ValueError('Slot counts do not match the selected timetable')
        for field,actual in action_counts.items():
            value=row[field]
            if type(value) is not int or value<0 or value!=actual[key]:
                raise ValueError('Slot action counts do not match actual changes')
        if delta!=row['added_services']-row['removed_services']+row['retimed_in']-row['retimed_out']:
            raise ValueError('Slot net change does not conserve service movements')
        total=totals[key[:2]]
        for i,v in enumerate((old,new,delta)): total[i]+=v
    if seen!=expected: raise ValueError('Expected 20 slots in each of two directions per comparison')
    for group,(old,new,delta) in totals.items():
        row=comparison[group]
        if old!=row['baseline_services'] or new!=row['optimized_services'] or delta!=row['added']-row['removed']:
            raise ValueError('Slot totals disagree with actual service counts')


def validate_candidate_families(data):
    """Keep the same-draw candidate comparison tied to the published winner."""
    metrics={'added','removed','retimed','services','operating_change_twd','mean_wait',
             'wait_person_minutes','unserved_mean','max_train_load','min_changed_headway_minutes',
             'train_objective','evaluation_total_cost_change'}
    allowed={'event_date','waiting_weight','original_candidate','expanded_candidate',
             'original_family','expanded_family','selected_by_training','baseline_services','baseline_mean_wait',
             'original_headway_valid','expanded_headway_valid',
             'evaluation_total_cost_difference','evaluation_wait_difference'}
    allowed|={f'{prefix}_{name}' for prefix in ['original','expanded'] for name in metrics}
    comparisons={(r['event_date'],r['waiting_weight']):r for r in data['comparison']}
    seen=set()
    mapped={'services':'optimized_services','mean_wait':'optimized_mean_wait',
            'wait_person_minutes':'optimized_wait_person_minutes'}
    for row in data['candidate_family_comparison']:
        if set(row)!=allowed: raise ValueError('Unexpected candidate-family aggregate fields')
        key=(row['event_date'],row['waiting_weight'])
        if key not in comparisons or key in seen: raise ValueError('Duplicate or unknown candidate-family comparison')
        seen.add(key);chosen=comparisons[key]
        if row['selected_by_training'] is not True:
            raise ValueError('Candidates must be selected on training scenarios')
        if row['expanded_candidate']!=chosen['candidate'] or row['expanded_family']!=chosen['candidate_family']:
            raise ValueError('Expanded candidate is not the published winner')
        if chosen['headway_valid'] is not True:
            raise ValueError('Invalid chosen headway')
        if row['baseline_services']!=chosen['baseline_services'] or not math.isclose(row['baseline_mean_wait'],chosen['baseline_mean_wait'],rel_tol=1e-9,abs_tol=1e-6):
            raise ValueError('Candidate families do not use the same baseline')
        for prefix in ['original','expanded']:
            if row[f'{prefix}_headway_valid'] is not True:
                raise ValueError('Invalid candidate-family headway')
            for metric in metrics:
                if metric=='min_changed_headway_minutes' and row[f'{prefix}_{metric}'] is None:
                    if any(row[f'{prefix}_{action}'] for action in ['added','removed','retimed']):
                        raise ValueError('Changed timetable needs a measured headway')
                    continue
                if not math.isfinite(row[f'{prefix}_{metric}']):
                    raise ValueError('Nonfinite candidate-family metric')
            gap=row[f'{prefix}_min_changed_headway_minutes']
            if row[f'{prefix}_max_train_load']>2220+1e-6 or (gap is not None and gap<2-1e-8):
                raise ValueError('Candidate violates capacity or changed-service headway')
        for metric in metrics:
            actual,expected=row[f'expanded_{metric}'],chosen[mapped.get(metric,metric)]
            if actual is None and expected is None and metric=='min_changed_headway_minutes': continue
            if actual is None or expected is None or not math.isclose(actual,expected,rel_tol=1e-9,abs_tol=1e-6):
                raise ValueError(f'Expanded candidate metric differs: {metric}')
        for difference,field in [('evaluation_total_cost_difference','evaluation_total_cost_change'),
                                 ('evaluation_wait_difference','wait_person_minutes')]:
            expected=row[f'expanded_{field}']-row[f'original_{field}']
            if not math.isclose(row[difference],expected,rel_tol=1e-9,abs_tol=1e-6):
                raise ValueError('Invalid paired candidate-family difference')
        if row['expanded_train_objective']>row['original_train_objective']+max(1e-6,abs(row['original_train_objective'])*1e-10):
            raise ValueError('Expanded search lost the original training winner')
    if seen!=set(comparisons): raise ValueError('Incomplete candidate-family comparison')


def export(output=None):
    source=ROOT/'result/green_dispatch';dest=output or ROOT/'app/frontend/public/data'
    data=json.loads((source/'public_summary.json').read_text('utf-8'))
    if not data['no_wait_threshold'] or data['decision_window']!='18:00–23:00':
        raise ValueError('Unexpected dispatch contract')
    if len(data['comparison'])!=24: raise ValueError('Expected eight events and three weights')
    validate_slot_counts(data)
    validate_candidate_families(data)
    shutil.copyfile(source/'public_summary.json',dest/'dispatch.json')
    for name in ['comparison','station_impacts','timetable','summary','slot_counts','candidate_family_comparison']:
        shutil.copyfile(source/f'{name}.csv',dest/f'dispatch-{name}.csv')
    for name in ['cost_wait','station_impacts','timetable','slot_heatmap']:
        shutil.copyfile(source/f'figures/{name}.png',dest/f'dispatch-{name}.png')
    p=dest/'manifest.json';manifest=json.loads(p.read_text('utf-8'))
    # Existing forecast / observations timestamps are not falsely advanced.
    manifest['dispatch_generated_at']=data['generated_at']
    manifest['outputs']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in dest.iterdir() if p.is_file() and p.name!='manifest.json'}
    p.write_text(json.dumps(manifest,ensure_ascii=False,allow_nan=False,indent=2)+'\n',encoding='utf-8')
    print('Exported dispatch aggregates; existing observation snapshots unchanged.')


if __name__=='__main__':export()
