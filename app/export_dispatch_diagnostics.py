"""Publish aggregate cost/wait probes and OD load estimates, never raw OD."""
from pathlib import Path
import csv
import hashlib
import json
import math
import shutil

ROOT=Path(__file__).resolve().parents[1]
POINT_FIELDS={'event_date','scenario_id','label','kind','waiting_weight','services','added','removed','retimed','operating_change_twd','mean_wait','wait_person_minutes','unserved_mean','max_train_load','headway_valid','min_changed_headway_minutes','evaluation_fully_served','diagnostic_only'}
LOAD_STATES=('arrival_load','alighting','preboard_load','boarded','departure_load','remaining_capacity','queue_left')
LOAD_FIELDS={'event_date','plan','direction','departure_time','within_decision_window','capacity_persons','evaluation_draws'} | {f'{s}_{q}' for s in LOAD_STATES for q in ('mean','p05','p95')}
TEXT_FIELDS={'event_date','scenario_id','label','kind','plan','direction','departure_time'}
BOOL_FIELDS={'headway_valid','evaluation_fully_served','diagnostic_only','within_decision_window'}
SOURCES=[
 {'title':'Mo et al. (2020)：容量限制下的都市軌道網路載客模型','url':'https://journals.sagepub.com/doi/abs/10.1177/0361198120914309'},
 {'title':'Zhu et al. (2017)：以實際班表與 AFC 推估乘客搭車選擇','url':'https://onlinelibrary.wiley.com/doi/10.1155/2017/5824051'},
 {'title':'Su et al. (2022)：乘客與列車配對機率及網路人流分配','url':'https://www.mdpi.com/1099-4300/24/8/1026'},
 {'title':'臺北捷運 TIMES：載重感測與 AFC 校準（PDF 第 8、10–13、19 頁）','url':'https://www-ws.gov.taipei/001/Upload/799/relfile/10173/8554762/63923974-318f-4dd6-816a-b06607271d9b.pdf'}]

def read_rows(path,fields):
    with path.open(encoding='utf-8-sig',newline='') as stream:
        reader=csv.DictReader(stream)
        if not fields.issubset(reader.fieldnames or []): raise ValueError(f'Missing aggregate fields: {path.name}')
        result=[]
        for row in reader:
            item={}
            for key in sorted(fields):
                value=row[key]
                if key in TEXT_FIELDS: item[key]=value
                elif key in BOOL_FIELDS:
                    if value not in ('True','False'): raise ValueError(f'Invalid boolean: {key}')
                    item[key]=value=='True'
                elif value=='': item[key]=None
                else:
                    number=float(value)
                    if not math.isfinite(number): raise ValueError(f'Nonfinite value: {key}')
                    item[key]=int(number) if number.is_integer() else number
            result.append(item)
        return result

