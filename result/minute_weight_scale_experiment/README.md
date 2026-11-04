# 分鐘權重倍率：補充情境資料

主結論、雙向誤差與圖表見[整合報告](../minute_weight_integrated_report/integrated_report.md)。版本控制僅收錄整合報告未使用、但由同一批九場預測產生的下列補充情境表：

| 檔案 | 內容 |
| --- | --- |
| [asymmetric_costs.csv](asymmetric_costs.csv) | 假設低估成本為高估的 1、2 或 3 倍時，各指定倍率的平均非對稱誤差與資料內最低成本倍率。成本比例是情境假設，並非實測營運成本。 |
| [queue_effects.csv](queue_effects.csv) | 九場活動各自的候車模擬：倍率、分鐘形狀與列車剩餘容量的組合，共 144 筆。 |
| [queue_summary.csv](queue_summary.csv) | 上述候車情境的 16 組彙總。 |

候車數字使用固定班表、雙向各 50%、步行延遲 5 分鐘及假設的列車剩餘容量；它們是需求壓力情境，不能用來判定哪個倍率的人流預測最準。計算程式見[補充分析](../minute_weight_integrated_report/scripts/analyze_minute_weight_scale_effects.py)。重算需提供整合報告記載雜湊的封存預測 CSV。
