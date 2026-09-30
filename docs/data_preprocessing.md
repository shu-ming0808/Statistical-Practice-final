# 資料前處理與分析表規格

## 1. 先保留長表，再依研究問題彙總

資料來源為[臺北捷運各站分時進出量統計](https://data.gov.tw/dataset/128506)。每列代表原檔日期、時段、起站、迄站的一組 OD 人次。不要把數百萬筆資料手動展開成「每站一欄」，也不要先把所有活動加總成一列。

本次使用專案 `data/original_data/` 的本機 CSV，沒有重新下載或改寫原檔。來源月份及筆數見 `results/preprocessing/summary.json`；每份來源 SHA-256、重複列及資料型別檢查見 `results/preprocessing/monthly/`。資料是否確實來自官方、站名變更及官方後續修訂，仍需比對原始發布資訊；通過格式檢查不等於完成來源真實性認證。

| 輸出 | 每列代表什麼 | 唯一鍵 | 用途 |
|---|---|---|---|
| `data/interim/clean_od/YYYYMM.parquet` | 一個時段、一組起迄站 | service_date + source_hour + origin_station + destination_station | 可重用的乾淨 OD 底表 |
| `data/processed/station_hourly.parquet` | 一個來源時段、一個站名標籤 | service_date + source_hour + station | 全部已下載月份的車站背景流量 |
| `regression_station_hourly.csv/.parquet` | 一場活動、一個時段、一個站名 | event_date + hour_start + station | 每小時流量回歸 |
| `regression_event_station.csv/.parquet` | 一場活動、一個站名、一個分析視窗 | event_date + station + window_start_hour + window_end_hour_exclusive | 活動前聲量與指定時段總人次回歸 |
| `flow_edges_hourly.parquet` | 一場活動、一個時段、一條非零 OD 邊 | event_date + hour_start + source + target | 有向加權人流圖 |
| `flow_nodes_hourly.csv/.parquet` | 一場活動、一個時段、一個節點 | event_date + hour_start + node | 節點流量、連接數與集中程度 |
| `beimen_event_hourly.csv/.parquet` | 上述逐時回歸表中的北門站 | event_date + hour_start | 先檢查專題核心車站，方便繪圖 |

`station_labels` 是原檔站名索引；尚非官方實體車站維度。主要大表採 Parquet，可用 DuckDB、pandas 或 R 讀取；較小分析表另外輸出 CSV。這避免把超過一億列原始 OD 再重複灌進 MySQL 分析表。

## 2. 時間、站名與資料品質

- `service_date` 保留原檔「日期」；名稱不代表已重建捷運營運日。`source_hour` 是原檔「時段」。`hour_start` 為兩者組合，按臺北本地時間解讀，不另做 UTC 位移。
- 尚未取得能確認「時段」以進站或出站刷卡時間分箱的官方字典。因此 `origin_count` 定義為同一來源時段按起站加總；`destination_count_same_source_bin` 定義為同時段按迄站加總。**後者不能直接宣稱是該小時實際出閘人數**，也不能據此估旅行時間。
- 原檔存在起站／迄站集合不同、帶字母前綴的站名。只去掉欄位頭尾空白，不擅自合併 `O頭前庄` 等標籤；需要官方站碼和歷史更名表後再建立實體站映射。
- 日期必須有效且屬於檔名月份；時段須為整數 0–23；人次須為非負整數。無效值會讓該次執行停止，不直接刪掉。
- 同一 OD 唯一鍵出現相同人次時只保留一次，並記錄移除筆數及首筆來源列號；若人次衝突則停止，不能逕自相加或任選一筆。
- 原始 0 人次保留。來源沒有的時段／站名，衍生回歸表放 `NULL`，不補成 0。
- 每個來源時段檢查已觀測起站集合 × 迄站集合的 OD 配對。某站少了應有配對時，該站彙總值設為 `NULL`。這是相對於該時段已觀測集合的完整性檢查，**無法證明整個站或整個時段沒有被來源漏報**。
- 來源缺少 02:00–04:00 時，逐時回歸表仍保留這三列但人次為空；這些列不得當成觀測零人次訓練。
- `source_row` 是資料列序號，從 1 開始、不含 CSV 標題；`source_month` 加每月來源雜湊可追溯原檔。

## 3. 回歸表如何使用

目前預設總量視窗是活動日 **18:00 ≤ t < 次日 00:00**，也就是 18、19、20、21、22、23 六個小時。逐時表保留活動當日全部 24 個時段。跨日可改成 `--start-hour 12 --end-hour 26`，涵蓋活動日 12:00 到隔日 01:59；跨日列仍以原活動的 `event_date` 串接活動及聲量。24–47 的偏移代表隔日；程式會在逐時表多保留需要的隔日時段。

總量只有在視窗每個小時都完整時才產生，否則為 `NULL`。`observed_origin_hours`、`expected_hours`、`origin_window_complete` 可供篩選，避免把五小時總量拿來和六小時比較。

### 目標與背景量

先從北門站一場一列開始：`origin_count_window` 為指定視窗的 OD 起站人次總量，`peak_origin_hour_count` 為該視窗內最大逐時值。逐時模型則用 `origin_count` 作目標。這些是捷運旅次人次，**不是活動現場總參與人數**。

`baseline_origin_mean_28d` 取同一站、同一時段、前 7／14／21／28 天中已下載且完整的非活動日平均。只使用活動之前的日期，排除事件表中所有原訂活動日；跨日設定另排除前一活動的跨日尾段。`baseline_n_days` 記錄可用天數，為 0 時平均為空。視窗背景量為逐時背景平均的總和；`baseline_min_days` 記錄最少可用比較天數。它未控制國定假日、其他大型活動、天氣和疫情，也不是因果效果估計。部分月份缺上月資料，應先檢查比較日不足的場次，再補下載前一月，不能把缺值設為 0。

`origin_excess_vs_baseline` 是觀測量減歷史背景量的描述性差值，可能為負；它使用當天目標，**不能當作預測同一目標的輸入變數**。

### 活動、聲量與其他變數

已保留星期、月份、年份與週末旗標；週末不等於國定假日或補班日。活動狀態、無人機及時長來自 MySQL 事件表，取消和延期也保留。

`observed_event_status`、`observed_has_drone_show`、實際時長屬於事後整理資訊。若要做活動前一天預測，需要另外保存公告時間及預測截止時間，只用當時已知資訊；目前不能自動將這些欄位視為合法的事前特徵。

Google Trends 以活動日連接前 1／3／7 天平均；目前未成功取得的數值保留 `NULL`，並設 `trends_missing`。每場分開查詢的 0–100 值不是搜尋次數，也尚未跨場校準，故 `trends_cross_event_calibrated=false`。不能直接把這些值當成跨年可比的搜尋量。詳見 `mysql/GOOGLE_TRENDS_README.md`。

天氣、官方假日、公告時點、活動預算仍待另取來源，沒有捏造欄位數值。後續天氣按 `hour_start` 及測站關聯，假日按實際曆日關聯，活動／聲量按 `event_date` 關聯，勿只憑欄位名稱都叫 date 就混用。

### 估計時避免的問題

1. 全年度只有有限場活動，將同一場拆成上百站、幾十小時不會增加獨立活動樣本數。先選核心車站，控制解釋變數數量，再考慮多站模型。
2. 用按活動或按年份的時間切分驗證，不把同一活動的列隨機拆入訓練與測試；缺值填補、標準化及特徵挑選只在訓練集估計。
3. 計數目標可先比較 Poisson／負二項模型與簡單線性回歸，實際選擇依分散程度、殘差和外部驗證；前處理階段不先對所有欄位標準化或刪除尖峰。
4. 最近 1、3、7 天平均通常相關很高。小樣本先比較單一聲量特徵模型，不必同時把三個平均及大量交互作用都放入。
5. 每小時 OD 不能還原真正的前 30 分鐘刷卡序列。日後若做逐筆模擬，須另存模擬標記、到達假設及隨機種子。

## 4. 流向表如何使用

`flow_edges_hourly` 的 `source`、`target` 為方向，`weight` 為 OD 人次。零權重邊省略以縮小圖；原始與乾淨 OD 表仍保留零值。`is_self_loop` 表示同站進出，保留在總人次中。節點 `out_strength` 與 `in_strength` 包含自迴圈；degree 計算只計正人次、排除自迴圈。

可以先做北門出發的前十大目的站、活動前後方向比例、同站同時段一般日對照、站點強度與流向集中度，再考慮群集。`net_out_strength` 是同一來源分箱下起迄總量之差，不是車站內排隊人數或現場人數淨變化。

**OD 邊不是實體軌道路段。** 例如北門到淡水的 OD 不代表一條直達軌道；估列車區間載客量或調度效果，需要路網、可行路徑、轉乘、旅行時間及列車容量資料，再做路徑分配。

## 5. MySQL 與 PK 怎麼串

原有 `original_data` 不改寫。新建 `analysis_station_label`、`analysis_event_station_hourly`、`analysis_event_station_window`，分別存站名索引、逐時分析量及視窗總量。完整 OD 使用本機 Parquet。這是原始層／分析層的邏輯分離，目前仍是同一台電腦與同一 MySQL 實例，**尚未建置正式交易系統與分析系統的實體隔離**。

日期無法唯一識別逐時 OD，因為同一天有很多時段和 OD 組合。事件表目前每日最多一個整合事件，`event_date` 可當該表 PK；人流表則用多個欄位一起構成 PK。將來同日多個獨立活動時需改 `event_id`，不能沿用每日一筆假設。

MySQL 已提供兩個即時關聯 view：`v_regression_event_station` 和 `v_regression_station_hourly`。聲量將來寫入原表後，view 查詢會跟著更新；CSV／Parquet 是執行當下的快照，需重跑才能更新。

```sql
SELECT event_date, station, origin_count_window,
       observed_event_status, interest_1d, interest_3d_avg, interest_7d_avg
FROM taipei_metro_analysis.v_regression_event_station
WHERE station = '北門'
  AND window_start_hour = 18 AND window_end_hour_exclusive = 24
ORDER BY event_date;
```

聲量 join 同時限定 `query_text='大稻埕煙火'` 和 `geo_code='TW'`，避免多關鍵字、多地區把同一人流列放大。新 MySQL 分析表採唯一鍵 upsert，重新執行不重複累加；各張表的資料更新放在同一交易，`source_run_sha256` 用來對應此次輸出。不同視窗的總量可並存；因此查詢總量時要指定視窗。逐時基準值會依目前視窗對跨日排除的設定更新，這些分析表代表最新處理版本，若要保存模型實驗版本須額外建立 run 維度。

新增的連線程式用既有本機 MySQL login path，不把密碼放入檔案或命令列；停用 LOCAL INFILE，字串以十六進位 SQL 值傳送，無遠端上傳。正式環境仍應另建最小權限服務帳號、遠端 TLS 及備份政策，既有 login path 不代表已完成權限隔離。

## 6. 執行與檔案整理

在專案根目錄執行：

```powershell
uv sync
uv run python src/main.py --mysql
# 改分析視窗：隔天 02:00 不包含在內
uv run python src/main.py --start-hour 12 --end-hour 26 --mysql
# 不加 --mysql 時只讀取 MySQL 活動／聲量，衍生結果寫本機檔案
uv run python -m unittest discover -s tests -v
uv run python -m unittest discover -s mysql -p 'test_*.py' -v
```

來源及乾淨輸出雜湊一致時重用快取；修改來源或快取損毀會重建。`--force` 強制重建。小時總表與 graph 均從同一份乾淨 OD 衍生，驗證起站彙總＝迄站彙總＝OD 總人次；測試另涵蓋跨日、取消場次、排除未來與活動日基準、重複列及缺資料不補 0。

專案固定在 `C:/Users/User.DESKTOP-4RV84M1/Desktop/統計/碩二上/統計實務`。舊目錄檔案已複製並逐檔核對 SHA-256；重複 202607 原檔只保留目標內既有一份。原本 notebook、參考文獻與 Git 變更保留。年表 PDF 在 `results/pdf/`，舊 PDF 排版與成本查詢暫存放 `.local/legacy_tmp/`，映射清單在 `.local/workspace_migration.json`。舊目錄保留備份，往後從此新專案執行。

技術參考：[DuckDB CSV 匯入](https://duckdb.org/docs/current/data/csv/overview)、[Parquet 讀寫](https://duckdb.org/docs/current/data/parquet/overview)。
