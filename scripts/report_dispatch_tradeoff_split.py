"""Separate Beimen and background wait effects for published dispatch candidates.

This reads completed independent evaluation outputs. It does not select plans or
re-run the optimizer. The fixed 8/10 minute plans remain diagnostic probes.
"""
from __future__ import annotations

from pathlib import Path
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "result/green_dispatch_diagnostics"
OUT = SOURCE / "tradeoff_split"
SCENARIOS = ["baseline", "current_0.5", "current_1", "current_2", "fixed_8", "fixed_10"]
LABELS = ["原班表", "λ=0.5", "λ=1", "λ=2", "8 分配置", "10 分配置"]
GROUPS = ["北門", "大橋頭站", "雙連", "民權西路", "其他旅次"]


def build() -> pd.DataFrame:
    points = pd.read_csv(SOURCE / "tradeoff_points.csv", encoding="utf-8-sig")
    published = pd.read_csv(ROOT / "result/green_dispatch/station_impacts.csv", encoding="utf-8-sig")
    probes = pd.read_csv(SOURCE / "station_impacts.csv", encoding="utf-8-sig")
    group_rows = []
    for row in published.itertuples(index=False):
        group_rows.append((row.event_date, f"current_{row.waiting_weight:g}", row.station,
                           row.passengers, row.baseline_mean_wait, row.optimized_mean_wait))
    for row in probes.itertuples(index=False):
        if row.scenario_id in ("baseline", "fixed_8", "fixed_10"):
            group_rows.append((row.event_date, row.scenario_id, row.station,
                               row.passengers, row.baseline_mean_wait, row.mean_wait))
    groups = pd.DataFrame(group_rows, columns=["event_date", "scenario_id", "station",
                                                "passengers", "baseline_wait", "plan_wait"])
    if groups.duplicated(["event_date", "scenario_id", "station"]).any():
        raise ValueError("Duplicate group rows")
    if set(groups.station) != set(GROUPS):
        raise ValueError("Unexpected station groups")
    records = []
    for row in points.itertuples(index=False):
        if row.scenario_id not in SCENARIOS:
            continue
        part = groups[(groups.event_date == row.event_date) & (groups.scenario_id == row.scenario_id)]
        if set(part.station) != set(GROUPS):
            raise ValueError(f"Missing group: {row.event_date} {row.scenario_id}")
        n = part.passengers.sum()
        reconstructed = np.dot(part.passengers, part.plan_wait)
        if not np.isclose(reconstructed, row.wait_person_minutes, atol=0.1, rtol=0):
            raise ValueError(f"Waiting accounting mismatch: {row.event_date} {row.scenario_id}")
        def summary(names: list[str]) -> tuple[float, float]:
            sub = part[part.station.isin(names)]
            mass = sub.passengers.sum()
            return float(np.dot(sub.passengers, sub.plan_wait) / mass), float(
                np.dot(sub.passengers, sub.plan_wait - sub.baseline_wait))
        beimen_wait, beimen_delta = summary(["北門"])
        peer_wait, peer_delta = summary(GROUPS[1:4])
        background_wait, background_delta = summary(["其他旅次"])
        records.append(dict(event_date=row.event_date, scenario_id=row.scenario_id,
            scenario_label=LABELS[SCENARIOS.index(row.scenario_id)], kind=row.kind,
            added=row.added, removed=row.removed, retimed=row.retimed,
            operating_change_twd=row.operating_change_twd,
            all_wait_minutes=row.mean_wait, beimen_wait_minutes=beimen_wait,
            peer_stations_wait_minutes=peer_wait, background_wait_minutes=background_wait,
            beimen_delta_person_minutes=beimen_delta, peer_delta_person_minutes=peer_delta,
            background_delta_person_minutes=background_delta,
            all_delta_person_minutes=row.wait_person_minutes -
                float(np.dot(part.passengers, part.baseline_wait)),
            modeled_passengers=n, evaluation_fully_served=row.evaluation_fully_served,
            diagnostic_only=row.diagnostic_only))
    out = pd.DataFrame(records)
    if len(out) != 8 * len(SCENARIOS):
        raise ValueError("Expected eight events and six plans")
    split = out.beimen_delta_person_minutes + out.peer_delta_person_minutes + out.background_delta_person_minutes
    if not np.allclose(split, out.all_delta_person_minutes, atol=0.1, rtol=0):
        raise ValueError("Wait decomposition does not reconcile")
    return out


