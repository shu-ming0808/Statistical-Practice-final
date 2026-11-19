"""Rebuild paper figures from archived project outputs without model selection."""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "result" / "research_paper" / "figures"
OUT.mkdir(parents=True, exist_ok=True)
plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 9,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "savefig.transparent": False,
})
INK = "#222222"
MID = "#696969"
LIGHT = "#b1b1b1"


def save(fig, name):
    fig.tight_layout()
    fig.savefig(OUT / name, bbox_inches="tight")
    plt.close(fig)


def forecast_benchmark():
    f = pd.read_csv(ROOT / "result/forecast/baselines/summary.csv")
    order = ["ordinary_day", "past_event_median", "ordinary_plus_event_increment", "xgboost_weighted"]
    labels = [
        "Ordinary-day profile",
        "Prior-year event median",
        "Ordinary + event excess",
        "Frozen weighted XGBoost",
    ]
    v = f.set_index("model").loc[order, "mae"].to_numpy(float)
    fig, ax = plt.subplots(figsize=(6.9, 3.1))
    bars = ax.barh(labels, v, color=[LIGHT, "#969696", MID, INK], height=.6)
    ax.invert_yaxis()
    ax.set_xlim(0, max(v) * 1.18)
    ax.set_xlabel("Mean absolute error (entries per hour)")
    for bar, n in zip(bars, v):
        ax.text(n + 25, bar.get_y() + bar.get_height() / 2, f"{n:,.0f}", va="center")
    save(fig, "forecast_benchmark.pdf")


def peak_profile():
    f = pd.read_csv(ROOT / ".local/forecast/all_predictions.csv")
    g = f.loc[(f.event_date == "2024-08-10") & f.station.eq("北門")].sort_values("hour")
    if len(g) != 6:
        raise ValueError("Peak case missing")
    fig, ax = plt.subplots(figsize=(6.9, 3.2))
    ax.plot(g.hour, g.y, color=INK, marker="o", linewidth=2.1, label="Observed")
    ax.plot(g.hour, g.xgboost_weighted, color=MID, linestyle="--", marker="s",
            linewidth=1.8, label="Frozen XGBoost")
    ax.plot(g.hour, g.retrospective_baseline_56d, color=LIGHT, linestyle=":", marker="^",
            linewidth=1.5, label="Ordinary-day profile")
    ax.set_xticks(g.hour, [f"{h}:00" for h in g.hour])
    ax.set_ylabel("Beimen entries (passengers/hour)")
    ax.set_title("10 Aug 2024: the principal underpredicted peak")
    ax.legend(frameon=False, ncol=3, fontsize=7.6)
    save(fig, "peak_profile.pdf")


def queue_shapes():
    f = pd.read_csv(ROOT / "result/queue_dispatch_v1/summary.csv")
    fig, ax = plt.subplots(figsize=(6.9, 3.2))
    colors = {"uniform": LIGHT, "broad": MID, "concentrated": INK}
    names = {"uniform": "Uniform", "broad": "Broad peak (SD 20 min)", "concentrated": "Concentrated peak (SD 5 min)"}
    for shape in ("uniform", "broad", "concentrated"):
        g = f.loc[f["shape"] == shape].sort_values("remaining_fraction")
        ax.plot(g.remaining_fraction * 100, g.mean_wait_minutes, marker="o",
                linewidth=1.9, color=colors[shape], label=names[shape])
    ax.set_xticks([25, 50, 75])
    ax.set_xlabel("Assumed remaining train capacity (% of 2,220)")
    ax.set_ylabel("Mean platform waiting (minutes)")
    ax.legend(frameon=False, fontsize=7.5)
    save(fig, "queue_shapes.pdf")


def dispatch_tradeoff():
    f = pd.read_csv(ROOT / "result/green_dispatch/summary.csv").sort_values("waiting_weight")
    fig, ax = plt.subplots(figsize=(6.8, 3.2))
    ax.plot(f.optimized_operating_twd / 1e6, f.optimized_mean_wait, "-o", color=INK)
    for row in f.itertuples():
        ax.annotate(r"$\gamma=$" + f"{row.waiting_weight:g}",
                    (row.optimized_operating_twd / 1e6, row.optimized_mean_wait),
                    xytext=(5, 4), textcoords="offset points", fontsize=8)
    ax.scatter(f.baseline_operating_twd.iloc[0] / 1e6, f.baseline_mean_wait.iloc[0],
               color=LIGHT, marker="s", s=55, zorder=5, label="Reference timetable")
    ax.set_xlabel("Modeled variable operating cost, eight events (million TWD)")
    ax.set_ylabel("Passenger-weighted mean wait (minutes)")
    ax.legend(frameon=False, fontsize=8)
    save(fig, "dispatch_tradeoff.pdf")


def benefit_split():
    values = np.array([234087, 59699, 1400624], dtype=float)
    names = ["Beimen", "Other three study stations", "Other modelled passengers"]
    fig, ax = plt.subplots(figsize=(6.9, 3.1))
    bars = ax.barh(names, values / 1e6, color=[INK, MID, LIGHT], height=.56)
    ax.invert_yaxis()
    ax.set_xlim(0, max(values) / 1e6 * 1.18)
    ax.set_xlabel("Reduction in simulated waiting (million passenger-minutes)")
    for b, n in zip(bars, values):
        ax.text(b.get_width() + .02, b.get_y() + b.get_height() / 2,
                f"{n/sum(values):.1%}", va="center")
    save(fig, "benefit_split.pdf")


if __name__ == "__main__":
    forecast_benchmark()
    peak_profile()
    queue_shapes()
    dispatch_tradeoff()
    benefit_split()
