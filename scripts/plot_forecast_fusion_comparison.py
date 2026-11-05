"""Compare frozen forecasts and same-cohort temporal fusion on Beimen."""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.optimize import linprog


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".local/forecast/all_predictions.csv"
FIGURE = ROOT / "result/forecast/figures/fusion_comparison.png"
TABLE = ROOT / "result/forecast/tables/fusion_comparison.csv"
WEIGHTS = ROOT / "result/forecast/tables/fusion_weights_by_year.csv"
BASES = ["ridge", "gam", "xgboost_weighted"]


def past_mae_weights(history: pd.DataFrame) -> np.ndarray:
    """Globally minimize past MAE subject to nonnegative weights summing to one."""
    x = history[BASES].to_numpy(float)
    y = history["y"].to_numpy(float)
    n = len(history)
    objective = np.r_[np.zeros(3), np.ones(n) / n]
    inequalities = np.r_[np.c_[x, -np.eye(n)], np.c_[-x, -np.eye(n)]]
    bounds = np.r_[y, -y]
    equality = np.c_[np.ones((1, 3)), np.zeros((1, n))]
    result = linprog(
        objective,
        A_ub=inequalities,
        b_ub=bounds,
        A_eq=equality,
        b_eq=[1.0],
        bounds=[(0, None)] * (n + 3),
        method="highs",
    )
    if not result.success:
        raise RuntimeError(f"Fusion optimization failed: {result.message}")
    return result.x[:3]


def main() -> None:
    data = pd.read_csv(SOURCE)
    data = data[data["station"] == "北門"].copy()
    if len(data) != 54 or data["event_date"].nunique() != 9:
        raise ValueError("Expected nine Beimen events, six hours each")
    if data.duplicated(["event_date", "hour"]).any():
        raise ValueError("Duplicate event/hour")
    if data[BASES + ["xgboost", "y"]].isna().any().any():
        raise ValueError("Missing prediction or observed value")

    data["fusion_equal"] = data[BASES].mean(axis=1)
    data["fusion_past_weights"] = np.nan
    weight_rows = []
    for year in sorted(data["year"].unique()):
        history = data[data["year"] < year]
        weights = np.full(3, 1 / 3) if history.empty else past_mae_weights(history)
        current = data["year"] == year
        data.loc[current, "fusion_past_weights"] = data.loc[current, BASES].to_numpy() @ weights
        weight_rows.append({
            "year": int(year), "past_events": history["event_date"].nunique(),
            "ridge": weights[0], "gam": weights[1],
            "xgboost_weighted": weights[2],
            "fallback_equal": history.empty,
        })

    methods = [
        ("Ridge", "ridge"), ("GAM", "gam"),
        ("XGBoost 未加權", "xgboost"),
        ("XGBoost 散場加權", "xgboost_weighted"),
        ("三模型等權融合", "fusion_equal"),
        ("三模型過去學權重融合", "fusion_past_weights"),
    ]
    metrics = pd.DataFrame([
        {"model": label, "mae": np.mean(np.abs(data[col] - data["y"])),
         "rmse": np.sqrt(np.mean((data[col] - data["y"]) ** 2)),
         "events": 9, "rows": 54}
        for label, col in methods
    ])
    FIGURE.parent.mkdir(parents=True, exist_ok=True)
    TABLE.parent.mkdir(parents=True, exist_ok=True)
    metrics.to_csv(TABLE, index=False, encoding="utf-8-sig")
    pd.DataFrame(weight_rows).to_csv(WEIGHTS, index=False, encoding="utf-8-sig")

    plt.rcParams.update({"font.family": "Microsoft JhengHei", "font.size": 11})
    fig, axes = plt.subplots(1, 2, figsize=(14, 6.5))
    colors = ["#acb4b0", "#acb4b0", "#7d9891", "#2e685b", "#ca9b70", "#a7654c"]
    for ax, metric, limit in zip(axes, ["mae", "rmse"], [1050, 1800]):
        ax.set_facecolor("none")
        y = np.arange(len(metrics))
        bars = ax.barh(y, metrics[metric], color=colors, height=0.63)
        ax.set_xlim(0, limit)
        ax.invert_yaxis()
        ax.set_yticks(y, metrics["model"] if ax is axes[0] else [""] * len(y))
        ax.set_title(metric.upper(), loc="left", fontsize=15, weight="bold", pad=12)
        ax.set_xlabel("人次／來源時段；越小越好")
        ax.grid(axis="x", alpha=0.15)
        ax.set_axisbelow(True)
        for spine in ["top", "right", "left"]:
            ax.spines[spine].set_visible(False)
        for bar, value in zip(bars, metrics[metric]):
            ax.text(bar.get_width() + limit * 0.009, bar.get_y() + bar.get_height() / 2,
                    f"{value:,.2f}", va="center", fontsize=10)
    fig.suptitle("九場北門、54 時段：單模型與三模型融合", fontsize=18, weight="bold", y=0.97)
    fig.text(0.06, 0.07,
             "融合使用 Ridge、GAM、散場加權 XGBoost 的同一批逐時預測。\n"
             "過去學權重：2024 無更早同口徑預測，採等權；2025/2026 僅用更早評估年份選非負、總和為 1 的 MAE 最小權重。",
             fontsize=9, color="#555b58", ha="left", va="top")
    fig.subplots_adjust(left=0.23, right=0.95, top=0.83, bottom=0.23, wspace=0.22)
    fig.savefig(FIGURE, dpi=200, transparent=True)
    plt.close(fig)
    print(metrics.to_string(index=False, float_format=lambda n: f"{n:.2f}"))
    print(pd.DataFrame(weight_rows).to_string(index=False))
    print(FIGURE)


if __name__ == "__main__":
    main()
