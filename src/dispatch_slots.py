"""Audited 15-minute counts for selected Green-line timetables.

These bins describe departures, not physical rolling stock or dispatch actions.
Costs continue to use the engine's one-to-one add/remove/retime matching.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DIRECTIONS = ('往松山', '往新店')
START, END, SLOT = 18 * 60, 23 * 60, 15


def minute(value):
    if pd.isna(value) or value == '':
        return None
    parts = str(value).split(':')
    if len(parts) not in (2, 3):
        raise ValueError(f'Invalid departure time: {value}')
    hour, mins = int(parts[0]), int(parts[1])
    seconds = float(parts[2]) if len(parts) == 3 else 0
    if not (0 <= hour < 24 and 0 <= mins < 60 and 0 <= seconds < 60):
        raise ValueError(f'Invalid departure time: {value}')
    return hour * 60 + mins + seconds / 60


def counts_by_slot(timetable, comparison):
    """Build complete half-open bins, checking totals against evaluated plans."""
    keys = ['event_date', 'waiting_weight']
    if comparison.duplicated(keys).any():
        raise ValueError('Duplicate evaluated plan')
    if not set(timetable.direction).issubset(DIRECTIONS):
        raise ValueError('Unknown Green-line direction')
    if set(map(tuple, timetable[keys].to_numpy())) != set(map(tuple, comparison[keys].to_numpy())):
        raise ValueError('Timetable and comparison cohorts differ')
    output = []
    for row in comparison.itertuples():
        group = timetable.loc[(timetable.event_date == row.event_date) &
                              (timetable.waiting_weight == row.waiting_weight)]
        for direction in DIRECTIONS:
            g = group.loc[group.direction == direction].copy()
            old = g.old_beimen.map(minute)
            new = g.new_beimen.map(minute)
            if old.dropna().duplicated().any() or new.dropna().duplicated().any():
                raise ValueError('Duplicate departure within a direction')
            for start in range(START, END, SLOT):
                old_here = old.ge(start) & old.lt(start + SLOT)
                new_here = new.ge(start) & new.lt(start + SLOT)
                before, after = int(old_here.sum()), int(new_here.sum())
                output.append(dict(event_date=row.event_date, waiting_weight=row.waiting_weight,
                    direction=direction, slot_start=f'{start // 60:02}:{start % 60:02}',
                    slot_end=f'{(start + SLOT) // 60:02}:{(start + SLOT) % 60:02}',
                    baseline_count=before, optimized_count=after, net_change=after - before,
                    added_services=int((new_here & g.action.eq('增班')).sum()),
                    removed_services=int((old_here & g.action.eq('減班')).sum()),
                    retimed_in=int((new_here & g.action.eq('移時')).sum()),
                    retimed_out=int((old_here & g.action.eq('移時')).sum())))
        current = output[-40:]
        checks = [('baseline_count', row.baseline_services), ('optimized_count', row.optimized_services),
                  ('net_change', row.added - row.removed), ('added_services', row.added),
                  ('removed_services', row.removed), ('retimed_in', row.retimed), ('retimed_out', row.retimed)]
        for name, expected in checks:
            if sum(r[name] for r in current) != expected:
                raise ValueError(f'{row.event_date} λ={row.waiting_weight}: {name} differs from evaluated plan')
    return pd.DataFrame(output)


def write_slot_outputs(out, timetable, comparison, reference_weight):
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm

    slots = counts_by_slot(timetable, comparison)
    slots.to_csv(out / 'slot_counts.csv', index=False, encoding='utf-8-sig')
    chosen = slots.loc[slots.waiting_weight == reference_weight]
    dates = sorted(chosen.event_date.unique())
    grid = np.array([chosen.loc[(chosen.event_date == date) & (chosen.direction == direction)]
                    .sort_values('slot_start').net_change.to_numpy()
                    for date in dates for direction in DIRECTIONS])
    limit = max(1, int(np.abs(grid).max()))
    cmap = LinearSegmentedColormap.from_list('departure_change', ['#2e6b9c', '#f4f3f0', '#b9652b'])
    fig, ax = plt.subplots(figsize=(14, max(4, .4 * len(grid) + 1.7)), layout='constrained')
    im = ax.imshow(grid, cmap=cmap, norm=TwoSlopeNorm(vmin=-limit, vcenter=0, vmax=limit), aspect='auto')
    labels = [f'{date}  {direction}' for date in dates for direction in DIRECTIONS]
    ax.set_yticks(range(len(labels)), labels, fontsize=9)
    times = [f'{t // 60:02}:{t % 60:02}' for t in range(START, END, SLOT)]
    ax.set_xticks(range(20), times, rotation=45, ha='right', fontsize=9)
    for y in range(len(grid)):
        for x in range(20):
            n = int(grid[y, x])
            color = 'white' if abs(n) > limit * .65 else '#2f3336'
            ax.text(x, y, f'{n:+d}' if n else '0', ha='center', va='center', fontsize=8, color=color)
    for y in range(1, len(grid), 2):
        ax.axhline(y + .5, color='white', lw=2)
    ax.set_title(f'每 15 分鐘班數淨變化｜新班表 − 原班表｜λ={reference_weight:g}', pad=12)
    ax.set_xlabel('北門離站時段起點（含起點、不含終點；18:00–23:00）')
    ax.spines[['top', 'right', 'left', 'bottom']].set_visible(False)
    bar = fig.colorbar(im, ax=ax, orientation='horizontal', shrink=.5, pad=.06)
    bar.set_label('藍：該時段班數較少　灰：相同　橘：較多；跨時段移時也會造成正負格')
    fig.savefig(out / 'figures' / 'slot_heatmap.png', dpi=160, bbox_inches='tight')
    plt.close(fig)
    return slots
