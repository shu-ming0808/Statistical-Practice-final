# 北門逐班列車載量：OD 模擬讀值

沿用已發布的 8 場綠線調度模型、原班表與 λ＝1 方案，以及原本獨立的 300 組評估情境。只增加載量讀值，不重新選方案、不改變人流與路徑假設。

- `beimen_load_profile.csv`：每場、每套班表、每個方向、每班北門離站時刻的平均與 P05／P95 載量。
- `reconciliation.csv`：重播的總候車人分鐘與已發布結果逐場核對。
- `audit.json`：輸入、程式與載量 CSV 雜湊；逐場／方案／方向的完整預期班次集合；相同亂數種子與限制。

到站車上人數 `arrival_load` 減北門下車人數 `alighting`，得到下客後／上客前 `preboard_load`；可上車空間 `remaining_capacity = 2220 - preboard_load`。再加北門上車人數 `boarded`，得到離站載量 `departure_load`；`queue_left` 為此班開走後已到月台但尚未上車者。

**這些是 OD、路徑與班表假設下的模擬值，並非列車實測。P05–P95 是模擬情境分位數，不是實際載量的信賴區間。**各欄分位數分別計算，不能直接把分位數相加驗證守恆；守恆已在每個原始情境逐班檢查。受保護的 18:00 前、23:00 後列車仍參與完整重播；展示決策時段時請使用 `within_decision_window`。

重跑：`python -X utf8 src/green_load_trace.py`。需要本機私有快照；不會重新產生 OD 或覆寫原調度輸出。
