"""Explore unnormalized minute weights on frozen Beimen hourly forecasts.

Only the forecast peak hour is scaled. The existing Gaussian minute profile
still sums to one before multiplication by the scale factor.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
EXPECTED_SHA256 = "96915ef02b9ebbf6fae909209f85b65cd9da1f2682dc87497d198bdb53daf3ef"
SCALES = np.round(np.arange(1.0, 3.0001, 0.01), 2)


def load_predictions(path: Path) -> pd.DataFrame:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != EXPECTED_SHA256:
        raise ValueError(f"Prediction file SHA-256 differs from frozen cohort: {digest}")
    frame = pd.read_csv(path, usecols=["event_date", "station", "hour", "y", "frozen_w4"])
    if len(frame) != 54 or frame.event_date.nunique() != 9 or not frame.station.eq("北門").all():
        raise ValueError("Expected nine Beimen events with 54 rows")
    if frame.duplicated(["event_date", "hour"]).any():
        raise ValueError("Duplicate event hour")
    for _, event in frame.groupby("event_date"):
        if sorted(event.hour.tolist()) != list(range(18, 24)):
            raise ValueError("Incomplete 18–23 event hours")
    if not np.isfinite(frame[["y", "frozen_w4"]].to_numpy(float)).all():
        raise ValueError("Nonfinite counts")
    if (frame[["y", "frozen_w4"]] < 0).any().any():
        raise ValueError("Negative counts")
    frame = frame.sort_values(["event_date", "hour"]).reset_index(drop=True)
    frame["forecast_peak"] = False
    peak_indices = frame.groupby("event_date", sort=False).frozen_w4.idxmax()
    frame.loc[peak_indices, "forecast_peak"] = True
    return frame


def scored(frame: pd.DataFrame, prediction: np.ndarray) -> dict:
    actual = frame.y.to_numpy(float)
    error = prediction - actual
    peak = frame.groupby("event_date").y.idxmax().to_numpy()
    return {
        "hourly_mae": float(np.mean(np.abs(error))),
        "hourly_rmse": float(np.sqrt(np.mean(error**2))),
        "hourly_bias": float(np.mean(error)),
        "actual_peak_mae": float(np.mean(np.abs(error[peak]))),
        "actual_peak_under": float(np.mean(np.maximum(-error[peak], 0))),
        "event_total_mae": float(np.mean(np.abs(pd.Series(error).groupby(frame.event_date).sum()))),
    }


def predictions(frame: pd.DataFrame, scale: float) -> np.ndarray:
    return frame.frozen_w4.to_numpy(float) * np.where(frame.forecast_peak.to_numpy(), scale, 1.0)


def experiment(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    sweep = pd.DataFrame([{"scale": scale, **scored(frame, predictions(frame, scale))} for scale in SCALES])
    selected = float(sweep.loc[sweep.hourly_mae.idxmin(), "scale"])
    event_rows = []
    for event_date in frame.event_date.unique():
        train = frame.loc[frame.event_date.ne(event_date)].copy().reset_index(drop=True)
        holdout = frame.loc[frame.event_date.eq(event_date)].copy().reset_index(drop=True)
        training_mae = [scored(train, predictions(train, scale))["hourly_mae"] for scale in SCALES]
        train_scale = float(SCALES[np.argmin(training_mae)])
        row = {"event_date": event_date, "selected_without_event": train_scale,
               "baseline_event_mae": scored(holdout, predictions(holdout, 1.0))["hourly_mae"],
               "held_out_event_mae": scored(holdout, predictions(holdout, train_scale))["hourly_mae"],
               "full_sample_selected_event_mae": scored(holdout, predictions(holdout, selected))["hourly_mae"]}
        event_rows.append(row)
    validation = pd.DataFrame(event_rows)
    held_out = frame.copy()
    held_out["chosen_scale"] = held_out.event_date.map(validation.set_index("event_date").selected_without_event)
    held_out_prediction = held_out.frozen_w4.to_numpy(float) * np.where(
        held_out.forecast_peak.to_numpy(), held_out.chosen_scale.to_numpy(float), 1.0)
    comparison = pd.DataFrame([
        {"method": "baseline_1.00", **scored(frame, predictions(frame, 1.0))},
        {"method": f"in_sample_best_{selected:.2f}", **scored(frame, predictions(frame, selected))},
        {"method": "leave_one_event_out_selection", **scored(frame, held_out_prediction)},
    ])
    return sweep, validation, comparison


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prediction_csv", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "result/minute_weight_scale_experiment")
    args = parser.parse_args()
    frame = load_predictions(args.prediction_csv)
    sweep, validation, comparison = experiment(frame)
    baseline = comparison.iloc[0]
    if not np.isclose(baseline.hourly_mae, 440.28167102955, atol=1e-6):
        raise AssertionError("Frozen baseline MAE does not match published result")
    args.output.mkdir(parents=True, exist_ok=True)
    sweep.to_csv(args.output / "sweep.csv", index=False, encoding="utf-8-sig")
    validation.to_csv(args.output / "leave_one_event_out.csv", index=False, encoding="utf-8-sig")
    comparison.to_csv(args.output / "comparison.csv", index=False, encoding="utf-8-sig")
    best_mae = sweep.loc[sweep.hourly_mae.idxmin()]
    best_rmse = sweep.loc[sweep.hourly_rmse.idxmin()]
    best_peak = sweep.loc[sweep.actual_peak_mae.idxmin()]
    selected = float(best_mae.scale)
    fixed_event_mae = validation.full_sample_selected_event_mae
    event_worse = int((fixed_event_mae > validation.baseline_event_mae).sum())
    peak_rows = frame.loc[frame.forecast_peak]
    largest_break_even = float((peak_rows.y / peak_rows.frozen_w4).max())
    minute_centers = np.arange(60) + 0.5
    individual_above_one = {}
    for sigma in (5, 20):
        gaussian = np.exp(-0.5 * ((minute_centers - 30) / sigma) ** 2)
        individual_above_one[sigma] = float(gaussian.sum() / gaussian.max())
    lines = [
        "# 北門分鐘權重總和放大實驗", "",
        "將預測最高的小時之原分鐘權重乘以 k，其餘五小時維持原預測。原 60 個分鐘權重總和為 1；實驗後尖峰小時的總和為 k，因而該小時預測人數也乘以 k。", "",
        "資料：封存九場北門 18–23 時、共 54 筆逐時預測及實際人數。預測檔 SHA-256：`" + EXPECTED_SHA256 + "`。沒有重新訓練模型。", "",
        "掃描 k=1.00–3.00，間隔 0.01；預測尖峰小時由預測值決定，相同最大值取較早時段。", "",
        "## 結果", "",
        "| 方法 | 小時 MAE | 小時 RMSE | 小時偏差 | 實際尖峰小時 MAE | 實際尖峰低估 | 每場總人數 MAE |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in comparison.itertuples():
        lines.append(f"| {row.method} | {row.hourly_mae:.2f} | {row.hourly_rmse:.2f} | {row.hourly_bias:.2f} | {row.actual_peak_mae:.2f} | {row.actual_peak_under:.2f} | {row.event_total_mae:.2f} |")
    lines += ["", f"全資料內最低小時 MAE：k={best_mae.scale:.2f}（{best_mae.hourly_mae:.2f}）。",
              f"全資料內最低小時 RMSE：k={best_rmse.scale:.2f}（{best_rmse.hourly_rmse:.2f}）。",
              f"全資料內最低實際尖峰小時 MAE：k={best_peak.scale:.2f}（{best_peak.actual_peak_mae:.2f}）。",
              f"固定 k={selected:.2f} 時，九場之中有 {event_worse} 場的小時 MAE 比 k=1 更高。",
              f"九場預測尖峰小時的實際／預測比值最大為 {largest_break_even:.6f}；本次 0.01 格點從 k=1.33 起，所有被放大的小時都已高估，因此再增加 k 只會提高小時 MAE、RMSE。",
              "", "## 倍率敏感度", "",
              "| k | 小時 MAE | 小時 RMSE | 實際尖峰小時 MAE | 實際尖峰低估 |",
              "|---:|---:|---:|---:|---:|",
              ]
    for scale in (1.0, 1.05, 1.06, 1.10, 1.20, 1.50, 2.0, 3.0):
        row = sweep.loc[np.isclose(sweep.scale, scale)].iloc[0]
        lines.append(f"| {scale:.2f} | {row.hourly_mae:.2f} | {row.hourly_rmse:.2f} | {row.actual_peak_mae:.2f} | {row.actual_peak_under:.2f} |")
    lines += ["", "## 解讀界線", "",
              "- 留一場選倍率：每次用其他八場的小時 MAE 選 k，再評估被留出的該場；這只是小樣本穩健性檢查。九場曾參與原模型開發，不能當成全新獨立測試。",
              "- 沒有實測分鐘抵達或候車時間，因此無法判定放大的分鐘分布或候車結果是否更接近現實。k>1 會增加總預測人數，不只是改變分鐘到達形狀。",
              "- σ=5 與 σ=20 決定分鐘形狀；同一 k 對逐時誤差的影響相同。以中心第 30 分鐘為例，單一分鐘權重超過 1 須分別到 k>" + f"{individual_above_one[5]:.2f}、k>{individual_above_one[20]:.2f}" + "；這些倍率已遠超過逐時誤差開始單調變差的界線。",
              "- 候車時間變短不是本實驗的最佳化目標；放大需求會改變排隊壓力。倍率的『最佳』會因 MAE、RMSE、尖峰風險等目標而不同。",
              "", "完整格點見 `sweep.csv`；逐場留一驗證見 `leave_one_event_out.csv`；總結見 `comparison.csv`。",
              "逐時低估、總量淨差額、實際尖峰低估為何隨倍率改變的詳細推導與逐場數據，見 [倍率增加的補充分析](effects.md)。", ""]
    (args.output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(comparison.round(2).to_string(index=False))
    print(f"best MAE={best_mae.scale:.2f}; RMSE={best_rmse.scale:.2f}; actual-peak MAE={best_peak.scale:.2f}")
    print(validation.round(2).to_string(index=False))


if __name__ == "__main__":
    main()
