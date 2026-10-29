"""Read-only instrumentation of the published Green-line evaluation scenarios.

This does not select a timetable or change demand assumptions. It replays the
verified prepared cache and the published lambda=1 plan with the original 300
evaluation draws, and reports simulated loads at Beimen. Scenario quantiles
are neither observations nor confidence intervals for real train occupancy.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import pickle
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd
from numba import njit, prange

from green_dispatch import _next_departure, simulate_direction, validate_plan
from prepare_green_dispatch import ROOT, digest, frozen_four, sample_mass
from run_green_dispatch import DIRECTIONS, baseline, clock


TRACE_FIELDS = (
    'arrival_load', 'alighting', 'preboard_load', 'boarded',
    'departure_load', 'remaining_capacity', 'queue_left',
)


@njit(cache=True, parallel=True)
def trace_direction(mass, times, exits, groups, post_keys, pre_wait,
                    station_bounds, offsets, train_times, connections,
                    walking, capacity, horizon, trace_station, detailed=False):
    """Instrumented copy of simulate_direction; first three outputs are identical.

The separate kernel deliberately leaves the production/cache fingerprints
untouched. Tests compare every original output on fractional, tied, random,
and missed-connection cases. The batch exporter also reconciles published waits.
Trace dimensions: scenario x train x TRACE_FIELDS. Queue counts include only
passengers who have arrived by this departure and remain after boarding.
"""
    ns, nk = mass.shape
    nstations = len(station_bounds) - 1
    stats = np.zeros((ns, 5, 4))
    histograms = np.zeros((ns, 5, 601)) if detailed else np.zeros((ns, 1, 1))
    max_loads = np.zeros(ns)
    trace = np.zeros((ns, len(train_times), len(TRACE_FIELDS)))
    for s in prange(ns):
        left = mass[s].astype(np.float64)
        ptr = station_bounds[:-1].copy()
        for k in range(nk):
            stats[s, groups[k], 1] += left[k]
        for train_index in range(len(train_times)):
            anchor = train_times[train_index]
            onboard = np.zeros(nstations)
            load = 0.0
            for station in range(nstations):
                t = anchor + offsets[station]
                if station == trace_station:
                    trace[s, train_index, 0] = load
                    trace[s, train_index, 1] = onboard[station]
                load -= onboard[station]
                seats = max(0.0, capacity - load)
                if station == trace_station:
                    trace[s, train_index, 2] = load
                    trace[s, train_index, 5] = seats
                k = ptr[station]
                end = station_bounds[station + 1]
                while k < end and times[k] <= t and seats > 1e-9:
                    z = k + 1
                    while z < end and times[z] == times[k]:
                        z += 1
                    total = np.sum(left[k:z])
                    fraction = min(1.0, seats / total) if total > 1e-9 else 1.0
                    for j in range(k, z):
                        take = left[j] * fraction
                        if take <= 1e-9:
                            continue
                        green_wait = t - times[j]
                        w = green_wait + pre_wait[j]
                        key = post_keys[j]
                        if key >= 0:
                            ready = anchor + offsets[exits[j]] + walking
                            next_time = _next_departure(connections[key], ready)
                            if next_time < 0:
                                stats[s, groups[j], 2] += take
                                w += max(0.0, horizon - ready)
                            else:
                                w += next_time - ready
                        stats[s, groups[j], 0] += take * w
                        stats[s, groups[j], 3] += take * green_wait
                        if detailed:
                            histograms[s, groups[j], min(600, int(w))] += take
                        left[j] -= take
                        onboard[exits[j]] += take
                        load += take
                        if station == trace_station:
                            trace[s, train_index, 3] += take
                    seats -= min(seats, total)
                    if fraction >= 1.0:
                        k = z
                    else:
                        break
                ptr[station] = k
                max_loads[s] = max(max_loads[s], load)
                if station == trace_station:
                    trace[s, train_index, 4] = load
                    j = k
                    while j < end and times[j] <= t:
                        trace[s, train_index, 6] += left[j]
                        j += 1
        for k in range(nk):
            if left[k] > 1e-8:
                g = groups[k]
                stats[s, g, 0] += left[k] * (max(0.0, horizon - times[k]) + pre_wait[k])
                stats[s, g, 3] += left[k] * max(0.0, horizon - times[k])
                stats[s, g, 2] += left[k]
    hist = np.zeros((5, 601))
    if detailed:
        for s in range(ns):
            hist += histograms[s]
    return stats, hist, np.max(max_loads), trace


def selected_plan(base, rows, start_hour=17):
    """Rebuild the public selected plan while retaining protected boundary trains."""
    window = (60., 360.)
    result = {}
    for d, name in enumerate(DIRECTIONS):
        protected = [t for t in base[d] if not window[0] <= t < window[1]]
        depart = rows.loc[(rows.direction == name) & rows.new_beimen.notna(), 'new_beimen']
        inside = []
        for text in depart:
            hh, mm, ss = map(float, text.split(':'))
            minute = hh * 60 + mm + ss / 60 - start_hour * 60
            if not window[0] <= minute < window[1]:
                raise ValueError('Published changed departure outside protected window')
            inside.append(minute)
        result[d] = sorted(protected + inside)
    return result


def check_trace(trace, capacity):
    """Per-draw conservation checks, before reducing to marginal quantiles."""
    np.testing.assert_allclose(trace[:, :, 0] - trace[:, :, 1], trace[:, :, 2], atol=1e-7)
    np.testing.assert_allclose(trace[:, :, 2] + trace[:, :, 3], trace[:, :, 4], atol=1e-7)
    np.testing.assert_allclose(capacity - trace[:, :, 2], trace[:, :, 5], atol=1e-7)
    if np.min(trace) < -1e-6 or np.max(trace[:, :, 4]) > capacity + 1e-6:
        raise ValueError('Load trace violates nonnegative demand or train capacity')


def summarize_trace(event, name, direction, trains, trace, cfg):
    means = trace.mean(axis=0)
    quantiles = np.quantile(trace, [.05, .95], axis=0)
    rows = []
    for i, minute in enumerate(trains):
        row = dict(event_date=event, plan=name, direction=DIRECTIONS[direction],
                   departure_time=clock(minute, cfg['simulation_start_hour']),
                   departure_minute_from_17=float(minute),
                   within_decision_window=bool(60 <= minute < 360),
                   capacity_persons=cfg['capacity_persons'], evaluation_draws=trace.shape[0])
        for j, field in enumerate(TRACE_FIELDS):
            row[field + '_mean'] = float(means[i, j])
            row[field + '_p05'] = float(quantiles[0, i, j])
            row[field + '_p95'] = float(quantiles[1, i, j])
        rows.append(row)
    return rows


def run(event_filter=None):
    source = ROOT / 'result/green_dispatch'
    out = ROOT / 'result/green_load_profile'
    cfg_path = ROOT / 'configs/green_dispatch.json'
    cfg = json.loads(cfg_path.read_text('utf-8'))
    audit = json.loads((source / 'audit.json').read_text('utf-8'))
    if cfg != audit['config']:
        raise ValueError('Current configuration differs from the published run')
    print('Verify the published input hashes before replay', flush=True)
    for name, expected in audit['input_hashes'].items():
        if digest(ROOT / name) != expected:
            raise ValueError('Published input changed: ' + name)
    input_signature = hashlib.sha256(json.dumps(audit['input_hashes'], sort_keys=True).encode()).hexdigest()
    prep_fingerprint = digest(ROOT / 'src/prepare_green_dispatch.py') + digest(cfg_path) + input_signature
    run_fingerprint = hashlib.sha256((digest(cfg_path) + digest(ROOT / 'src/prepare_green_dispatch.py') +
        digest(ROOT / 'src/green_dispatch.py') + digest(ROOT / 'src/run_green_dispatch.py') + input_signature).encode()).hexdigest()
    tables = json.loads((ROOT / '.local/transit_reference/20261015/normalized.json').read_text('utf-8'))
    for item in tables['metro_source_snapshot']:
        if digest(ROOT / '.local/transit_reference/20261015' / item['local_file']) != item['content_sha256']:
            raise ValueError('Official source snapshot changed')
    comparison = pd.read_csv(source / 'comparison.csv')
    timetable = pd.read_csv(source / 'timetable.csv')
    forecasts = frozen_four()
    rows, reconciliation, expected_departures = [], [], []
    events = [a for a in audit['events'] if not event_filter or a['event_date'] == event_filter]
    if not events:
        raise ValueError('No matching published event')
    for event_audit in events:
        event = event_audit['event_date']
        tic = perf_counter()
        if event_audit['fingerprint'] != run_fingerprint:
            raise ValueError('Published event fingerprint does not match the current source: ' + event)
        prep_path = ROOT / '.local/green_dispatch' / f'prepared_{event}.pkl'
        prep = pickle.loads(prep_path.read_bytes())
        if prep['fingerprint'] != prep_fingerprint:
            raise ValueError('Prepared cache fingerprint changed: ' + event)
        packed, connections, prepared_audit = prep['data']
        base = baseline(tables, prepared_audit['day_type'], cfg['simulation_start_hour'])
        public_rows = timetable[(timetable.event_date == event) & (timetable.waiting_weight == 1)]
        plan = selected_plan(base, public_rows, cfg['simulation_start_hour'])
        validate_plan(base, plan, (60, 360), cfg['minimum_headway_minutes'])
        seed = np.random.SeedSequence([cfg['seed'], int(event.replace('-', ''))])
        _, evaluation_seed = seed.spawn(2)
        masses, _ = sample_mass(packed, forecasts, event, np.random.default_rng(evaluation_seed), cfg['evaluation_draws'])
        reference = comparison[(comparison.event_date == event) & (comparison.waiting_weight == 1)].iloc[0]
        print(f'{event}: replay baseline and selected lambda=1 on {cfg["evaluation_draws"]} evaluation draws', flush=True)
        for plan_name, services, prefix in [('baseline', base, 'baseline'), ('selected_lambda1', plan, 'optimized')]:
            waiting = np.zeros(cfg['evaluation_draws'])
            max_load = 0.
            for d, p in enumerate(packed):
                # Expected clocks come from the complete reconstructed plans,
                # independently of the emitted CSV rows. Protected services are
                # included so publication can reject even a single missing train.
                expected_departures.append(dict(event_date=event, plan=plan_name,
                    direction=DIRECTIONS[d], departure_times=[
                        clock(t, cfg['simulation_start_hour']) for t in services[d]]))
                station = np.flatnonzero(np.isclose(p['offsets'], 0.))
                if len(station) != 1:
                    raise ValueError('Beimen must have exactly one zero route offset')
                args = (masses[d], p['times'], p['exits'], p['groups'], p['post_keys'], p['pre_wait'],
                        p['station_bounds'], p['offsets'], np.asarray(services[d], dtype=float), connections,
                        float(cfg['transfer_walk_minutes']), float(cfg['capacity_persons']),
                        float((cfg['simulation_end_hour'] - cfg['simulation_start_hour']) * 60))
                stats, _, observed_max, trace = trace_direction(*args, int(station[0]), False)
                check_trace(trace, cfg['capacity_persons'])
                # A small live equivalence check guards against later divergence
                # of this instrumentation from the production simulation kernel.
                check_args = (masses[d][:3],) + args[1:]
                original = simulate_direction(*check_args, False)
                np.testing.assert_allclose(stats[:3], original[0], rtol=1e-12, atol=1e-7)
                waiting += stats[:, :, 0].sum(axis=1)
                max_load = max(max_load, observed_max)
                rows.extend(summarize_trace(event, plan_name, d, services[d], trace, cfg))
            expected = float(reference[prefix + '_wait_person_minutes'])
            np.testing.assert_allclose(waiting.mean(), expected, rtol=1e-11, atol=1e-5)
            reconciliation.append(dict(event_date=event, plan=plan_name,
                wait_person_minutes=float(waiting.mean()), published_wait_person_minutes=expected,
                absolute_difference=float(abs(waiting.mean() - expected)), max_train_load=float(max_load),
                prepared_cache_sha256=digest(prep_path), all_load_conservation_checks_passed=True))
        del masses, packed, prep, trace
        gc.collect()
        print(f'{event}: all loads conserve passengers and published waits match ({perf_counter()-tic:.1f}s)', flush=True)
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out / 'beimen_load_profile.csv', index=False, encoding='utf-8-sig')
    pd.DataFrame(reconciliation).to_csv(out / 'reconciliation.csv', index=False, encoding='utf-8-sig')
    metadata = dict(generated_at=datetime.now(timezone.utc).isoformat(),
        simulation_not_observed=True, quantiles='5th and 95th simulation-scenario quantiles, not confidence intervals',
        reference_weight=1, evaluation_draws=cfg['evaluation_draws'], seed=cfg['seed'],
        source_run_fingerprint=run_fingerprint, source_audit_sha256=digest(source / 'audit.json'),
        source_comparison_sha256=digest(source / 'comparison.csv'),
        source_timetable_sha256=digest(source / 'timetable.csv'),
        profile_csv_sha256=digest(out / 'beimen_load_profile.csv'),
        expected_departures=expected_departures,
        script_sha256=digest(Path(__file__)), events=[a['event_date'] for a in events],
        protected_services_preserved=True, production_model_unchanged=True,
        load_states=list(TRACE_FIELDS),
        limitations=['Hourly entry-timed OD does not identify actual train choices or minute arrivals.',
            'Shortest-time routes and walking times are assumptions, not observed trajectories.',
            'Historical OD background demand is not a measured event-day train occupancy.',
            'Trains start empty at their terminus; no pre-17:00 passenger cohort is modelled.',
            'Current reference timetable is used retrospectively; event-day extra trains are unknown.'])
    (out / 'audit.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (out / 'README.md').write_text(
        '# 北門逐班列車載量：OD 模擬讀值\n\n'
        f'沿用已發布的 {len(events)} 場綠線調度模型、原班表與 λ＝1 方案，以及原本獨立的 {cfg["evaluation_draws"]} 組評估情境。'
        '只增加載量讀值，不重新選方案、不改變人流與路徑假設。\n\n'
        '- `beimen_load_profile.csv`：每場、每套班表、每個方向、每班北門離站時刻的平均與 P05／P95 載量。\n'
        '- `reconciliation.csv`：重播的總候車人分鐘與已發布結果逐場核對。\n'
        '- `audit.json`：輸入、程式與載量 CSV 雜湊；逐場／方案／方向的完整預期班次集合；相同亂數種子與限制。\n\n'
        '到站車上人數 `arrival_load` 減北門下車人數 `alighting`，得到下客後／上客前 '
        '`preboard_load`；可上車空間 `remaining_capacity = 2220 - preboard_load`。'
        '再加北門上車人數 `boarded`，得到離站載量 `departure_load`；`queue_left` 為此班開走後已到月台但尚未上車者。\n\n'
        '**這些是 OD、路徑與班表假設下的模擬值，並非列車實測。P05–P95 是模擬情境分位數，不是實際載量的信賴區間。**'
        '各欄分位數分別計算，不能直接把分位數相加驗證守恆；守恆已在每個原始情境逐班檢查。'
        '受保護的 18:00 前、23:00 後列車仍參與完整重播；展示決策時段時請使用 `within_decision_window`。\n\n'
        '重跑：`python -X utf8 src/green_load_trace.py`。需要本機私有快照；不會重新產生 OD 或覆寫原調度輸出。\n',
        encoding='utf-8')
    print(out, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--event', help='Diagnostic one-event export; omit for all published events')
    run(parser.parse_args().event)