def validate_points(rows,main):
    comparisons={(r['event_date'],r['waiting_weight']):r for r in main['comparison']}
    events={key[0] for key in comparisons}; seen=set()
    expected={ (e,s) for e in events for s in ('baseline','current_0.5','current_1','current_2','fixed_8','fixed_10') }
    for r in rows:
        key=r['event_date'],r['scenario_id']
        if key not in expected or key in seen: raise ValueError('Unknown or duplicate tradeoff point')
        seen.add(key); base=comparisons[r['event_date'],1]
        scenario=r['scenario_id']
        expected_kind='baseline' if scenario=='baseline' else 'current' if scenario.startswith('current_') else 'reduction_probe'
        if r['kind']!=expected_kind: raise ValueError('Scenario kind mismatch')
        if scenario.startswith('current_') and r['waiting_weight']!=float(scenario.split('_')[1]): raise ValueError('Scenario weight mismatch')
        for field in ('services','added','removed','retimed'):
            if not isinstance(r[field],(int,float)) or not math.isfinite(r[field]) or r[field]<0 or int(r[field])!=r[field]: raise ValueError('Invalid service count')
        for field in ('operating_change_twd','mean_wait','wait_person_minutes','unserved_mean','max_train_load'):
            if not isinstance(r[field],(int,float)) or not math.isfinite(r[field]): raise ValueError('Nonfinite tradeoff value')
        if base['baseline_services']+r['added']-r['removed']!=r['services']: raise ValueError('Service count mismatch')
        if not r['headway_valid'] or r['max_train_load']>2220+1e-6 or r['mean_wait']<0 or r['unserved_mean']<0: raise ValueError('Invalid diagnostic physics')
        gap=r['min_changed_headway_minutes']
        if gap is not None and (not math.isfinite(gap) or gap<2-1e-6): raise ValueError('Invalid changed headway')
        if r['evaluation_fully_served']!=(r['unserved_mean']<=1e-6): raise ValueError('Invalid completion flag')
        if r['kind']=='current':
            selected=comparisons.get((r['event_date'],r['waiting_weight']))
            if selected is None or r['diagnostic_only']: raise ValueError('Wrong current weight')
            pairs=[('operating_change_twd','operating_change_twd'),('mean_wait','optimized_mean_wait'),('services','optimized_services')]
            if any(not math.isclose(r[a],selected[b],rel_tol=1e-10,abs_tol=1e-6) for a,b in pairs): raise ValueError('Current result changed')
        elif r['kind']=='baseline':
            if r['operating_change_twd']!=0 or r['waiting_weight'] is not None or r['diagnostic_only'] or not math.isclose(r['mean_wait'],base['baseline_mean_wait'],abs_tol=1e-6): raise ValueError('Wrong baseline')
        elif r['kind']=='reduction_probe':
            if not r['diagnostic_only'] or r['waiting_weight'] is not None or r['removed']<=0: raise ValueError('Probe wrongly presented as adopted')
        else: raise ValueError('Unknown diagnostic kind')
    if seen!=expected: raise ValueError('Incomplete eight-event tradeoff data')

def validate_loads(rows,events,expected_departures=None):
    seen=set(); groups=set()
    for r in rows:
        key=tuple(r[k] for k in ('event_date','plan','direction','departure_time'))
        if key in seen or r['event_date'] not in events or r['plan'] not in ('baseline','selected_lambda1') or r['direction'] not in ('往松山','往新店'): raise ValueError('Unknown or duplicate load row')
        seen.add(key); groups.add(key[:3]); cap=r['capacity_persons']
        if cap!=2220 or r['evaluation_draws']!=300: raise ValueError('Wrong capacity or draw count')
        h,m,s=map(float,r['departure_time'].split(':')); minute=h*60+m+s/60
        if not (h.is_integer() and m.is_integer() and 0<=h<24 and 0<=m<60 and 0<=s<60) or r['within_decision_window']!=(1080<=minute<1380): raise ValueError('Invalid load departure clock')
        for state in LOAD_STATES:
            if any(not isinstance(r[f'{state}_{q}'],(int,float)) or not math.isfinite(r[f'{state}_{q}']) for q in ('mean','p05','p95')): raise ValueError('Nonfinite load value')
            if any(r[f'{state}_{q}']< -1e-6 for q in ('mean','p05','p95')) or r[f'{state}_p05']>r[f'{state}_p95']+1e-6: raise ValueError('Invalid load quantiles')
            if state!='queue_left' and any(r[f'{state}_{q}']>cap+1e-6 for q in ('mean','p05','p95')): raise ValueError('Load exceeds capacity')
        for actual,expected in [(r['arrival_load_mean']-r['alighting_mean'],r['preboard_load_mean']),
                                (r['preboard_load_mean']+r['boarded_mean'],r['departure_load_mean']),
                                (cap-r['preboard_load_mean'],r['remaining_capacity_mean'])]:
            if not math.isclose(actual,expected,abs_tol=1e-6): raise ValueError('Mean load conservation failed')
    if groups!={(e,p,d) for e in events for p in ('baseline','selected_lambda1') for d in ('往松山','往新店')}: raise ValueError('Incomplete load groups')
    if expected_departures is not None:
        expected=set(); expected_groups=set()
        for item in expected_departures:
            group=tuple(item[k] for k in ('event_date','plan','direction'))
            if group in expected_groups: raise ValueError('Duplicate expected departure group')
            expected_groups.add(group)
            for clock in item['departure_times']:
                key=group+(clock,)
                if key in expected: raise ValueError('Duplicate expected departure')
                expected.add(key)
        if expected_groups!=groups or expected!=seen: raise ValueError('Incomplete or changed departure set')


