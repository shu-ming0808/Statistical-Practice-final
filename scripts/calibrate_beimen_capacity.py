"""Audit baseline Beimen train-load observations and fit a simple past-only scale.

An observed numeric train load is required for empirical calibration. The
published four-level real-time crowding category is insufficient by itself to
identify a number of free spaces. Never treat simulation draws as observations.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "result/green_load_profile/calibration"
MODEL = ROOT / "result/green_load_profile/beimen_load_profile.csv"
REQUIRED = ["event_date", "direction", "departure_time", "observed_preboard_load"]
TEMPLATE = "event_date,direction,departure_time,observed_preboard_load,source_record_id\n"


def minute(value: str) -> float:
    parts = str(value).split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"Invalid time: {value}")
    return sum(float(v) * factor for v, factor in zip(parts, (60, 1, 1/60)))


def match(observed: pd.DataFrame, model: pd.DataFrame, tolerance: float) -> pd.DataFrame:
    rows = []
    for row in observed.itertuples(index=False):
        candidates = model[(model.event_date == row.event_date) &
                           (model.direction == row.direction) &
                           (model.within_decision_window)]
        if candidates.empty:
            raise ValueError(f"No model event/direction for {row.event_date} {row.direction}")
        target = minute(row.departure_time)
        offsets = candidates.departure_time.map(minute).sub(target).abs()
        nearest = candidates.loc[offsets.idxmin()]
        gap = float(offsets.min())
        if gap > tolerance:
            raise ValueError(f"No train within {tolerance:g} min: {row.event_date} {row.direction} {row.departure_time}")
        rows.append(dict(event_date=row.event_date, direction=row.direction,
            observed_departure_time=row.departure_time,
            matched_model_departure_time=nearest.departure_time,
            match_gap_minutes=gap, observed_preboard_load=float(row.observed_preboard_load),
            simulated_preboard_load=float(nearest.preboard_load_mean),
            simulated_p05=float(nearest.preboard_load_p05),
            simulated_p95=float(nearest.preboard_load_p95),
            capacity_persons=float(nearest.capacity_persons),
            source_record_id=getattr(row, "source_record_id", "")))
    matched = pd.DataFrame(rows)
    if matched.duplicated(["event_date", "direction", "matched_model_departure_time"]).any():
        raise ValueError("Multiple observations map to one model train")
    return matched


def calibrate_past_only(matched: pd.DataFrame) -> pd.DataFrame:
    """One global nonnegative scale; train on earlier observed dates only."""
    rows = []
    for date in sorted(matched.event_date.unique()):
        earlier = matched[matched.event_date < date]
        later = matched[matched.event_date == date]
        if earlier.empty or earlier.event_date.nunique() < 2:
            continue
        x = earlier.simulated_preboard_load.to_numpy(float)
        y = earlier.observed_preboard_load.to_numpy(float)
        denom = np.dot(x, x)
        if denom <= 0:
            continue
        scale = max(0.0, float(np.dot(x, y) / denom))
        for row in later.itertuples(index=False):
            corrected = min(row.capacity_persons, scale * row.simulated_preboard_load)
            rows.append(dict(event_date=date, direction=row.direction,
                departure_time=row.matched_model_departure_time,
                observed_preboard_load=row.observed_preboard_load,
                raw_preboard_load=row.simulated_preboard_load,
                calibrated_preboard_load=corrected,
                raw_free_space=row.capacity_persons-row.simulated_preboard_load,
                calibrated_free_space=row.capacity_persons-corrected,
                observed_free_space=row.capacity_persons-row.observed_preboard_load,
                fitted_scale=scale, training_events=int(earlier.event_date.nunique())))
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--observed", type=Path, help="Numeric preboarding load observed for historical baseline trains")
    parser.add_argument("--tolerance-minutes", type=float, default=2)
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "observations_template.csv").write_text(TEMPLATE, encoding="utf-8-sig")
    if not args.observed:
        report = """# 北門逐班空位：實測校準狀態

目前 **尚未完成實測校準**。公開輸出只有 OD／班表假設下的模擬載量及 300 次情境分位數，不能當成觀測載量。

北捷官方高運量車廂擁擠度 API 是須申請的即時四級資料；已查到的公開目錄沒有這八場歷史逐班數值或四級對應人數門檻。鐵客松曾列出逐車載重與到離站資料，但明訂僅限競賽用途，不能直接取來用於本研究。

可取得合法的歷史逐班數值時，依 `observations_template.csv` 填入活動日期、北門方向、離站時刻、北門下客後／上客前的實測車上人數與來源紀錄。執行 `python -X utf8 scripts/calibrate_beimen_capacity.py --observed <csv>`；工具先對齊原班表列車，檢查原推估誤差，再只用較早活動擬合一個非負縮放係數，對較晚活動評估空位 MAE。此縮放是診斷基線，不會自動修改增減班方案或宣稱模擬已驗證。

四級擁擠度只能做等級校核；若要轉成確切空位，還需官方門檻或同列車載重計數。當前 D−1 預測不能使用活動當天即時 API 值。

來源：[北捷 API 目錄](https://www.metro.taipei/cp.aspx?n=BDEB860F2BE3E249)、[API 詮釋資料](https://www-ws.gov.taipei/001/Upload/405/relfile/18288/116939/26d47117-1927-48fb-9293-3f7879ec8092.pdf)、[鐵客松資料使用限制](https://www.hackrail.tw/info/dataguide)。
"""
        (OUT / "report.md").write_text(report, encoding="utf-8")
        print("No historical numeric load observations; wrote availability audit and input template")
        return
    observed = pd.read_csv(args.observed, encoding="utf-8-sig")
    missing = set(REQUIRED) - set(observed.columns)
    if missing:
        raise ValueError(f"Missing fields: {sorted(missing)}")
    if observed.empty or observed[REQUIRED].isna().any().any():
        raise ValueError("Observed records must be nonempty and complete")
    model = pd.read_csv(MODEL, encoding="utf-8-sig")
    model = model[model.plan == "baseline"]
    matched = match(observed, model, args.tolerance_minutes)
    if (matched.observed_preboard_load < 0).any() or (matched.observed_preboard_load > matched.capacity_persons).any():
        raise ValueError("Observed load outside zero to train capacity")
    matched.to_csv(OUT / "matched_observations.csv", index=False, encoding="utf-8-sig")
    rolled = calibrate_past_only(matched)
    if rolled.empty:
        raise ValueError("Need numeric observations from at least three event dates for past-only evaluation")
    rolled.to_csv(OUT / "past_only_evaluation.csv", index=False, encoding="utf-8-sig")
    raw_mae = (rolled.observed_free_space - rolled.raw_free_space).abs().mean()
    calibrated_mae = (rolled.observed_free_space - rolled.calibrated_free_space).abs().mean()
    report = ("# 北門原班表逐班空位校準\n\n"
        f"較晚活動評估 {rolled.event_date.nunique()} 場、{len(rolled)} 班；空位 MAE 原推估 {raw_mae:.1f} 人、簡單縮放後 {calibrated_mae:.1f} 人。\n\n"
        "每場只用更早活動的實測資料擬合全域縮放。這是原班表載量校核，不是增減班方案的實測驗證。\n")
    (OUT / "report.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
