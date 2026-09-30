# Threads 網頁取樣與文字雲

更新：2026-09-30。Python＋Playwright 操作正常可見的搜尋頁，不需開發者 Token，不呼叫私人 API；資料是公開搜尋樣本，非全平台聲量。

## 直接執行

在專案資料夾開啟 PowerShell：

```powershell
.\.venv\Scripts\python.exe -X utf8 src\threads_web_collect.py
```

程式開啟獨立 Microsoft Edge 視窗 → 在該視窗自行登入 Threads → 回終端按 Enter → 自動搜尋、捲動、去重與輸出。密碼和驗證碼只在 Threads／Instagram 正常登入畫面輸入。

登入等待期間程式會持續同步瀏覽器事件。按 Enter 後重新確認已返回 Threads 首頁或搜尋頁的分頁，再開啟查詢；因此登入流程新增或替換分頁時，不會繼續使用已關閉的舊分頁。這個分頁檢查不代表登入權限已驗證；搜尋頁仍會檢查登入／驗證限制。

預設 **2026/7/15–8/15，含首尾共32天，臺北時間**；查詢「大稻埕 煙火」「大稻埕」，依使用者要求只抓「最相關」，已移除「最近」及兩種排序輪流採集。跨關鍵字依貼文 ID 去重。

每個關鍵字的最相關搜尋最多採集15分鐘；兩個詞約最多30分鐘，另加登入、導航及產圖時間。每個詞最多捲200次、間隔4秒，全次最多保存2000篇。先碰到時間、捲動、筆數或平台限制時停止；連續90秒沒新卡片也會提早停止。這些都是上限，不是保證執行時間或保證取得筆數。終端每10輪回報已讀篇數及期間內篇數，Ctrl+C 可中止並保留已取得的樣本。

需要更久可調整，例如每個關鍵字的最相關搜尋30分鐘：

```powershell
.\.venv\Scripts\python.exe -X utf8 src\threads_web_collect.py --max-minutes 30 --max-scrolls 500 --max-posts 5000
```

Python 與 Codex 是不同瀏覽器登入環境。程式不複製瀏覽器資料、不匯出 Cookie、不持久保存登入狀態；每次執行需在新視窗登入。

依賴已在本機安裝；新電腦先執行：

```powershell
uv sync --group notebook --extra social --extra web
```

預設使用既有 Edge。可加 `--browser chrome` 使用已安裝 Chrome；若要 `--browser chromium`，先執行 `.\.venv\Scripts\python.exe -m playwright install chromium`。

也可用啟動腳本：

```powershell
powershell.exe -NoProfile -File .\scripts\run_threads_web.ps1
```

## 日期如何處理

程式讀取貼文可見 DOM 的 `<time datetime>`，轉成臺北時間後逐筆檢查。只有日期標籤時保留日精度；不推算相對時間、不補年份。日期不明的貼文標待審，日期確定在範圍外者不存原文。

2026-09-30已看到網頁「指定日期之後／之前」選單，但自動套用未驗證成功。另以日期搜尋文字做單次驗證，結果仍混入期間外貼文，因此未把該方式加入程式。**預設只保證本機日期過濾**。延長採集有助增加搜尋樣本，但不能保證涵蓋所有舊貼文。

實際保留邊界是臺北時間 `2026-07-15 00:00:00` 至 `2026-08-16 00:00:00` 之前；以發文時間判斷，不以正文提及的活動日期判斷。每次搜尋的 `date_counts` 記錄看過的不同貼文中「期間內／期間外／日期不明」數量，`observed_date_min`、`observed_date_max` 記錄實際看過的日期範圍，方便辨別漏抓和日期解析問題。

可讓程式在每個查詢暫停，由你先設定網站日期篩選，再按 Enter：

```powershell
.\.venv\Scripts\python.exe -X utf8 src\threads_web_collect.py --manual-filters
```

手動設定記為「使用者設定，未自動驗證」，程式仍逐筆檢查時間。

## 輸出

每次執行獨立建立 `<run_id>`，不覆蓋上次資料：

```text
data/original_data/threads/web_sample_2026-07-15_2026-08-15/<run_id>/
  posts.jsonl       正式原始觀測資料，可人工複核 relevance
  posts.csv         Excel 檢視版；危險公式字首加單引號
  manifest.json     查詢、排序、期間內外篇數、停止原因、時間與保存期限

results/threads/web_sample_2026-07-15_2026-08-15/<run_id>/
  words.csv                    每個詞出現於幾篇貼文
  daily_observed_counts.csv    每日通過清理的實際觀測樣本數
  summary.json                 樣本、排除原因及限制
  wordcloud.png                1600×1200（4:3），白底圓形、明體與柔和四色，有可用文字才生成
```

主要欄位：`post_id`、`permalink`、`text`、`published_at_utc`、`published_date_taipei`、`date_precision`、`query_text`、`matched_queries`、`topics`、`text_complete`、`relevance`、`relevance_reason`、`author_type`、`collected_at_utc`、`source_type`。

