"""Same-fold, past-only simple baselines for the frozen Beimen forecast cohort.

This script reads private event-level inputs and predictions, but publishes only
aggregate model and event scores. It does not retune the frozen forecast models.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from forecasting.evaluation import metrics

OUT = ROOT / "result" / "forecast" / "baselines"
FEATURE = "retrospective_baseline_56d"
COLS = ["event_date", "station", "hour"]
MODELS = {
    "ordinary_day": "Past 56-day same-weekday non-event flow",
    "past_event_median": "Prior-year event station-hour median",
    "ordinary_plus_event_increment": "Ordinary flow + prior-year median excess",
    "xgboost_weighted": "Frozen weighted XGBoost",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_baselines(panel: pd.DataFrame, years: list[int]) -> pd.DataFrame:
    blocks = []
    for year in years:
        train = panel.loc[panel.year < year]
        test = panel.loc[(panel.year == year) & panel.station.eq("北門")].copy()
        if train.empty or test.empty or train.event_date.max() >= test.event_date.min():
            raise ValueError(f"Chronology violated for {year}")
        if train.duplicated(COLS).any() or test.duplicated(COLS).any():
            raise ValueError("Duplicate station-hour event key")
        test["ordinary_day"] = test[FEATURE]
        event_median = train.groupby(["station", "hour"], sort=False)["y"].median()
        excess = train.loc[train[FEATURE].notna()].copy()
        excess["increment"] = excess.y - excess[FEATURE]
        excess_median = excess.groupby(["station", "hour"], sort=False)["increment"].median()
        keys = pd.MultiIndex.from_frame(test[["station", "hour"]])
        test["past_event_median"] = event_median.reindex(keys).to_numpy(float)
        test["ordinary_plus_event_increment"] = (
            test[FEATURE].to_numpy(float) + excess_median.reindex(keys).to_numpy(float)
        )
        for name in ("ordinary_day", "past_event_median", "ordinary_plus_event_increment"):
            test[name] = np.maximum(test[name].to_numpy(float), 0)
        test["train_events"] = train.event_date.nunique()
        test["train_end"] = train.event_date.max()
        blocks.append(test)
    result = pd.concat(blocks, ignore_index=True).sort_values(COLS).reset_index(drop=True)
    if len(result) != 54 or result.event_date.nunique() != 9:
        raise ValueError("Expected exactly nine events and 54 Beimen hours")
    if result[["ordinary_day", "past_event_median", "ordinary_plus_event_increment"]].isna().any().any():
        raise ValueError("A baseline prediction is missing; no implicit fallback is permitted")
    return result


def event_rows(frame: pd.DataFrame, models: list[str]) -> pd.DataFrame:
    output = []
    for date, event in frame.groupby("event_date"):
        for name in models:
            score = metrics(event, event[name])
            output.append({"event_date": str(date.date()), "model": name, **score})
    return pd.DataFrame(output)


def paired_bootstrap(events: pd.DataFrame, models: list[str], draws: int = 20000) -> pd.DataFrame:
    wide = events.pivot(index="event_date", columns="model", values="mae")
    reference = wide["xgboost_weighted"].to_numpy(float)
    rng = np.random.default_rng(20261119)
    indices = rng.integers(0, len(wide), size=(draws, len(wide)))
    rows = []
    for name in models:
        if name == "xgboost_weighted":
            continue
        difference = wide[name].to_numpy(float) - reference
        samples = difference[indices].mean(axis=1)
        rows.append({
            "comparison": f"{name} minus frozen weighted XGBoost",
            "mean_mae_difference": difference.mean(),
            "bootstrap_p025": np.quantile(samples, .025),
            "bootstrap_p975": np.quantile(samples, .975),
            "resampling_unit": "event",
            "events": len(wide),
            "interpretation": "descriptive paired resampling; not new-event coverage",
        })
    return pd.DataFrame(rows)


def plot(summary: pd.DataFrame, events: pd.DataFrame) -> None:
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9})
    order = list(MODELS)
    colors = ["#8d9892", "#636d70", "#456b6a", "#202b2b"]
    values = summary.set_index("model").loc[order, "mae"]
    fig, ax = plt.subplots(figsize=(7.4, 3.6))
    bars = ax.barh(np.arange(len(order)), values, color=colors, height=.62)
    ax.set_yticks(np.arange(len(order)), [MODELS[x] for x in order])
    ax.invert_yaxis()
    ax.set_xlim(0, max(values) * 1.24)
    ax.set_xlabel("Beimen hourly MAE (passenger entries)")
    ax.set_title("Nine events; identical forward-year evaluation folds")
    ax.spines[["top", "right"]].set_visible(False)
    for bar, value in zip(bars, values):
        ax.text(bar.get_width() + max(values) * .015, bar.get_y() + bar.get_height() / 2,
                f"{value:,.1f}", va="center")
    fig.tight_layout()
    fig.savefig(OUT / "baseline_mae.png", dpi=220, transparent=True)
    plt.close(fig)

    wide = events.pivot(index="event_date", columns="model", values="mae")
    fig, ax = plt.subplots(figsize=(8.2, 3.8))
    for name, color in zip(order, colors):
        ax.plot(wide.index, wide[name], marker="o", linewidth=1.6, color=color, label=MODELS[name])
    ax.set_ylabel("Hourly MAE (passenger entries)")
    ax.set_xlabel("Event date")
    ax.tick_params(axis="x", rotation=35)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(loc="upper left", ncol=2, frameon=False, fontsize=7.5)
    fig.tight_layout()
    fig.savefig(OUT / "baseline_event_mae.png", dpi=220, transparent=True)
    plt.close(fig)


def main() -> None:
    config = json.loads((ROOT / "configs" / "forecast.json").read_text(encoding="utf-8"))
    raw = ROOT / config["input"]
    archived = ROOT / ".local" / "forecast" / "all_predictions.csv"
    if sha256(raw) != config["input_sha256"]:
        raise ValueError("Frozen raw input hash mismatch")
    panel = pd.read_csv(raw, parse_dates=["event_date"])
    original = pd.read_csv(archived, parse_dates=["event_date"]).sort_values(COLS).reset_index(drop=True)
    forecast = build_baselines(panel, config["years"])
    if not forecast[COLS].equals(original[COLS]):
        raise ValueError("Evaluation cohort is not identical to the frozen forecasts")
    np.testing.assert_array_equal(forecast.y.to_numpy(float), original.y.to_numpy(float))
    forecast["xgboost_weighted"] = original["xgboost_weighted"].to_numpy(float)
    if abs(metrics(forecast, forecast.xgboost_weighted)["mae"] - 440.28167102955) > 1e-8:
        raise ValueError("Frozen reference MAE mismatch")
    model_names = list(MODELS)
    summary = pd.DataFrame([{"model": name, **metrics(forecast, forecast[name])} for name in model_names])
    events = event_rows(forecast, model_names)
    intervals = paired_bootstrap(events, model_names)
    OUT.mkdir(parents=True, exist_ok=True)
    summary.to_csv(OUT / "summary.csv", index=False, encoding="utf-8-sig")
    events.to_csv(OUT / "per_event.csv", index=False, encoding="utf-8-sig")
    intervals.to_csv(OUT / "paired_mae_differences.csv", index=False, encoding="utf-8-sig")
    manifest = {
        "raw_input_sha256": sha256(raw),
        "frozen_predictions_sha256": sha256(archived),
        "years": config["years"],
        "train_only_event_analogs": True,
        "same_nine_event_folds": True,
        "ordinary_day_source": FEATURE,
        "ordinary_day_historical_publication_verified": False,
        "bootstrap_seed": 20261119,
        "bootstrap_draws": 20000,
        "baseline_formulas_not_tuned_on_nine_event_holdout": True,
        "frozen_xgboost_4x_weight_selected_after_inspecting_nine_events": True,
        "paired_bootstrap_is_descriptive_not_formal_future_coverage": True,
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    plot(summary, events)
    print(summary[["model", "mae", "rmse", "peak_abs", "window_under"]].round(2).to_string(index=False))
    print(intervals[["comparison", "mean_mae_difference", "bootstrap_p025", "bootstrap_p975"]].round(2).to_string(index=False))


if __name__ == "__main__":
    main()
