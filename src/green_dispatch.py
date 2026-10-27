"""Finite timetable neighbourhood search and capacity-conserving Green-line queues.

Passenger arrivals are minute-end cohorts. Within a tied arrival minute, scarce
seats are shared proportionally. Trains carry passengers to their Green-line
alighting station, so removing a train cannot create free seats downstream.
Only the Green line changes. R/O connecting departures remain fixed.
"""
from __future__ import annotations

import numpy as np
from numba import njit, prange, set_num_threads, config as numba_config
from scipy.optimize import linear_sum_assignment

set_num_threads(min(4, numba_config.NUMBA_NUM_THREADS))


@njit(cache=True)
def _next_departure(times, ready):
    for t in times:
        if t >= ready:
            return t
    return -1.0


@njit(cache=True, parallel=True)
def simulate_direction(mass, times, exits, groups, post_keys, pre_wait,
                       station_bounds, offsets, train_times, connections,
                       walking, capacity, horizon, detailed=False):
    """One direction, many scenarios. mass[scenario, cohort]; ordered stations.

Returns scenario x group (waiting, passengers, residual), wait histograms,
and maximum train loads. No waiting-time acceptance threshold is imposed.
Unserved passengers retain their waiting to the common end and are reported.
"""
    ns, nk = mass.shape
    nstations = len(station_bounds) - 1
    stats = np.zeros((ns, 5, 4))  # waiting, demand, unserved, Green waiting
    histograms = np.zeros((ns,5,601)) if detailed else np.zeros((ns,1,1))
    max_loads = np.zeros(ns)
    for s in prange(ns):
        # Store sparse input counts compactly, but do repeated fractional seat
        # allocation in float64 so rounding cannot accumulate past capacity.
        left = mass[s].astype(np.float64)
        ptr = station_bounds[:-1].copy()
        for k in range(nk):
            stats[s, groups[k], 1] += left[k]
        for anchor in train_times:
            onboard = np.zeros(nstations)
            load = 0.0
            for station in range(nstations):
                t = anchor + offsets[station]
                load -= onboard[station]
                seats = max(0.0, capacity - load)
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
                            histograms[s,groups[j], min(600, int(w))] += take
                        left[j] -= take
                        onboard[exits[j]] += take
                        load += take
                    seats -= min(seats, total)
                    if fraction >= 1.0:
                        k = z
                    else:
                        break
                ptr[station] = k
                max_loads[s] = max(max_loads[s], load)
        for k in range(nk):
            if left[k] > 1e-8:
                g = groups[k]
                stats[s, g, 0] += left[k] * (max(0.0, horizon-times[k]) + pre_wait[k])
                stats[s, g, 3] += left[k] * max(0.0, horizon-times[k])
                stats[s, g, 2] += left[k]
    hist=np.zeros((5,601))
    if detailed:
        for s in range(ns): hist+=histograms[s]
    return stats, hist, np.max(max_loads)


def compact_scenarios(packed, masses):
    """Drop cohorts empty in every scenario, without changing queues or routes."""
    compact, counts = [], []
    fields = ('times','exits','groups','post_keys','pre_wait')
    for p, mass in zip(packed, masses):
        indices = np.flatnonzero(np.any(mass > 0, axis=0))
        small = dict(p)
        for field in fields:
            small[field] = p[field][indices]
        small['station_bounds'] = np.searchsorted(indices, p['station_bounds'])
        compact.append(small)
        counts.append(np.ascontiguousarray(mass[:, indices]))
    return compact, counts


def validate_plan(base, plan, window, gap):
    """Protected boundary services are immutable; check both neighbours."""
    lo, hi = window
    for d in base:
        b, p = base[d], plan[d]
        if len(set(p)) != len(p) or sorted(p) != list(p):
            raise ValueError('Duplicate or unsorted departure')
        if [t for t in b if not lo <= t < hi] != [t for t in p if not lo <= t < hi]:
            raise ValueError('Protected departure outside decision window changed')
        # Official minute-rounded schedules may themselves violate an assumed gap.
        # Only unchanged original pairs are grandfathered and disclosed.
        original_pairs = set(zip(b, b[1:]))
        for a, z in zip(p, p[1:]):
            if z-a < gap-1e-8 and (a, z) not in original_pairs:
                raise ValueError('Changed pair violates minimum separation')


def differences(base, plan, window):
    lo, hi = window
    added = sum(len(set(plan[d])-set(base[d])) for d in base)
    removed = sum(len(set(base[d])-set(plan[d])) for d in base)
    return added, removed


