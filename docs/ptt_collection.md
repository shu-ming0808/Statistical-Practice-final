# PTT 大稻埕文章與文字雲

## 執行

在本專案 PowerShell 執行：

```powershell
.\.venv\Scripts\python.exe -X utf8 src\ptt_web_collect.py
```

預設查詢 Gossiping、MRT、WomenTalk、Taipei 四個看板，關鍵字「大稻埕」，日期 **2026/7/15–8/15（臺北時間，含首尾）**。程式取得 PTT 公開 HTML，不需要登入或開發者 Token。每次請求至少間隔2秒；每個看板／關鍵字最多20頁、整次最多讀取500篇文章頁，採集時間上限15分鐘。先到達任一上限即停止，Ctrl+C 可中止並保存已取得資料。

新電腦安裝依賴：`uv sync --group notebook --extra social --extra web`。也可執行 `powershell.exe -NoProfile -File .\scripts\run_ptt_web.ps1`。

自訂看板、關鍵字與日期：

```powershell
.\.venv\Scripts\python.exe -X utf8 src\ptt_web_collect.py --board Gossiping --board MRT --query 大稻埕 --start-date 2026-07-15 --end-date 2026-08-15
```

`--plan` 只顯示設定；`--purge` 只清除本工具已到期原文及衍生報表。看板名稱或路徑無效時停止，不接受任意網址。

## 搜尋及日期判斷

查詢使用各看板原有的標題搜尋，例如 [Gossiping 的大稻埕搜尋](https://www.ptt.cc/bbs/Gossiping/search?q=%E5%A4%A7%E7%A8%BB%E5%9F%95)、[MRT 的大稻埕搜尋](https://www.ptt.cc/bbs/MRT/search?q=%E5%A4%A7%E7%A8%BB%E5%9F%95)。循頁面「上頁」連結往前讀，不猜測頁碼；同一看板與文章 ID 只保存一份。

`M.<timestamp>.A.<id>.html` 內的時間只用來減少無須讀取的文章請求，預篩窗口比目標前後各寬一天。最終年份、日期及時間使用文章頁「時間」欄，依臺北時區解讀。列表的 `7/15` 和正文提及的活動日期均不當作發文日期。時間欄缺失則標待核對，不從網址補成已驗證日期。

僅正文提到大稻埕、但標題沒寫的文章可能不在搜尋結果中；其他看板、刪文和網站索引限制也會造成漏抓。即使已讀到搜尋末頁，仍不能稱為全站完整語料。不同文章可能轉貼同一則新聞，文章篇數不等於不同人的獨立意見。

## 內容處理

- 保存文章標題、正文、文章網址、看板、發文時間、採集時間、關鍵字、相關性與來源標記。
- 排除作者欄位、推噓文、發信站／IP 等頁面資訊及簽名區；不另外取得帳號個資。
- 標題或正文含大稻埕與煙火／夏日節等事件詞，初篩為 `include`。僅談大稻埕地區者標 `review`，待人工核對；日期不明或文字不完整也標待核對。
- 文字雲只使用日期在區間內、文字完整且 `include` 的文章。每篇每個詞最多計一次，排除搜尋詞、停用詞、網址、Email 與電話式字串。
- 未觀測到的日期在每日 CSV 留空，不解讀為沒有討論。沒有可用文字時不製造文字雲。
- 這是活動期間的回顧性探索；不能直接當作活動前已知因子，文字出現也不是因果證據。

## 輸出

每次建立新的 `<run_id>`，PTT 與 Threads 分開保存：

```text
data/original_data/ptt/web_sample_2026-07-15_2026-08-15/<run_id>/
  posts.jsonl      正式觀測資料
  posts.csv        Excel 檢視版（已處理公式字首）
  manifest.json    查詢、分頁、略過原因、採集上限、到期日

results/ptt/web_sample_2026-07-15_2026-08-15/<run_id>/
  words.csv
  daily_observed_counts.csv
  summary.json
  wordcloud.png   有可用詞彙才產生，1600×1200，白底圓形、明體與柔和四色
```

人工核對 JSONL 的 `relevance` 後，可以重作報表：

```powershell
.\.venv\Scripts\python.exe -X utf8 src\ptt_web_report.py --input "完整路徑\posts.jsonl" --output "對應報表資料夾" --start-date 2026-07-15 --end-date 2026-08-15
```

PTT 入口使用共用中文分詞／出圖模組，但固定來源為 `ptt_public_web`，不混用或改標 Threads 文章。圖內與 CSV 均標示 PTT 及樣本限制。

## 限制、資安與保存

只向 `https://www.ptt.cc` 的指定公開路徑請求，不跟隨外部網址、不停用 TLS 驗證、不讀密碼或舊 Token。開始時讀取 robots.txt；404 會記錄為未提供檔案。遇到禁止抓取、年齡確認、登入或驗證限制即停止相關步驟並記錄；不自動確認年齡、不寫入繞過確認的 Cookie。遇到429／403等限制停止整次採集，不重試或輪替代理。

原文與結果已排除 Git，取得後最多保存90天。清除在執行收集器或 `--purge` 時進行，不是背景排程；未執行期間需自行在到期日前清理。錯誤只記固定分類，不輸出原始網路例外。

## 實際檢查

2026-09-30首輪執行共17次請求，讀取7頁搜尋結果與9篇文章，保存9篇：Gossiping 8篇、MRT 1篇；8篇初篩為煙火相關，1篇捷運路線新聞待核對。所有保存文章都具完整發文時間，已產生 PTT 文字雲。WomenTalk、Taipei 的本次標題搜尋未取得區間內文章，不能推論那些看板沒有相關討論。

本機測試涵蓋完整日期、臺北時間邊界、作者與推文排除、重複文章、分頁範圍、年齡確認停止、跨平台資料隔離及90天保存範圍。