def plot(frame: pd.DataFrame) -> None:
    plt.rcParams.update({"font.family": ["Microsoft JhengHei", "DejaVu Sans"],
                         "axes.unicode_minus": False, "figure.dpi": 160})
    events = sorted(frame.event_date.unique())
    scenarios = SCENARIOS[1:]
    subset = frame[frame.scenario_id.isin(scenarios)].set_index(["event_date", "scenario_id"])
    fields = [
        ("operating_change_twd", "可變營運支出差（萬元）", 1e4),
        ("beimen_wait_minutes", "北門平均候車（分）", 1),
        ("peer_stations_wait_minutes", "其餘三站平均候車（分）", 1),
        ("background_wait_minutes", "沿線其他旅次平均候車（分）", 1),
    ]
    fig, axes = plt.subplots(1, 4, figsize=(17, 7), layout="constrained")
    for ax, (field, title, scale) in zip(axes, fields):
        values = np.array([[subset.loc[(e, s), field] / scale for s in scenarios] for e in events])
        if field == "operating_change_twd":
            high = np.max(np.abs(values)); vmin, vmax = -high, high; cmap = "RdBu_r"
        else:
            vmin, vmax = values.min(), values.max(); cmap = "YlOrRd"
        ax.imshow(values, cmap=cmap, vmin=vmin, vmax=vmax, aspect="auto")
        for i in range(len(events)):
            for j in range(len(scenarios)):
                ax.text(j, i, f"{values[i, j]:.1f}", ha="center", va="center", fontsize=9,
                        color="#161616")
        ax.set_xticks(range(len(scenarios)), [LABELS[SCENARIOS.index(s)] for s in scenarios],
                      rotation=45, ha="right")
        ax.set_yticks(range(len(events)), [e[2:] for e in events] if ax is axes[0] else [])
        ax.set_title(title, loc="left", fontsize=11)
        ax.spines[:].set_visible(False)
    fig.suptitle("每場調度方案：支出與候車分開看", fontsize=16, x=0.01, ha="left")
    fig.supxlabel("8／10 分配置是減班診斷，並非入選班表；色彩僅在同一欄內比較。", fontsize=10)
    fig.savefig(OUT / "event_cost_wait_split.png", transparent=True, bbox_inches="tight")
    plt.close(fig)

    chosen = frame[frame.scenario_id == "current_1"].set_index("event_date").loc[events]
    x = np.arange(len(events)); width = .23
    fig, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True, layout="constrained")
    axes[0].bar(x-width/2, chosen.added, width, label="增班", color="#557a70")
    axes[0].bar(x+width/2, -chosen.removed, width, label="減班", color="#a26655")
    axes[0].axhline(0, color="#777", lw=.8); axes[0].set_ylabel("班數")
    axes[0].legend(frameon=False, ncol=2)
    axes[1].bar(x, chosen.operating_change_twd/1e4, width*2, color="#a1a7a3")
    axes[1].axhline(0, color="#777", lw=.8); axes[1].set_ylabel("支出差（萬元）")
    axes[2].bar(x-width, chosen.beimen_delta_person_minutes/1000, width,
                label="北門", color="#526e65")
    axes[2].bar(x, chosen.peer_delta_person_minutes/1000, width,
                label="其餘三站", color="#ad845f")
    axes[2].bar(x+width, chosen.background_delta_person_minutes/1000, width,
                label="沿線其他旅次", color="#9da5a1")
    axes[2].axhline(0, color="#777", lw=.8); axes[2].set_ylabel("候車變化（千人分鐘）")
    axes[2].legend(frameon=False, ncol=3)
    axes[2].set_xticks(x, [e[2:] for e in events], rotation=25, ha="right")
    fig.suptitle("λ=1 相對原班表：增班支出與各類旅客等待變化", fontsize=15)
    fig.savefig(OUT / "selected_plan_driver_split.png", transparent=True, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    frame = build()
    frame.to_csv(OUT / "per_event_metrics.csv", index=False, encoding="utf-8-sig")
    plot(frame)
    chosen = frame[frame.scenario_id == "current_1"]
    gain = -chosen[["beimen_delta_person_minutes", "peer_delta_person_minutes",
                    "background_delta_person_minutes"]].sum()
    total = gain.sum()
    lines = ["# 調度目標分解：增班是誰的等待改善？", "",
        "沿用已完成的 8 場獨立評估結果，不重新選班表；每場將候車人分鐘拆為北門、其餘三站及沿線其他旅次。後者是模型群組，不能直接當成實測乘客。",
        "", f"λ=1 的 8 場方案合計增 {int(chosen.added.sum())} 班、減 {int(chosen.removed.sum())} 班；相對原班表的可變支出 +{chosen.operating_change_twd.sum():,.0f} 元。",
        f"總候車減少 {total:,.0f} 人分鐘，其中北門 {gain.iloc[0]:,.0f}（{gain.iloc[0]/total:.1%}）、其餘三站 {gain.iloc[1]:,.0f}（{gain.iloc[1]/total:.1%}）、沿線其他旅次 {gain.iloc[2]:,.0f}（{gain.iloc[2]/total:.1%}）。",
        "", "![逐場支出與候車](event_cost_wait_split.png)", "",
        "![λ=1 增班與候車來源](selected_plan_driver_split.png)", "",
        "8／10 分配置是減班診斷，沒有被原選擇器正式採用。圖中只顯示有限方案，不能聲稱已求出全域 Pareto 前緣。", "",
        "[逐場數據](per_event_metrics.csv)；來源：`result/green_dispatch/comparison.csv`、`station_impacts.csv` 與 `result/green_dispatch_diagnostics/`。", "",
        "重跑：`python -X utf8 scripts/report_dispatch_tradeoff_split.py`。", ""]
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:5]))


if __name__ == "__main__":
    main()