def match_changes(base, plan, shift_limit=5):
    """Match final set differences to nearby moved originals, one-to-one.

This is an accounting convention; not identification of physical rolling stock.
    """
    rows = []
    for d in base:
        removed = set(base[d])-set(plan[d]); added = set(plan[d])-set(base[d])
        old, new = sorted(removed), sorted(added)
        if old and new:
            m,n=len(old),len(new)
            # Maximize valid matches first, then minimize total time displacement.
            penalty=(m+n+1)*(shift_limit+1)
            costs=np.full((m+n,m+n),float(penalty))
            costs[m:,n:]=0
            for i,b in enumerate(old):
                for j,a in enumerate(new):
                    costs[i,j]=abs(a-b) if abs(a-b)<=shift_limit else 4*penalty
            rr,cc=linear_sum_assignment(costs)
            for i,j in zip(rr,cc):
                if i<m and j<n and abs(old[i]-new[j])<=shift_limit:
                    rows.append((d,'移時',old[i],new[j]));removed.remove(old[i]);added.remove(new[j])
        rows += [(d, '減班', b, None) for b in sorted(removed)]
        rows += [(d, '增班', None, a) for a in sorted(added)]
        rows += [(d, '保留', a, a) for a in sorted(set(base[d]) & set(plan[d]))]
    return rows


def operating_change(base, plan, cfg):
    rows = match_changes(base, plan)
    added = sum(r[1] == '增班' for r in rows)
    removed = sum(r[1] == '減班' for r in rows)
    unit = cfg['service_km'] * cfg['variable_cost_per_train_km']
    # Removal saves service only; an added train also pays the assumed empty return.
    delta = unit * (added * (1 + cfg['extra_empty_return_multiplier']) - removed)
    return delta, added, removed, sum(r[1] == '移時' for r in rows)


def search_plans(base, evaluate, cfg, window):
    """Greedy add/remove/retime local search on proposal scenarios.

All retained proposals are later ranked on the full training scenarios; no
evaluation scenario is used for selection. Search is not globally optimal.
    """
    lo, hi = window
    cache = {}
    seen = {}
    def key(p): return tuple(tuple(p[d]) for d in sorted(p))
    def value(p, weight):
        k = key(p)
        if k not in cache:
            cache[k] = evaluate(p)
        delta, adds, _, _ = operating_change(base, p, cfg)
        if adds > cfg['maximum_added_services']:
            return float('inf')
        return delta + weight * cfg['time_value_per_minute'] * cache[k]
    starts=[{d:list(v) for d,v in base.items()}]
    for headway in cfg.get('initial_headways_minutes',[]):
        for phase in range(min(headway,3)):
            trial={}
            for d in base:
                protected=[t for t in base[d] if not lo<=t<hi]
                interior=[t for t in range(lo+phase,hi,headway)
                          if all(abs(t-b)>=cfg['minimum_headway_minutes'] for b in protected)]
                trial[d]=sorted(protected+interior)
            try: validate_plan(base,trial,window,cfg['minimum_headway_minutes'])
            except ValueError: continue
            starts.append(trial)
    for p in starts: seen[key(p)]={d:list(v) for d,v in p.items()}
    for weight in cfg['waiting_weights']:
        initial=min(starts,key=lambda p:value(p,weight))
        current = {d:list(v) for d,v in initial.items()}
        best = value(current, weight)
        for _ in range(cfg['search_passes']):
            changed = False
            # Coordinate sweeps permit removals and both earlier/later movements.
            for d in base:
                for t in list(current[d]):
                    if not lo <= t < hi or t not in current[d]: continue
                    without = [a for a in current[d] if a != t]
                    options = [None] + [t+v for v in cfg['shift_offsets_minutes'] if lo <= t+v < hi]
                    winner = None
                    for new in options:
                        trial = {**current, d:sorted(without + ([] if new is None else [new]))}
                        try: validate_plan(base, trial, window, cfg['minimum_headway_minutes'])
                        except ValueError: continue
                        score = value(trial, weight)
                        if score < best - 1e-6:
                            best, winner = score, trial
                    if winner is not None:
                        current = winner; changed = True
                        seen[key(current)] = {a:list(v) for a,v in current.items()}
            # Insert, then let the next sweep move or remove any train.
            for _ in range(cfg['maximum_added_services']):
                winner = None
                for d in base:
                    for t in range(lo, hi, cfg['proposal_add_step_minutes']):
                        trial = {**current, d:sorted(current[d]+[t])}
                        try: validate_plan(base, trial, window, cfg['minimum_headway_minutes'])
                        except ValueError: continue
                        score = value(trial, weight)
                        if score < best - 1e-6:
                            best, winner = score, trial
                if winner is None: break
                current = winner; changed = True
                seen[key(current)] = {a:list(v) for a,v in current.items()}
            if not changed: break
    return list(seen.values()), len(cache)
