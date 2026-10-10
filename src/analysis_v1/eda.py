"""Descriptive EDA at the activity level; no hypothesis tests or causal claims."""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np
import pandas as pd

WEATHER = {
    "temperature_at_report_time_c": ("temperature_mean", "氣溫六點平均（°C）"),
    "wind_preceding_10min_mean_m_s": ("wind_mean", "風速六點平均（m/s）"),
    "precipitation_hour_ending_mm": ("rain_mean", "平均每小時雨量（mm）"),
    "cloud_at_report_time_tenths": ("cloud_mean", "雲量六點平均（十分量）"),
}
CORRELATION_LABELS = {
    "origin_total": "四站起站總量", "baseline_total": "四站歷史基準", "is_weekend": "週末",
    "day_of_year": "年內日序", "year_index": "年度序號", "temperature_mean": "氣溫",
    "wind_mean": "風速", "rain_mean": "數值雨量", "cloud_mean": "雲量",
}


def _shared_values(frame: pd.DataFrame, columns: list[str], keys: list[str]) -> pd.DataFrame:
    """Weather/event attributes must not silently differ among repeated station rows."""
    for column in columns:
        if frame.groupby(keys)[column].nunique(dropna=False).gt(1).any():
            raise ValueError(f"Shared EDA attribute differs within {keys}: {column}")
    return frame[keys + columns].drop_duplicates(keys)