同一貼文依網址 shortcode 去重，跨關鍵字只算一次並保留查詢來源。`author_type` 預設 unknown，人工核對後才標官方／商業／一般貼文。只讀可見正文，不下載媒體或留言串，不讀私訊。現今互動數不是活動當時值，因此不當作事前特徵。

## 遇到 browser_error

表示瀏覽器操作失敗，**不是那段期間沒有貼文**。新版終端會顯示「停止步驟」與「原因」；`manifest.json` 的 `error` 只記錄步驟、固定錯誤分類及允許的網路錯誤代碼，不保存原始例外或登入網址。

- `search_navigation`＋`timeout`：搜尋頁在45秒內未完成載入，尚不能判定搜尋結果；程式不會反覆重試。
- `navigation_interrupted`：頁面跳轉被中斷，可能尚在登入跳轉中。
- `browser_or_page_closed`：程式操作時分頁或瀏覽器連線已失效，不等同使用者主動關閉視窗。
- `login_not_ready`：按 Enter 後30秒內仍未返回 Threads 首頁或搜尋頁。

目前已補上登入期間事件同步、重新取得分頁、較長導航等待與安全錯誤分類。2026-09-30的 `20260930T032540Z_14da527f` 執行保存100篇，其中74篇符合文字雲納入條件；這仍是可見搜尋樣本，不代表全站完整採集。

## 清理與人工複核

- 正文含「大稻埕」及「煙火／花火／夏日節」等事件詞，才初步標 `include`；規則初篩仍須抽查。
- 只掛主題、文字截斷、卡片界線不明或日期不明，標 `review`，不自動納入文字雲。
- 含「大稻埕」但未明確提到煙火／夏日節的內容，改標 `review`、原因 `place_context_requires_review`，保留供人工判斷交通、天氣、市集等活動背景，不直接認定是煙火討論。
- 明確無關者標 `exclude`。查詢詞不保證每篇結果相關。
- 詞雲排除網址、@帳號、Email、電話式字串和停用詞。人名仍可能出現在正文，分享前應查看詞頻 CSV，用額外停用詞排除。
- 字體大小按包含該詞的不同貼文篇數；每篇每詞最多一次。沒觀測到的日期留空，不當作聲量為零；空資料不畫假文字雲。

人工修改 `posts.jsonl` 的 `relevance`、`relevance_reason` 後，可重新產圖。編輯 CSV 不會回寫 JSONL；不要將未核對日期、截斷文字標為已核對。

```powershell
.\.venv\Scripts\python.exe -X utf8 src\threads_web_report.py --input "完整路徑\posts.jsonl" --output "對應報表資料夾" --start-date 2026-07-15 --end-date 2026-08-15
```

額外停用詞等用法請執行 `src\threads_web_report.py --help`。

## 資安、保存與研究限制

- 不讀舊 Token、資料庫密碼，不打印登入網址、Cookie、原始瀏覽器錯誤訊息。原文及結果資料夾已排除 Git；原文不要公開上傳。
- 取得後最多保存90天。每次執行會清除本工具到期原文及衍生結果；`--purge` 可主動清理。這不是背景排程，未執行期間須自行在到期日前清除；刪除申請沿用既有政策。
- 遇到登入失效、限流或驗證提示停止，不繞過登入／CAPTCHA／安全警告，不代理輪替或反覆重試。
- 全段日期的文字雲屬活動期間回顧性探索，含事後心得；不能當成每場活動事前已知特徵或因果證據。
- 搜尋排序、個人化、索引、刪文與網頁改版會影響涵蓋；沒有新卡片不代表全站抓完。

## 檢查與舊版移除

```powershell
# 不開瀏覽器，查看計畫
.\.venv\Scripts\python.exe -X utf8 src\threads_web_collect.py --plan
# 合成資料測試，不登入 Threads
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p 'test_threads_web*.py' -v
# 僅清除本工具的到期資料
.\.venv\Scripts\python.exe -X utf8 src\threads_web_collect.py --purge
```

依使用者要求，舊 API 收集器、API 文字雲程式、3個 Token／啟動腳本與2個舊測試已直接刪除，未備份。舊 SQLite、已抓取資料及其他專題資料保留；新版不混入官方 API 紀錄。Meta 說明網站不在這次刪除範圍。

驗證：檢查真實可見搜尋頁的正文、時間與卡片結構，使用合成網頁測試解析、邊界、清理及出圖。另在本機 Edge 以本地提供的合成頁面測試等待 Enter 期間開新分頁、原分頁關閉及分頁恢復；不使用真實帳號。測試覆蓋搜尋逾時、敏感網址不寫入紀錄、延遲載入超過三輪後仍可取得貼文、日期統計與時間／閒置上限。

參考：[Playwright Python 瀏覽器](https://playwright.dev/python/docs/browsers)、[BrowserContext](https://playwright.dev/python/docs/api/class-browsercontext)。
