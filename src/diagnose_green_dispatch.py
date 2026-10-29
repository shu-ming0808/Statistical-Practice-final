"""Diagnose removal trade-offs without replacing the published dispatch policy.

Reuses verified prepared demand and the original 200/300 random streams. Fixed
8/10-minute patterns are diagnostic counterfactuals. Single cancellations are
screened on 12 training draws, shortlisted on 200, and only then evaluated on
300 independent draws. No evaluation score chooses an operational timetable.
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

from green_dispatch import compact_scenarios, minimum_changed_headway, operating_change, validate_plan
from prepare_green_dispatch import ROOT, GROUPS, digest, frozen_four, sample_mass
from run_green_dispatch import baseline, clock, evaluate

WINDOW = (60, 360)
DIRECTIONS = {0: '往新店', 1: '往松山'}
FIXED_HEADWAYS = (8, 10)
REMOVAL_SHORTLIST_PER_WEIGHT = 3


def fixed_headway_plan(base, headway, window=WINDOW, gap=2):
    """Same phase-zero pattern as the existing initial search; protect outside."""
    lo, hi = window
    if headway < gap:
        raise ValueError('Headway is below the research separation')
    plan = {}
    for direction, times in base.items():
        protected = [t for t in times if not lo <= t < hi]
        interior = [t for t in range(lo, hi, headway)
                    if all(abs(t-p) >= gap for p in protected)]
        plan[direction] = sorted(protected+interior)
    validate_plan(base, plan, window, gap)
    return plan


def single_removals(base, window=WINDOW, gap=2):
    lo, hi = window
    for direction in sorted(base):
        for departure in base[direction]:
            if lo <= departure < hi:
                plan = {d: list(v) for d, v in base.items()}
                plan[direction].remove(departure)
                validate_plan(base, plan, window, gap)
                yield direction, departure, plan


def stats_summary(stats, max_load):
    wait = stats[:, :, 0].sum(axis=1)
    demand = stats[:, :, 1].sum(axis=1)
    residual = stats[:, :, 2].sum(axis=1)
    return dict(wait_person_minutes=float(wait.mean()),
                mean_wait=float(wait.sum()/demand.sum()) if demand.sum() else None,
                passengers=float(demand.mean()), unserved_mean=float(residual.mean()),
                unserved_max=float(residual.max()), max_train_load=float(max_load),
                fully_served=bool(np.all(residual <= 1e-6)))


def select_training_removal(rows, weight, time_value):
    """Only full-training fields can influence selection; evaluation is ignored."""
    eligible = [r for r in rows if r['train_fully_served']]
    if not eligible:
        return None
    return min(eligible, key=lambda r: (
        r['operating_change_twd']+weight*time_value*r['train_delta_wait_person_minutes'],
        r['scenario_id']))


def break_even_weight(cost, additional_wait, time_value):
    """Positive-wait cancellation threshold; None means no finite positive cutoff."""
    return -cost/(time_value*additional_wait) if cost < 0 and additional_wait > 0 else None


def public_row(event, scenario_id, label, kind, weight, base, plan, summary, cfg):
    cost, adds, removes, moves = operating_change(base, plan, cfg)
    services = sum(sum(WINDOW[0] <= t < WINDOW[1] for t in ts) for ts in plan.values())
    return dict(event_date=event, scenario_id=scenario_id, label=label, kind=kind,
                waiting_weight=weight, services=services, added=adds, removed=removes,
                retimed=moves, operating_change_twd=cost,
                mean_wait=summary['mean_wait'], wait_person_minutes=summary['wait_person_minutes'],
                unserved_mean=summary['unserved_mean'], max_train_load=summary['max_train_load'],
                headway_valid=True, min_changed_headway_minutes=minimum_changed_headway(base, plan),
                evaluation_fully_served=summary['fully_served'], diagnostic_only=kind == 'reduction_probe')


def group_rows(event, scenario_id, stats, base_stats):
    rows = []
    for group, name in enumerate(GROUPS):
        n = stats[:, group, 1].sum()
        rows.append(dict(event_date=event, scenario_id=scenario_id, station=name,
            passengers=float(n/len(stats)),
            mean_wait=float(stats[:, group, 0].sum()/n) if n else None,
            baseline_mean_wait=float(base_stats[:, group, 0].sum()/n) if n else None,
            delta_wait_person_minutes=float((stats[:, group, 0]-base_stats[:, group, 0]).mean()),
            green_wait_person_minutes=float(stats[:, group, 3].mean()),
            unserved_mean=float(stats[:, group, 2].mean())))
    return rows


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def run(args):
    source = ROOT/'result/green_dispatch'
    out = ROOT/'result/green_dispatch_diagnostics'
    cache = ROOT/'.local/green_dispatch_diagnostics'
    out.mkdir(exist_ok=True, parents=True); cache.mkdir(exist_ok=True, parents=True)
    cfg = json.loads((ROOT/'configs/green_dispatch.json').read_text('utf-8'))
    main_audit = json.loads((source/'audit.json').read_text('utf-8'))
    if cfg != main_audit['config']:
        raise ValueError('Current configuration differs from the published experiment')
    # Validate actual source bytes; never silently accept changed private demand.
    for relative, expected in main_audit['input_hashes'].items():
        if digest(ROOT/relative) != expected:
            raise ValueError(f'Published input changed: {relative}')
    signature = hashlib.sha256(json.dumps(main_audit['input_hashes'], sort_keys=True).encode()).hexdigest()
    prep_fingerprint = digest(ROOT/'src/prepare_green_dispatch.py')+digest(ROOT/'configs/green_dispatch.json')+signature
    main_fingerprint = hashlib.sha256((digest(ROOT/'configs/green_dispatch.json')+
        digest(ROOT/'src/prepare_green_dispatch.py')+digest(ROOT/'src/green_dispatch.py')+
        digest(ROOT/'src/run_green_dispatch.py')+signature).encode()).hexdigest()
    published = pd.read_csv(source/'comparison.csv')
    training = pd.read_csv(source/'training_candidates.csv')
    station_source = pd.read_csv(source/'station_impacts.csv')
    diagnostic_fingerprint = hashlib.sha256((main_fingerprint+digest(Path(__file__))+
        digest(source/'comparison.csv')+digest(source/'training_candidates.csv')).encode()).hexdigest()
    tables = json.loads((ROOT/'.local/transit_reference/20261015/normalized.json').read_text('utf-8'))
    for snap in tables['metro_source_snapshot']:
        if digest(ROOT/'.local/transit_reference/20261015'/snap['local_file']) != snap['content_sha256']:
            raise ValueError('Official timetable source changed')
    forecasts = None
    collections = {name: [] for name in ['tradeoff_points','removal_proposals','removal_full_training',
        'selected_removals','station_impacts','fixed_training','cost_drivers']}
    events_audit = []
    events = sorted(published.event_date.unique())
    if args.event:
        events = [event for event in events if event == args.event]
        if not events:
            raise ValueError('Unknown event')
    for event in events:
        tick = perf_counter()
        event_source = next(e for e in main_audit['events'] if e['event_date'] == event)
        if event_source['fingerprint'] != main_fingerprint:
            raise ValueError('Published main simulation is stale')
        completed = cache/f'completed_{event}.pkl'
        previous = pickle.loads(completed.read_bytes()) if args.reuse and completed.exists() else None
        if previous and previous['fingerprint'] == diagnostic_fingerprint:
            print(event+': reuse verified diagnostic results', flush=True)
            for name in collections: collections[name] += previous['rows'][name]
            events_audit.append(previous['audit']); continue
        prepared = pickle.loads((ROOT/f'.local/green_dispatch/prepared_{event}.pkl').read_bytes())
        if prepared['fingerprint'] != prep_fingerprint:
            raise ValueError('Prepared demand cache does not match current verified inputs')
        packed, connections, prepared_audit = prepared['data']
        if forecasts is None: forecasts = frozen_four()
        base = baseline(tables, prepared_audit['day_type'], cfg['simulation_start_hour'])
        validate_plan(base, base, WINDOW, cfg['minimum_headway_minutes'])
        train_seed, eval_seed = np.random.SeedSequence([cfg['seed'],int(event.replace('-',''))]).spawn(2)
        masses, donors = sample_mass(packed, forecasts, event, np.random.default_rng(train_seed), cfg['training_draws'])
        proposal_packed, proposal_mass = compact_scenarios(packed, [m[:cfg['proposal_draws']] for m in masses])
        def run_summary(plan, proposal=False):
            s, _, load = evaluate(proposal_packed if proposal else packed, connections,
                proposal_mass if proposal else masses, plan, cfg, False)
            if load > cfg['capacity_persons']+1e-6: raise ValueError('Capacity overflow')
            return s, stats_summary(s, load)
        base_train_stats, base_train = run_summary(base)
        _, base_proposal = run_summary(base, True)
        all_rows = {name: [] for name in collections}
        fixed = {f'fixed_{h}': fixed_headway_plan(base, h, WINDOW, cfg['minimum_headway_minutes']) for h in FIXED_HEADWAYS}
        print(f'{event}: evaluate fixed 8/10-minute patterns on 200 training draws', flush=True)
        for sid, plan in fixed.items():
            _, s = run_summary(plan)
            cost, adds, removes, moves = operating_change(base, plan, cfg)
            all_rows['fixed_training'].append(dict(event_date=event, scenario_id=sid,
                operating_change_twd=cost, added=adds, removed=removes, retimed=moves,
                train_wait_person_minutes=s['wait_person_minutes'],
                train_delta_wait_person_minutes=s['wait_person_minutes']-base_train['wait_person_minutes'],
                train_unserved_mean=s['unserved_mean'], train_unserved_max=s['unserved_max'],
                train_fully_served=s['fully_served'], train_max_load=s['max_train_load']))
        plans = {}
        for direction, departure, plan in single_removals(base, WINDOW, cfg['minimum_headway_minutes']):
            sid = f'cancel_d{direction}_{departure:g}'
            plans[sid] = plan
            _, s = run_summary(plan, True)
            cost, adds, removes, moves = operating_change(base, plan, cfg)
            dw = s['wait_person_minutes']-base_proposal['wait_person_minutes']
            all_rows['removal_proposals'].append(dict(event_date=event, scenario_id=sid,
                direction=DIRECTIONS[direction], removed_beimen=clock(departure),
                operating_change_twd=cost, added=adds, removed=removes, retimed=moves,
                proposal_draws=cfg['proposal_draws'], train_wait_person_minutes=s['wait_person_minutes'],
                train_delta_wait_person_minutes=dw, train_unserved_mean=s['unserved_mean'],
                train_unserved_max=s['unserved_max'], train_fully_served=s['fully_served'],
                break_even_waiting_weight=break_even_weight(cost,dw,cfg['time_value_per_minute']),
                favorable_at_all_nonnegative_weights=bool(s['fully_served'] and cost<=0 and dw<=0)))
        eligible = [r for r in all_rows['removal_proposals'] if r['train_fully_served']]
        shortlist = set()
        for weight in cfg['waiting_weights']:
            ranked = sorted(eligible, key=lambda r:(r['operating_change_twd']+
                weight*cfg['time_value_per_minute']*r['train_delta_wait_person_minutes'],r['scenario_id']))
            shortlist.update(r['scenario_id'] for r in ranked[:REMOVAL_SHORTLIST_PER_WEIGHT])
        print(f'{event}: {len(plans)} single cancellations screened; {len(shortlist)} full-training candidates', flush=True)
        for sid in sorted(shortlist):
            _, s = run_summary(plans[sid])
            proposal_row = next(r for r in all_rows['removal_proposals'] if r['scenario_id']==sid)
            row = {k:v for k,v in proposal_row.items() if k not in ['proposal_draws','break_even_waiting_weight','favorable_at_all_nonnegative_weights']}
            row.update(training_draws=cfg['training_draws'],train_wait_person_minutes=s['wait_person_minutes'],
                train_delta_wait_person_minutes=s['wait_person_minutes']-base_train['wait_person_minutes'],
                train_unserved_mean=s['unserved_mean'],train_unserved_max=s['unserved_max'],train_fully_served=s['fully_served'])
            row['break_even_waiting_weight']=break_even_weight(row['operating_change_twd'],row['train_delta_wait_person_minutes'],cfg['time_value_per_minute'])
            all_rows['removal_full_training'].append(row)
        selected = {weight:select_training_removal(all_rows['removal_full_training'],weight,cfg['time_value_per_minute']) for weight in cfg['waiting_weights']}
        del run_summary, masses, proposal_mass, proposal_packed, base_train_stats
        gc.collect()
        evaluation, _ = sample_mass(packed, forecasts, event, np.random.default_rng(eval_seed), cfg['evaluation_draws'])
        base_eval_stats, _, base_load = evaluate(packed, connections, evaluation, base, cfg, False)
        base_eval = stats_summary(base_eval_stats, base_load)
        event_pub = published[published.event_date == event]
        anchor = event_pub.iloc[0]
        if not np.isclose(base_eval['wait_person_minutes'],anchor.baseline_wait_person_minutes,rtol=0,atol=1e-5):
            raise ValueError('Reconstructed baseline differs from published random scenarios')
        if not np.isclose(base_eval['passengers'],anchor.modeled_passengers,rtol=0,atol=1e-5):
            raise ValueError('Reconstructed demand differs from published random scenarios')
        all_rows['tradeoff_points'].append(public_row(event,'baseline','原班表','baseline',None,base,base,base_eval,cfg))
        all_rows['station_impacts'] += group_rows(event,'baseline',base_eval_stats,base_eval_stats)
        for r in event_pub.itertuples():
            all_rows['tradeoff_points'].append(dict(event_date=event,scenario_id=f'current_{r.waiting_weight:g}',
                label=f'現行候選 λ={r.waiting_weight:g}',kind='current',waiting_weight=r.waiting_weight,
                services=r.optimized_services,added=r.added,removed=r.removed,retimed=r.retimed,
                operating_change_twd=r.operating_change_twd,mean_wait=r.optimized_mean_wait,
                wait_person_minutes=r.optimized_wait_person_minutes,unserved_mean=r.unserved_mean,
                max_train_load=r.max_train_load,headway_valid=bool(r.headway_valid),
                min_changed_headway_minutes=r.min_changed_headway_minutes,
                evaluation_fully_served=bool(r.unserved_mean<=1e-6),diagnostic_only=False))
        eval_results = {}
        eval_plans = dict(fixed)
        eval_plans.update({r['scenario_id']:plans[r['scenario_id']] for r in selected.values() if r is not None})
        print(f'{event}: {len(eval_plans)} diagnostic plans on 300 independent evaluation draws', flush=True)
        for sid, plan in eval_plans.items():
            stats, _, load = evaluate(packed, connections, evaluation, plan, cfg, False)
            if load > cfg['capacity_persons']+1e-6: raise ValueError('Capacity overflow')
            s = stats_summary(stats,load); eval_results[sid] = s
            all_rows['station_impacts'] += group_rows(event,sid,stats,base_eval_stats)
            if sid in fixed:
                h = int(sid.split('_')[1])
                all_rows['tradeoff_points'].append(public_row(event,sid,f'固定 {h} 分鐘（診斷）','reduction_probe',None,base,plan,s,cfg))
        for weight, r in selected.items():
            if r is None: continue
            s = eval_results[r['scenario_id']]
            dw = s['wait_person_minutes']-base_eval['wait_person_minutes']
            all_rows['selected_removals'].append(dict(**r,waiting_weight=weight,selected_by_training=True,
                diagnostic_only=True,adopted=False,evaluation_draws=cfg['evaluation_draws'],
                mean_wait=s['mean_wait'],wait_person_minutes=s['wait_person_minutes'],
                delta_wait_person_minutes=dw,unserved_mean=s['unserved_mean'],unserved_max=s['unserved_max'],
                max_train_load=s['max_train_load'],evaluation_fully_served=s['fully_served'],headway_valid=True,
                train_delta_objective=r['operating_change_twd']+weight*cfg['time_value_per_minute']*r['train_delta_wait_person_minutes'],
                evaluation_delta_objective=r['operating_change_twd']+weight*cfg['time_value_per_minute']*dw,
                favorable_on_training=bool(r['operating_change_twd']+weight*cfg['time_value_per_minute']*r['train_delta_wait_person_minutes']<0),
                evaluation_break_even_waiting_weight=break_even_weight(r['operating_change_twd'],dw,cfg['time_value_per_minute'])))
        event_train = training[training.event_date == event]
        g = station_source[(station_source.event_date==event)&(station_source.waiting_weight==cfg['reference_weight'])]
        bg = float(g[g.station=='其他旅次'].passengers.sum())
        unit = cfg['service_km']*cfg['variable_cost_per_train_km']
        all_rows['cost_drivers'].append(dict(event_date=event,modeled_passengers=base_eval['passengers'],
            background_passengers=bg,background_fraction=bg/base_eval['passengers'],
            baseline_wait_person_minutes=base_eval['wait_person_minutes'],baseline_mean_wait=base_eval['mean_wait'],
            baseline_train_wait_person_minutes=base_train['wait_person_minutes'],
            single_cancel_saving_twd=unit,single_add_cost_twd=unit*(1+cfg['extra_empty_return_multiplier']),
            cancel_break_even_person_minutes_lambda1=unit/cfg['time_value_per_minute'],
            full_training_candidates=len(event_train),retained_candidates_with_removals=int((event_train.removed>0).sum()),
            cancellation_proposals=len(plans),proposal_fully_served=len(eligible),
            cancellation_full_training=len(shortlist),chosen_cancellation_count=len({r['scenario_id'] for r in selected.values() if r}),
            proposal_min_delta_wait_person_minutes=min(r['train_delta_wait_person_minutes'] for r in all_rows['removal_proposals'])))
        event_audit = dict(event_date=event,seconds=round(perf_counter()-tick,2),donor_events=donors,
            baseline_matches_published=True,source_fingerprint=main_fingerprint,
            fixed_patterns=list(fixed),single_cancel_proposals=len(plans),shortlisted_single_cancels=sorted(shortlist),
            training_selected_single_cancels={str(w):r['scenario_id'] if r else None for w,r in selected.items()},
            evaluation_used_for_selection=False)
        completed.write_bytes(pickle.dumps(dict(fingerprint=diagnostic_fingerprint,rows=all_rows,audit=event_audit)))
        for name in collections: collections[name] += all_rows[name]
        events_audit.append(event_audit)
        print(f'{event}: diagnostic complete ({event_audit["seconds"]} s)',flush=True)
        del evaluation,packed,base_eval_stats
        gc.collect()
    for name, rows in collections.items():
        pd.DataFrame(rows).to_csv(out/f'{name}.csv',index=False,encoding='utf-8-sig',lineterminator='\n')
    write_json(out/'audit.json',dict(generated_at=datetime.now(timezone.utc).isoformat(),
        source_fingerprint=main_fingerprint,diagnostic_fingerprint=diagnostic_fingerprint,
        input_hashes=main_audit['input_hashes'],configuration=cfg,
        fixed_headways=list(FIXED_HEADWAYS),single_cancel_shortlist_per_weight=REMOVAL_SHORTLIST_PER_WEIGHT,
        training_draws=cfg['training_draws'],proposal_draws=cfg['proposal_draws'],evaluation_draws=cfg['evaluation_draws'],
        same_random_streams_as_published=True,diagnostic_only=True,adopted=False,
        events=events_audit,method='Fixed patterns evaluated; single cancellation chosen on training only; published policy unchanged'))
    print(out,flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--event');parser.add_argument('--reuse',action='store_true')
    run(parser.parse_args())