def summarise_events(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return one row per activity and a level-aware completeness table.

    A weather mean requires all six distinct hourly observations. Trace rain
    retains its status and does not become a numeric zero or a partial mean.
    """
    if frame.empty or frame.duplicated(["event_date", "station", "hour"]).any():
        raise ValueError("EDA requires nonempty unique event/station/hour records")
    sizes = frame.groupby("event_date").size()
    if not sizes.eq(24).all():
        raise ValueError("EDA requires 24 station-hour records per activity")
    activities = _shared_values(frame, ["year", "is_weekend", "day_of_year", "year_index"], ["event_date"])
    totals = frame.groupby("event_date")[["y", "baseline_hourly_56d"]].sum(min_count=24).rename(
        columns={"y": "origin_total", "baseline_hourly_56d": "baseline_total"})
    activities = activities.merge(totals, on="event_date", validate="one_to_one")
    n = len(activities)
    completeness = []
    for column, label in [("y", "起站人次"), ("baseline_hourly_56d", "歷史基準"),
                          ("station", "車站"), ("hour", "時段"), ("is_weekend", "週末"),
                          ("day_of_year", "年內日序"), ("year_index", "年度序號")]:
        valid = frame.groupby("event_date")[column].count()
        complete = int(valid.eq(24).sum())
        completeness.append(dict(variable=column, label=label, group="核心輸入／目標",
            unit="活動（四站六時段完整）", events=n, complete_events=complete,
            incomplete_events=n-complete, expected_unique_values=n*24,
            available_unique_values=int(frame[column].notna().sum()), missing_unique_values=int(frame[column].isna().sum())))

    weather_columns = [column for column in WEATHER if column in frame]
    extra = [column for column in ("precipitation_status", "cloud_measurement_type") if column in frame]
    if weather_columns:
        hourly = _shared_values(frame, weather_columns + extra, ["event_date", "hour"])
        if not hourly.groupby("event_date").size().eq(6).all():
            raise ValueError("Weather EDA requires six distinct report bins per activity")
        for column, (name, label) in WEATHER.items():
            if column not in hourly:
                continue
            values = pd.to_numeric(hourly[column], errors="raise").copy()
            if column == "precipitation_hour_ending_mm" and "precipitation_status" in hourly:
                # Unknown/trace/cumulative-status values are not numeric hourly rain.
                values = values.where(hourly.precipitation_status.eq("observed"))
            work = hourly[["event_date"]].assign(value=values)
            aggregated = work.groupby("event_date").value.agg(["mean", "count"])
            aggregated[name] = aggregated["mean"].where(aggregated["count"].eq(6))
            activities = activities.merge(aggregated[[name]], on="event_date", validate="one_to_one")
            complete = int(aggregated["count"].eq(6).sum())
            completeness.append(dict(variable=column, label=label, group="事後實測天氣：僅 EDA",
                unit="活動（六個測站時點完整；四站不重複計數）", events=n,
                complete_events=complete, incomplete_events=n-complete, expected_unique_values=n*6,
                available_unique_values=int(values.notna().sum()), missing_unique_values=int(values.isna().sum())))
        if "precipitation_status" in hourly:
            trace = hourly.precipitation_status.eq("trace_T").groupby(hourly.event_date).sum().rename("rain_trace_bins")
            activities = activities.merge(trace, on="event_date", validate="one_to_one")

    for column, label in [("observed_has_drone_show", "實際無人機（有／無／未知）"),
                          ("observed_fireworks_seconds", "實際煙火秒數")]:
        if column not in frame:
            activities[column] = np.nan
        else:
            values = _shared_values(frame, [column], ["event_date"])
            activities = activities.merge(values, on="event_date", validate="one_to_one")
        available = int(activities[column].notna().sum())
        completeness.append(dict(variable=column, label=label, group="事後活動實況：僅 EDA",
            unit="活動（每場一筆）", events=n, complete_events=available, incomplete_events=n-available,
            expected_unique_values=n, available_unique_values=available, missing_unique_values=n-available))
    return activities.sort_values("event_date").reset_index(drop=True), pd.DataFrame(completeness)


def _font_name() -> str:
    available = {font.name for font in font_manager.fontManager.ttflist}
    for name in ("Microsoft JhengHei", "Noto Sans CJK TC", "Microsoft YaHei", "SimHei"):
        if name in available:
            return name
    return "DejaVu Sans"


def build_eda(frame: pd.DataFrame, metadata: dict, output: Path) -> dict:
    """Write three Chinese figures and BOM CSV tables; return portable paths."""
    output = Path(output)
    figures, tables = output / "figures", output / "tables"
    figures.mkdir(parents=True, exist_ok=True)
    tables.mkdir(parents=True, exist_ok=True)
    activities, missing = summarise_events(frame)
    year_counts = activities.groupby("year").size().rename("活動場數").reset_index().rename(columns={"year": "年度"})
    variables = [name for name in CORRELATION_LABELS if name in activities and activities[name].notna().sum() >= 2]
    correlation = activities[variables].corr(method="spearman", min_periods=3)
    valid = activities[variables].notna().astype(int)
    pair_counts = valid.T.dot(valid)
    stats = activities[variables].describe().T
    stats["missing_events"] = len(activities) - activities[variables].count()
    table_objects = {"eda_activity_features": activities, "eda_completeness": missing,
                     "eda_activity_counts": year_counts, "eda_descriptive_statistics": stats,
                     "eda_spearman": correlation, "eda_spearman_pair_counts": pair_counts}
    for name, value in table_objects.items():
        value.to_csv(tables / f"{name}.csv", index=name in {"eda_descriptive_statistics", "eda_spearman", "eda_spearman_pair_counts"},
                     encoding="utf-8-sig", na_rep="")

    with plt.rc_context({"font.family": _font_name(), "axes.unicode_minus": False, "font.size": 11}):
        fig, ax = plt.subplots(figsize=(9, 4.4))
        bars = ax.bar(year_counts["年度"].astype(str), year_counts["活動場數"], color="#575757", width=.65)
        ax.bar_label(bars, padding=3, fontsize=11)
        ax.set(title="各年度已舉行活動數", xlabel="年度", ylabel="活動場數")
        ax.set_ylim(0, year_counts["活動場數"].max()+1)
        ax.spines[["top", "right"]].set_visible(False)
        fig.text(.5, .015, f"共 {len(activities)} 場；四站與六小時的列數不增加獨立活動數。", ha="center", fontsize=9)
        fig.tight_layout(rect=(0, .05, 1, 1))
        fig.savefig(figures / "eda_activity_counts.png", dpi=170)
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(10, 6.7))
        positions = np.arange(len(missing))
        ax.barh(positions, missing.complete_events, color="#555555", label="該層級資料完整")
        ax.barh(positions, missing.incomplete_events, left=missing.complete_events, color="#d8d8d8", label="缺資料／未核實")
        ax.set_yticks(positions, missing.label)
        ax.invert_yaxis()
        ax.set_xlim(0, len(activities)+3.8)
        for y, row in enumerate(missing.itertuples()):
            ax.text(len(activities)+.25, y, f"{row.complete_events}/{row.events}", va="center", fontsize=10)
        fig.suptitle("資料完整度：以活動為分母", y=.99)
        ax.set(xlabel="活動場數（右側為完整場數／全部場數）")
        ax.legend(loc="lower left", bbox_to_anchor=(0, 1.01), ncol=2, frameon=False, fontsize=9)
        ax.spines[["top", "right"]].set_visible(False)
        fig.text(.01, .015, "核心須四站六時段完整；天氣須六個測站時點完整（不重複計四站）；節目每場一筆。\n微量雨 T 保留為有雨狀態，不填數值零。天氣與實際節目僅供事後描述。", fontsize=9)
        fig.tight_layout(rect=(0, .08, 1, .94))
        fig.savefig(figures / "eda_completeness.png", dpi=170)
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(9, 8.1))
        cmap = plt.get_cmap("RdBu_r").copy()
        cmap.set_bad("#eeeeee")
        im = ax.imshow(correlation.to_numpy(), vmin=-1, vmax=1, cmap=cmap)
        labels = [CORRELATION_LABELS[name] for name in variables]
        ax.set_xticks(range(len(labels)), labels, rotation=40, ha="right")
        ax.set_yticks(range(len(labels)), labels)
        for i in range(len(labels)):
            for j in range(len(labels)):
                value = correlation.iloc[i, j]
                text = "—" if pd.isna(value) else f"{value:.2f}"
                ax.text(j, i, text, ha="center", va="center", fontsize=9,
                        color="white" if pd.notna(value) and abs(value) > .65 else "black")
        ax.set_title("活動層級 Spearman 相關：每場一列", pad=13)
        fig.colorbar(im, ax=ax, fraction=.045, pad=.04, label="Spearman 相關係數")
        positive_pairs = pair_counts.to_numpy()[pair_counts.to_numpy() > 0]
        n_text = f"{positive_pairs.min()}–{positive_pairs.max()}" if len(positive_pairs) else "0"
        fig.text(.01, .015, f"配對完整活動數依變數而異（{n_text} 場），詳見配對樣本數表；不做 p 值或因果推論。\n天氣須六點完整才計平均；四站共用天氣只計一次。此圖含事後資訊，不是預測輸入清單。", fontsize=9)
        fig.tight_layout(rect=(0, .08, 1, 1))
        fig.savefig(figures / "eda_spearman.png", dpi=170)
        plt.close(fig)

    return {"activity_count": len(activities), "year_count": int(activities.year.nunique()),
            "figures": {name: f"figures/{name}.png" for name in ("eda_activity_counts", "eda_completeness", "eda_spearman")},
            "tables": {name: f"tables/{name}.csv" for name in table_objects},
            "evaluation_status": metadata.get("audit", {}).get("evaluation_status", "retrospective"),
            "notes": ["Descriptive statistics only; no p-values or causal interpretation.",
                      "Weather deduplicated by activity/hour before requiring six valid observations.",
                      "Pairwise correlation sample sizes reported separately; unknowns and trace rainfall are not zero.",
                      "Observed weather and actual programs are EDA-only and excluded from prediction features."]}
