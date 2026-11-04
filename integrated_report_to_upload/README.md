# 北門分鐘權重倍率整合報告：上傳包

從 [integrated_report.md](integrated_report.md) 閱讀結論。本資料夾包含報告引用的兩張圖與三份 CSV；分析程式保存在主專案的 `result/minute_weight_integrated_report/scripts/`，避免維護兩份程式碼。

| 上傳檔案 | 對應產生程式與資料 |
| --- | --- |
| [sweep.csv](sweep.csv) | [倍率實驗](../result/minute_weight_integrated_report/scripts/experiment_minute_weight_scale.py)：掃描 `k=1.00–3.00`，每 0.01 一筆。 |
| [event_effects.csv](event_effects.csv) | [補充分析](../result/minute_weight_integrated_report/scripts/analyze_minute_weight_scale_effects.py) 的 `event_effects()`：逐場 MAE 變化。 |
| [actual_peak_breakdown.csv](actual_peak_breakdown.csv) | 同一支補充分析程式的 `underprediction_breakdown()`：預測尖峰與實際尖峰的時段、誤差對照。 |
| [hourly_metrics.png](hourly_metrics.png)、[actual_peak_metrics.png](actual_peak_metrics.png) | [繪圖程式](../result/minute_weight_integrated_report/scripts/plot_minute_weight_integrated_metrics.py)：讀取 `sweep.csv` 繪製。 |
| [integrated_report.md](integrated_report.md) | 依上述輸出整理的解讀與表格；不是由程式自動生成的報告。 |

**資料核對：**三份 CSV 與兩張 PNG 均和主專案 `result/minute_weight_integrated_report/` 的對應檔案逐位元相同。`sweep.csv` 有 201 筆倍率；小時 MAE 的最低格點為 `k=1.05`，實際尖峰小時雙向 MAE 的最低格點為 `k=1.06`。報告的五個本機檔案連結均有效。

倍率程式的上游逐時預測由 [run_forecast.py](../src/run_forecast.py) 產生。重算須另備報告所記載 SHA-256 的封存 `predictions.csv`；該輸入未包含在本上傳包。若只單獨複製此資料夾，表中的程式連結需要回到原專案才可開啟。
