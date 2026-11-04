"""Plot the two metric groups in the integrated report from the 201-point sweep."""

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, MultipleLocator


HERE = Path(__file__).resolve().parents[1]
SOURCE = HERE / "data" / "sweep.csv"

with SOURCE.open(encoding="utf-8-sig", newline="") as handle:
    rows = list(csv.DictReader(handle))

k = [float(row["scale"]) for row in rows]
hour_mae = [float(row["hourly_mae"]) for row in rows]
hour_rmse = [float(row["hourly_rmse"]) for row in rows]
peak_mae = [float(row["actual_peak_mae"]) for row in rows]
peak_under = [float(row["actual_peak_under"]) for row in rows]
peak_over = [mae - under for mae, under in zip(peak_mae, peak_under)]

# hourly_bias = mean(prediction - actual); MAE = (under + over) / 54.
hour_under = [27 * (float(row["hourly_mae"]) - float(row["hourly_bias"])) for row in rows]
hour_over = [27 * (float(row["hourly_mae"]) + float(row["hourly_bias"])) for row in rows]

plt.rcParams.update(
    {
        "font.family": "Microsoft JhengHei",
        "font.size": 11,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.titleweight": "bold",
        "axes.labelcolor": "#262B31",
        "text.color": "#262B31",
        "svg.fonttype": "none",
        "savefig.facecolor": "white",
    }
)

BLUE = "#28649B"
PURPLE = "#8062A8"
ORANGE = "#C95426"
GREEN = "#0C836C"
GRID = "#DDE3E8"


def finish_axis(ax, *, xlim=None, xlabel=False):
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    if xlim:
        ax.set_xlim(*xlim)
    if xlabel:
        ax.set_xlabel("倍率 k")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))


def save(fig, stem):
    fig.savefig(HERE / "figures" / f"{stem}.png", dpi=180, bbox_inches="tight")
    plt.close(fig)


fig = plt.figure(figsize=(12.4, 10.2), layout="constrained")
grid = fig.add_gridspec(3, 2, height_ratios=[1, 1, 1.25])
fig.suptitle("全部 54 小時：MAE、RMSE 與低估／高估總量", fontsize=17, fontweight="bold")

ax = fig.add_subplot(grid[0, :])
ax.plot(k, hour_mae, color=BLUE, linewidth=2.3)
ax.scatter([1.05], [hour_mae[5]], color=BLUE, zorder=3, s=38)
ax.annotate("最低：k=1.05，437.14", (1.05, hour_mae[5]), xytext=(1.068, 449),
            arrowprops={"arrowstyle": "-", "color": BLUE}, color=BLUE)
ax.set_ylabel("MAE（人次／小時）")
ax.set_ylim(432, 482)
ax.xaxis.set_major_locator(MultipleLocator(0.02))
finish_axis(ax, xlim=(1.0, 1.14), xlabel=True)

ax = fig.add_subplot(grid[1, :])
ax.plot(k, hour_rmse, color=PURPLE, linewidth=2.3)
ax.scatter([1.06], [hour_rmse[6]], color=PURPLE, zorder=3, s=38)
ax.annotate("最低：k=1.06，671.79", (1.06, hour_rmse[6]), xytext=(1.076, 685),
            arrowprops={"arrowstyle": "-", "color": PURPLE}, color=PURPLE)
ax.set_ylabel("RMSE（人次／小時）")
ax.set_ylim(665, 715)
ax.xaxis.set_major_locator(MultipleLocator(0.02))
finish_axis(ax, xlim=(1.0, 1.14), xlabel=True)

ax = fig.add_subplot(grid[2, 0])
ax.plot(k, hour_under, color=ORANGE, linewidth=2.2, label="低估總量")
ax.plot(k, hour_over, color=GREEN, linewidth=2.2, label="高估總量")
ax.set_ylabel("低估／高估總量（人次）")
ax.set_ylim(8000, 18500)
ax.xaxis.set_major_locator(MultipleLocator(0.04))
finish_axis(ax, xlim=(1.0, 1.20), xlabel=True)
ax.legend(frameon=False, ncol=2, loc="upper right")

ax = fig.add_subplot(grid[2, 1])
ax.plot(k, hour_under, color=ORANGE, linewidth=2.2, label="低估總量")
ax.plot(k, hour_over, color=GREEN, linewidth=2.2, label="高估總量")
ax.set_ylabel("低估／高估總量（人次）")
finish_axis(ax, xlim=(1.0, 3.0), xlabel=True)

save(fig, "hourly_metrics")


fig = plt.figure(figsize=(12.4, 7.6), layout="constrained")
grid = fig.add_gridspec(2, 1, height_ratios=[1.12, 1])
fig.suptitle("實際尖峰小時：平均低估、高估與雙向 MAE", fontsize=17, fontweight="bold")

ax = fig.add_subplot(grid[0])
ax.plot(k, peak_under, color=ORANGE, linewidth=2.3, label="平均低估")
ax.plot(k, peak_over, color=GREEN, linewidth=2.3, label="平均高估")
ax.plot(k, peak_mae, color=BLUE, linewidth=2.5, label="雙向 MAE")
ax.scatter([1.06], [peak_mae[6]], color=BLUE, zorder=3, s=38)
ax.annotate("MAE 最低：k=1.06，665.92", (1.06, peak_mae[6]), xytext=(1.071, 780),
            arrowprops={"arrowstyle": "-", "color": BLUE}, color=BLUE)
ax.set_ylabel("人次／場")
ax.set_ylim(0, 850)
ax.xaxis.set_major_locator(MultipleLocator(0.02))
finish_axis(ax, xlim=(1.0, 1.14), xlabel=True)
ax.legend(frameon=False, ncol=3, loc="upper left")

ax = fig.add_subplot(grid[1])
ax.plot(k, peak_under, color=ORANGE, linewidth=2.3, label="平均低估")
ax.plot(k, peak_over, color=GREEN, linewidth=2.3, label="平均高估")
ax.plot(k, peak_mae, color=BLUE, linewidth=2.5, label="雙向 MAE")
ax.axvline(1.33, color="#8E969D", linestyle="--", linewidth=1)
ax.annotate("低估停在 176.02", (1.33, peak_under[33]), xytext=(1.52, 1500),
            arrowprops={"arrowstyle": "-", "color": "#5D666E"}, color="#5D666E")
ax.set_ylabel("人次／場")
finish_axis(ax, xlim=(1.0, 3.0), xlabel=True)

save(fig, "actual_peak_metrics")
