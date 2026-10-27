"""18:00–23:00 Green-line add/remove/retime experiment and public report.

Run from the repository root. Inputs stay private; only aggregate comparisons,
candidate timetables, assumptions and input hashes are exported.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gc
import hashlib
import json
from pathlib import Path
import pickle
from time import perf_counter

import numpy as np
import pandas as pd

from green_dispatch import simulate_direction, search_plans, validate_plan, operating_change, match_changes, compact_scenarios
from prepare_green_dispatch import ROOT, FOUR, GROUPS, Network, frozen_four, prepare_event, sample_mass, digest

DIRECTIONS=['往新店','往松山']


def write_json(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def baseline(tables,day,start_hour):
    result={}
    for table in tables['metro_timetable']:
        if table['station_label']!='北門': continue
        direction=1 if '松山' in table['direction_text'] else 0
        values=[]
        for r in tables['metro_departure']:
            if r['source_id']!=table['source_id'] or r['day_type']!=day: continue
            # An early protected boundary is included for network warmup.
            if r['departure_minute']<16*60: continue
            if r['terminal']!=('松山' if direction else '新店'):
                raise ValueError('Evening short turn requires explicit route model')
            values.append(r['departure_minute']-start_hour*60)
        result[direction]=sorted(values)
    return result


def evaluate(packed,connections,masses,plan,cfg,detailed=False):
    stats=np.zeros((len(masses[0]),5,4));hist=np.zeros((5,601));maxload=0.
    for d,p in enumerate(packed):
        s,h,m=simulate_direction(masses[d],p['times'],p['exits'],p['groups'],p['post_keys'],
            p['pre_wait'],p['station_bounds'],p['offsets'],np.asarray(plan[d],dtype=float),
            connections,float(cfg['transfer_walk_minutes']),float(cfg['capacity_persons']),
            float((cfg['simulation_end_hour']-cfg['simulation_start_hour'])*60),detailed)
        stats+=s;hist+=h;maxload=max(maxload,m)
    return stats,hist,maxload


def percentile(hist,q=.95):
    if hist.sum()<=0: return None
    return int(np.searchsorted(np.cumsum(hist),q*hist.sum()))+.5


def clock(t,start=17):
    if t is None: return ''
    minute=t+60*start
    return f'{int(minute//60)%24:02}:{int(minute%60):02}:{round((minute%1)*60):02}'


def run(args):
    cfg_path=ROOT/'configs/green_dispatch.json'
    cfg=json.loads(cfg_path.read_text('utf-8'))
    out=ROOT/cfg['output'];out.mkdir(parents=True,exist_ok=True)
    cache=ROOT/'.local/green_dispatch';cache.mkdir(parents=True,exist_ok=True)
    tables=json.loads((ROOT/'.local/transit_reference/20261015/normalized.json').read_text('utf-8'))
    for r in tables['metro_source_snapshot']:
        p=ROOT/'.local/transit_reference/20261015'/r['local_file']
        if digest(p)!=r['content_sha256']: raise ValueError('Official reference snapshot changed')
    forecasts=frozen_four();network=Network(tables,cfg)
    input_paths=[ROOT/'configs/forecast.json', ROOT/'.local/forecast/analysis_input.csv',
        ROOT/'.local/forecast/predictions.csv', ROOT/'.local/transit_reference/20261015/normalized.json',
        ROOT/'data/interim/events_snapshot.json', ROOT/'data/processed/station_hourly.parquet']
    input_paths+=sorted((ROOT/'.local/forecast/models').glob('*_xgboost_weighted.joblib'))
    input_paths+=sorted((ROOT/'data/interim/clean_od').glob('*.parquet'))
    input_hashes={str(p.relative_to(ROOT)).replace('\\','/'):digest(p) for p in input_paths}
    input_signature=hashlib.sha256(json.dumps(input_hashes,sort_keys=True).encode()).hexdigest()
    events=sorted(forecasts.event_date.dt.strftime('%Y-%m-%d').unique())[1:]
    if args.event: events=[e for e in events if e==args.event]
    fingerprint=hashlib.sha256((digest(cfg_path)+digest(ROOT/'src/prepare_green_dispatch.py')+
                               digest(ROOT/'src/green_dispatch.py')+digest(ROOT/'src/run_green_dispatch.py')+input_signature).encode()).hexdigest()
    all_rows=[];station_rows=[];timetable=[];audits=[];tradeoffs=[]
    window=(60,360)  # 18:00 inclusive, 23:00 exclusive, relative to 17:00.
    for event in events:
        tic=perf_counter();saved=cache/f'completed_{event}.pkl'
        if args.reuse and saved.exists():
            value=pickle.loads(saved.read_bytes())
            if value['fingerprint']==fingerprint:
                print(f'{event}: reuse checked results',flush=True)
                all_rows+=value['rows'];station_rows+=value['stations'];timetable+=value['timetable']
                audits.append(value['audit']);tradeoffs+=value['tradeoffs'];continue
        print(f'{event}: preparing past-only OD and frozen forecasts',flush=True)
        prepared=cache/f'prepared_{event}.pkl'
        prep_fingerprint=digest(ROOT/'src/prepare_green_dispatch.py')+digest(cfg_path)+input_signature
        prep=pickle.loads(prepared.read_bytes()) if prepared.exists() else None
        if prep and prep['fingerprint']==prep_fingerprint:
            packed,connections,audit=prep['data']
        else:
            packed,connections,audit=prepare_event(event,forecasts,network,cfg)
            prepared.write_bytes(pickle.dumps(dict(fingerprint=prep_fingerprint,data=(packed,connections,audit))))
        base=baseline(tables,audit['day_type'],cfg['simulation_start_hour'])
        validate_plan(base,base,window,cfg['minimum_headway_minutes'])
        # Stable event-specific streams; train/evaluation use independent child streams.
        seed=np.random.SeedSequence([cfg['seed'],int(event.replace('-',''))])
        train_seed,test_seed=seed.spawn(2)
        masses,donors=sample_mass(packed,forecasts,event,np.random.default_rng(train_seed),cfg['training_draws'])
        proposal_packed,proposal=compact_scenarios(packed,[m[:cfg['proposal_draws']] for m in masses])
        def scoring(plan, ms=None):
            pk,counts=(proposal_packed,proposal) if ms is None else (packed,ms)
            s,_,_=evaluate(pk,connections,counts,plan,cfg)
            if np.any(s[:,:,2].sum(axis=1)>1e-6): return float('inf')
            return float(s[:,:,0].sum(axis=1).mean())
        print(f'{event}: {audit["cohorts"]} route/minute cohorts; searching add/remove/retime',flush=True)
        candidates,attempts=search_plans(base,scoring,cfg,window)
        proposal_count=len(candidates)
        # Screening uses only the proposal subset of training draws. Preserve
        # the baseline and the ten best per preference for full-training ranking.
        proposal_wait=[scoring(p) for p in candidates]
        proposal_cost=[operating_change(base,p,cfg)[0] for p in candidates]
        short={0}
        for weight in cfg['waiting_weights']:
            order=sorted(range(len(candidates)),key=lambda i:proposal_cost[i]+weight*cfg['time_value_per_minute']*proposal_wait[i])
            short.update(order[:cfg['shortlist_per_weight']])
        candidates=[candidates[i] for i in sorted(short)]
        print(f'{event}: rank {len(candidates)} retained plans on {cfg["training_draws"]} training draws',flush=True)
        ranked=[]
        for i,p in enumerate(candidates):
            wait=scoring(p,masses)
            cost,adds,removes,moves=operating_change(base,p,cfg)
            ranked.append(dict(event_date=event,candidate=i,train_wait_person_minutes=wait,
                               operating_change_twd=cost,added=adds,removed=removes,retimed=moves))
        winners={}
        for weight in cfg['waiting_weights']:
            finite=[r for r in ranked if np.isfinite(r['train_wait_person_minutes'])]
            if not finite: raise ValueError(f'{event}: no fully served training candidate')
            best=min(finite,key=lambda r:(r['operating_change_twd']+weight*cfg['time_value_per_minute']*r['train_wait_person_minutes'],r['added']+r['removed']+r['retimed']))
            winners[weight]=best['candidate']
        # Free large training matrices before generating independent evaluation data.
        # Release both the full training arrays and their compact proposal copies.
        del scoring,masses,proposal,proposal_packed;gc.collect()
        evaluation,_=sample_mass(packed,forecasts,event,np.random.default_rng(test_seed),cfg['evaluation_draws'])
        base_s,base_h,_=evaluate(packed,connections,evaluation,base,cfg,True)
        base_w=base_s[:,:,0].sum(axis=1);demand=base_s[:,:,1].sum(axis=1)
        base_count=sum(sum(window[0]<=t<window[1] for t in v) for v in base.values())
        base_op=base_count*cfg['service_km']*cfg['variable_cost_per_train_km']
        rows=[];stations=[];times=[]
        for weight,i in winners.items():
            plan=candidates[i];validate_plan(base,plan,window,cfg['minimum_headway_minutes'])
            s,h,maxload=evaluate(packed,connections,evaluation,plan,cfg,True)
            cost,adds,removes,moves=operating_change(base,plan,cfg)
            w=s[:,:,0].sum(axis=1);paired=cost+weight*cfg['time_value_per_minute']*(w-base_w)
            row=dict(event_date=event,waiting_weight=weight,candidate=i,baseline_services=base_count,
                optimized_services=base_count+adds-removes,added=adds,removed=removes,retimed=moves,
                baseline_operating_twd=base_op,optimized_operating_twd=base_op+cost,operating_change_twd=cost,
                baseline_wait_person_minutes=float(base_w.mean()),optimized_wait_person_minutes=float(w.mean()),
                wait_change_percent=float((w.mean()/base_w.mean()-1)*100),
                modeled_passengers=float(demand.mean()),baseline_mean_wait=float(base_w.sum()/demand.sum()),
                optimized_mean_wait=float(w.sum()/demand.sum()),baseline_p95=percentile(base_h.sum(axis=0)),
                optimized_p95=percentile(h.sum(axis=0)),unserved_mean=float(s[:,:,2].sum(axis=1).mean()),
                baseline_unserved_mean=float(base_s[:,:,2].sum(axis=1).mean()),max_train_load=float(maxload),
                evaluation_total_cost_change=float(paired.mean()),
                paired_mc_se=float(paired.std(ddof=1)/np.sqrt(len(paired))),
                train_objective=float(ranked[i]['operating_change_twd']+weight*cfg['time_value_per_minute']*ranked[i]['train_wait_person_minutes']))
            if row['unserved_mean']>1e-6: row['optimized_p95']=None
            if row['baseline_unserved_mean']>1e-6: row['baseline_p95']=None
            rows.append(row)
            for g,name in enumerate(GROUPS):
                n=base_s[:,g,1].sum()
                stations.append(dict(event_date=event,waiting_weight=weight,station=name,
                    passengers=float(n/len(base_s)),baseline_mean_wait=float(base_s[:,g,0].sum()/n) if n else None,
                    optimized_mean_wait=float(s[:,g,0].sum()/n) if n else None,
                    baseline_green_wait=float(base_s[:,g,3].sum()/n) if n else None,
                    optimized_green_wait=float(s[:,g,3].sum()/n) if n else None,
                    baseline_p95=percentile(base_h[g]),optimized_p95=percentile(h[g]),
                    unserved_mean=float(s[:,g,2].mean()),
                    unaffected_bypass_passengers=audit['bypass_passengers'][g]))
            for d,action,old,new in match_changes(base,plan):
                if action=='保留' and not window[0]<=old<window[1]: continue
                p=packed[d]
                times.append(dict(event_date=event,waiting_weight=weight,direction=DIRECTIONS[d],action=action,
                    old_beimen=clock(old),new_beimen=clock(new),origin='松山' if d==0 else '新店',
                    old_origin=clock(None if old is None else old+p['offsets'][0]),
                    new_origin=clock(None if new is None else new+p['offsets'][0]),
                    new_terminal=clock(None if new is None else new+p['offsets'][-1])))
            print(f'{event} lambda={weight}: +{adds}/-{removes}/move {moves}; cost {cost:,.0f}; wait {row["wait_change_percent"]:+.1f}%',flush=True)
        audit.update(event_date=event,donor_events=donors,candidates_evaluated=attempts,
            proposal_plans=proposal_count,retained_candidates=len(candidates),
            training_draws=cfg['training_draws'],evaluation_draws=cfg['evaluation_draws'],
            chosen_from_training=True,seconds=round(perf_counter()-tic,2),fingerprint=fingerprint)
        value=dict(fingerprint=fingerprint,rows=rows,stations=stations,timetable=times,audit=audit,tradeoffs=ranked)
        saved.write_bytes(pickle.dumps(value))
        all_rows+=rows;station_rows+=stations;timetable+=times;audits.append(audit);tradeoffs+=ranked
        del evaluation,packed;gc.collect()
    for name,rows in [('comparison',all_rows),('station_impacts',station_rows),('timetable',timetable),('training_candidates',tradeoffs)]:
        pd.DataFrame(rows).to_csv(out/f'{name}.csv',index=False,encoding='utf-8-sig')
    write_json(out/'audit.json',dict(generated_at=datetime.now(timezone.utc).isoformat(),config=cfg,events=audits,
        input_hashes=input_hashes,
        no_wait_threshold=True,not_global_optimum=True,release_times_verified=False,not_operational_dispatch=True))
    from report_green_dispatch import report
    report(out,cfg,update_readme=args.update_readme)
    print(str(out),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--event');parser.add_argument('--reuse',action='store_true')
    parser.add_argument('--update-readme',action='store_true')
    run(parser.parse_args())
