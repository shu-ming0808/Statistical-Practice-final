"""Render English, scalable README figures from already published results."""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "app/frontend/public/data"
COLORS = {"uniform": "#4a7f8e", "broad": "#bf9558", "concentrated": "#ae5360"}
SHAPES = {"uniform": "Uniform", "broad": "Broad peak (20 min)", "concentrated": "Narrow peak (5 min)"}


def save(fig: plt.Figure, name: str) -> None:
    out = ROOT / "docs/images"
    out.mkdir(parents=True, exist_ok=True)
    for extension in ("svg", "png"):
        fig.savefig(out / f"{name}.{extension}", dpi=300, bbox_inches="tight", transparent=True)
    plt.close(fig)


def forecast_diagnostics() -> None:
    published = json.loads((PUBLIC / "research.json").read_text(encoding="utf-8"))["model"]["hourly"]
    if len(published) != 54 or len({r["event_date"] for r in published}) != 9:
        raise ValueError("Expected the published nine-event, 54-hour cohort")
    frame = pd.DataFrame(published)
    frame["error"] = frame.predicted - frame.actual
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.3), layout="constrained")
    bins = np.arange(np.floor(frame.error.min() / 500) * 500,
                     np.ceil(frame.error.max() / 500) * 500 + 501, 500)
    axes[0].hist(frame.error, bins=bins, color="#438276", edgecolor="white")
    axes[0].axvline(0, color="#555555", linestyle="--")
    axes[0].set(title="Final XGBoost: hourly errors",
                xlabel="Predicted minus observed passengers (negative = underprediction)",
                ylabel="Number of station-hours")
    for year, color in [(2024, "#716690"), (2025, "#438276"), (2026, "#c49b59")]:
        group = frame[frame.event_date.str.startswith(str(year))]
        axes[1].scatter(group.actual, group.predicted, label=str(year), color=color, s=30)
    top = max(frame.actual.max(), frame.predicted.max()) * 1.05
    axes[1].plot([0, top], [0, top], linestyle="--", color="#555555")
    axes[1].set(xlim=(0, top), ylim=(0, top), xlabel="Observed entries", ylabel="Predicted entries",
                title="Predicted versus observed")
    axes[1].legend(title="Evaluation year", frameon=False)
    fig.suptitle("Beimen: 54 station-hours across nine events | retrospective evaluation",
                 fontsize=13)
    save(fig, "forecast_diagnostics_en")


def minute_queue() -> None:
    replay = json.loads((PUBLIC / "rail-replay.json").read_text(encoding="utf-8"))
    cases = replay["events"]["2024-08-10"]
    fig, axes = plt.subplots(2, 1, figsize=(11.5, 7), sharex=True, layout="constrained")
    for shape in SHAPES:
        case = cases[f"{shape}-0.5"]
        north, south = case["north"], case["south"]
        arrivals = np.asarray(north["arrivals"]) + np.asarray(south["arrivals"])
        x = 18 + (np.arange(len(arrivals)) + 0.5) / 60
        axes[0].plot(x, arrivals, label=SHAPES[shape], color=COLORS[shape], linewidth=1.7)
        trains = {int(t["time"]): t for t in south["trains"]}
        qx, qy = [], []
        for minute, after in enumerate(south["queue"]):
            time = 18 + minute / 60
            if minute in trains:
                qx.extend((time, time))
                qy.extend((trains[minute]["before"], trains[minute]["left"]))
            else:
                qx.append(time)
                qy.append(after)
        axes[1].plot(qx, qy, color=COLORS[shape], linewidth=1.5)
    axes[0].set(title="Same hourly demand, different minute-level arrival patterns",
                ylabel="Arrivals per minute (both directions)")
    axes[0].legend(ncol=3, frameon=False, loc="upper right")
    axes[1].set(title="Queue toward Xindian | 1,110 seats available per train",
                xlabel="Clock time (24:00 = next day)", ylabel="Passengers waiting")
    for ax in axes:
        ax.set_xlim(18, 24.85)
        ax.grid(alpha=0.17)
    fig.suptitle("Beimen, 2024-08-10 forecast with the 2026-08-30 standard Saturday timetable\n"
                 "Scenario illustration; not observed event-day waiting", fontsize=13)
    save(fig, "minute_queue_en")


