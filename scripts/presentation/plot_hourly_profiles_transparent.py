"""Recreate the frozen nine-event hourly profile with a transparent background."""

from pathlib import Path
import sys

import matplotlib.pyplot as plt
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from forecasting.plot_style import style  # noqa: E402


SOURCE = ROOT / ".local/forecast/predictions.csv"
OUTPUT = ROOT / "result/presentation/method_to_minute_20261103/備用_九場逐時走勢_透明.png"


def main() -> None:
    pred = pd.read_csv(SOURCE, parse_dates=["event_date"])
    pred = pred[pred.station.eq("北門")].sort_values(["event_date", "hour"])
    if len(pred) != 54 or pred.event_date.nunique() != 9:
        raise ValueError("Expected nine Beimen events with six hourly rows each")
    style()
    fig, axes = plt.subplots(3, 3, figsize=(13, 9), constrained_layout=True)
    for ax, (date, group) in zip(axes.flat, pred.groupby("event_date")):
        ax.plot(group.hour, group.y, c="#222222", marker="o", ms=3, label="實際")
        ax.plot(group.hour, group.frozen_w4, c="#438276", ls="--", marker="o", ms=3,
                label="最終 XGBoost")
        ax.set(title=str(date.date()), xticks=range(18, 24), xlabel="來源時段",
               ylabel="北門進站人次", ylim=(0, None))
        ax.set_facecolor("none")
    handles, labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="outside lower center", ncol=2)
    fig.suptitle("最終模型九場逐時對照｜尖峰低估及時段偏移仍須保留")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT, dpi=170, bbox_inches="tight", transparent=True)
    plt.close(fig)
    print(OUTPUT)


if __name__ == "__main__":
    main()