def validate_provenance(load_audit,diagnostic_audit,rows,main,root=ROOT):
    source=root/'result/green_dispatch'
    for label,filename in [('audit','audit.json'),('comparison','comparison.csv'),('timetable','timetable.csv')]:
        if hashlib.sha256((source/filename).read_bytes()).hexdigest()!=load_audit[f'source_{label}_sha256']:
            raise ValueError('Stale load source: '+filename)
    if hashlib.sha256((root/'result/green_load_profile/beimen_load_profile.csv').read_bytes()).hexdigest()!=load_audit['profile_csv_sha256']:
        raise ValueError('Load CSV hash mismatch')
    source_audit=json.loads((source/'audit.json').read_text('utf-8'))
    fingerprints={r['fingerprint'] for r in source_audit['events']}
    if fingerprints!={load_audit['source_run_fingerprint']} or fingerprints!={diagnostic_audit['source_fingerprint']}:
        raise ValueError('Mixed diagnostic run fingerprints')
    if diagnostic_audit['configuration']!=source_audit['config']: raise ValueError('Mixed diagnostic configuration')
    events={r['event_date'] for r in main['comparison']}
    validate_loads(rows,events,load_audit['expected_departures'])
    # Match the loaded decision-window trains against the published selected
    # timetable, not only against the trace script's own expected list.
    for event in events:
        for direction in ('往松山','往新店'):
            published=[r for r in main['timetable'] if r['event_date']==event and r['waiting_weight']==1 and r['direction']==direction]
            for plan,field in [('baseline','old_beimen'),('selected_lambda1','new_beimen')]:
                clocks={r['departure_time'] for r in rows if r['event_date']==event and r['direction']==direction and r['plan']==plan and r['within_decision_window']}
                if clocks!={r[field] for r in published if r[field]}: raise ValueError('Load departures differ from published timetable')

def write_json(path,data):
    path.write_text(json.dumps(data,ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n',encoding='utf-8',newline='\n')

def export(output=None):
    dest=output or ROOT/'app/frontend/public/data'
    diagnostic=ROOT/'result/green_dispatch_diagnostics'; loads=ROOT/'result/green_load_profile'
    main=json.loads((ROOT/'result/green_dispatch/public_summary.json').read_text('utf-8'))
    points=read_rows(diagnostic/'tradeoff_points.csv',POINT_FIELDS); validate_points(points,main)
    profiles=read_rows(loads/'beimen_load_profile.csv',LOAD_FIELDS)
    validate_loads(profiles,{r['event_date'] for r in points})
    audit=json.loads((diagnostic/'audit.json').read_text('utf-8'))
    load_audit=json.loads((loads/'audit.json').read_text('utf-8'))
    validate_provenance(load_audit,audit,profiles,main)
    write_json(dest/'dispatch-diagnostics.json',dict(schema_version=1,generated_at=audit['generated_at'],tradeoff_points=points,diagnostic_probes_adopted=False,not_global_frontier=True))
    write_json(dest/'dispatch-load-profile.json',dict(schema_version=1,generated_at=load_audit['generated_at'],evaluation_draws=300,simulation_not_observed=True,rows=profiles,sources=SOURCES))
    for name,path in [('dispatch-tradeoff_points.csv',diagnostic/'tradeoff_points.csv'),('dispatch-selected_removals.csv',diagnostic/'selected_removals.csv'),('dispatch-beimen_load_profile.csv',loads/'beimen_load_profile.csv'),('dispatch-tradeoff.png',diagnostic/'figures/cost_wait_tradeoff.png'),('dispatch-load-profile.png',diagnostic/'figures/beimen_remaining_capacity.png')]:
        shutil.copyfile(path,dest/name)
    manifest_path=dest/'manifest.json'; manifest=json.loads(manifest_path.read_text('utf-8'))
    manifest['dispatch_diagnostics_generated_at']=audit['generated_at']
    manifest['outputs']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in dest.iterdir() if p.is_file() and p.name!='manifest.json'}
    manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    print('Exported cost/wait diagnostics and simulated OD loads; main decisions unchanged.')

if __name__=='__main__': export()