def cost_wait_tradeoff() -> None:
    points = pd.read_csv(ROOT / "result/green_dispatch_diagnostics/tradeoff_points.csv")
    events = sorted(points.event_date.unique())
    if len(events) != 8:
        raise ValueError("Expected eight published dispatch events")
    fig, axes = plt.subplots(4, 2, figsize=(12, 12.5), layout="constrained")
    for ax, event in zip(axes.flat, events):
        group = points[points.event_date == event]
        baseline = group[group.kind == "baseline"]
        chosen = group[group.kind == "current"].sort_values("waiting_weight")
        probes = group[group.kind == "reduction_probe"]
        ax.plot(chosen.operating_change_twd / 10000, chosen.mean_wait,
                color="#7baa86", linewidth=1.2, zorder=1)
        ax.scatter(chosen.operating_change_twd / 10000, chosen.mean_wait,
                   c=chosen.waiting_weight, cmap="Greens", vmin=0, vmax=2.8,
                   s=48, edgecolors="#366946", linewidths=0.5, zorder=2)
        ax.scatter(baseline.operating_change_twd / 10000, baseline.mean_wait,
                   marker="s", color="#56565c", s=55, zorder=3)
        ax.scatter(probes.operating_change_twd / 10000, probes.mean_wait,
                   marker="D", facecolors="none", edgecolors="#9d6945", s=70, zorder=3)
        for weight, offset in [(0.25, (5, 6)), (1.0, (5, 7)), (2.5, (5, 6))]:
            row = chosen[np.isclose(chosen.waiting_weight, weight)]
            if not row.empty:
                point = row.iloc[0]
                ax.annotate(f"λ={weight:g}", (point.operating_change_twd / 10000, point.mean_wait),
                            xytext=offset, textcoords="offset points", fontsize=8, color="#2e5c3b")
        ax.axvline(0, color="#aaaaaa", linewidth=0.7)
        ax.set(title=event, xlim=(-19, 95), ylim=(2, 11),
               xlabel="Variable cost change (NT$10,000)",
               ylabel="Mean waiting time (min)")
        ax.grid(axis="y", color="#e6e6e6")
    fig.suptitle("Operating cost versus waiting time | 300 evaluation simulations per event",
                 fontsize=14)
    fig.supxlabel("Square: baseline; filled circles: selected weights; hollow diamonds: unselected service-reduction probes.\n"
                 "Finite candidate comparison, not a global optimum.", fontsize=10)
    save(fig, "cost_wait_tradeoff_en")


def dispatch_heatmap() -> None:
    slots = pd.read_csv(ROOT / "result/green_dispatch/slot_counts.csv")
    chosen = slots[np.isclose(slots.waiting_weight, 1)]
    events = sorted(chosen.event_date.unique())
    times = sorted(chosen.slot_start.unique())
    directions = [("往松山", "To Songshan"), ("往新店", "To Xindian")]
    if len(events) != 8 or len(times) != 20:
        raise ValueError("Expected eight events and twenty 15-minute intervals")
    rows, labels = [], []
    for event in events:
        for direction, english in directions:
            selected = chosen[(chosen.event_date == event) & (chosen.direction == direction)]
            selected = selected.set_index("slot_start").reindex(times)
            if selected.net_change.isna().any():
                raise ValueError(f"Incomplete slot counts: {event} {direction}")
            rows.append(selected.net_change.to_numpy(dtype=int))
            labels.append(f"{event}  {english}")
    grid = np.asarray(rows)
    limit = max(1, int(np.abs(grid).max()))
    cmap = LinearSegmentedColormap.from_list("change", ["#2e6b9c", "#f4f3f0", "#b9652b"])
    fig, ax = plt.subplots(figsize=(14, 8.6), layout="constrained")
    mesh = ax.pcolormesh(np.arange(21), np.arange(17), grid,
                         cmap=cmap, norm=TwoSlopeNorm(vmin=-limit, vcenter=0, vmax=limit),
                         edgecolors="white", linewidth=0.4, shading="flat")
    ax.set_xlim(0, 20)
    ax.set_ylim(16, 0)
    ax.set_xticks(np.arange(20) + 0.5, times, rotation=45, ha="right", fontsize=8)
    ax.set_yticks(np.arange(16) + 0.5, labels, fontsize=8)
    for row in range(16):
        for column in range(20):
            value = grid[row, column]
            ax.text(column + 0.5, row + 0.5, f"{value:+d}" if value else "0",
                    ha="center", va="center", fontsize=7,
                    color="white" if abs(value) > 0.65 * limit else "#2f3336")
    ax.set(title="15-minute departure count change | candidate minus standard timetable | λ=1",
           xlabel="Beimen departure interval start (18:00–23:00)")
    colorbar = fig.colorbar(mesh, ax=ax, orientation="horizontal", shrink=0.5, pad=0.07)
    colorbar.solids.set_rasterized(False)
    colorbar.set_label("Blue: fewer in this interval   Grey: unchanged   Orange: more")
    fig.supxlabel("A train shifted across interval boundaries can create both a positive and a negative cell.",
                 fontsize=10)
    save(fig, "dispatch_heatmap_en")


def main() -> None:
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "svg.fonttype": "none", "axes.unicode_minus": True})
    forecast_diagnostics()
    minute_queue()
    cost_wait_tradeoff()
    dispatch_heatmap()


if __name__ == "__main__":
    main()
