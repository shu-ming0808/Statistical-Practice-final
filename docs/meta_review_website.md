# Meta 審查說明頁與圖示

發布日期：2026-09-29。Sites 回報部署成功，訪客權限為公開。

## 2026-09-30：公開搜尋審查準備

目前Token已驗證通過，實測2026/7/15–8/15搜尋「大稻埕」為空結果，尚未取得研究貼文。使用者後台顯示使用案例與測試完成，但應用程式檢閱未完成。Token包含權限名稱與應用程式獲准公開搜尋，是不同條件。官方明定：未批准 `threads_keyword_search` 前，只搜尋授權者自己的貼文；這是目前最有力的解釋，空結果本身仍不能當成審查狀態的證明。

### 從現有主控板送審

1. 展開「商家和存取驗證」，查看帳號實際被要求的項目。若要求組織證明，需使用真實資料；不能預設個人一定能略過，也不能捏造公司。
2. 點「應用程式檢閱」，確認必要權限 `threads_basic` 和 `threads_keyword_search`。本工具沒有發文功能，不申請 `threads_content_publish`。
3. 在基本設定填入下表的公開隱私、刪除、條款網址、1024×1024圖示與聯絡信箱，依後台補齊欄位。
4. 為關鍵字搜尋填寫實際用途、測試操作方式、螢幕錄影。示範本人授權、實際API查詢、結果與詞頻分析流程。審查前若只能顯示自己帳號貼文，要明確標記限制，不把人工假資料說成抓到的公開內容。影片不能暴露Token或App Secret。
5. 提供審查員能操作的測試入口與步驟。**目前程式是本機Python，公告網站只有靜態政策；還沒有讓外部審查員登入操作的搜尋介面。** 錄影與政策頁不能自動取代可測試程式；需依審查要求補上實際可重現的操作方式。
6. 依後台完成送審／發布要求。發布本身不等於搜尋權限批准，獲准後還要實測公開搜尋。

官方明列的權限用途與管理Threads社群媒體、顯示使用者搜尋的公開內容有關，未保證純學術文字雲研究必定合格。必須如實寫研究性質，不能為了過審聲称不存在的品牌管理、自動回覆功能。此文件沒有代使用者送審。

### 用途說明草稿（按審查欄位調整）

> 本應用程式為統計實務課程的探索性研究工具。研究操作者使用本人的Threads帳號授權，以「大稻埕」關鍵字查詢2026年7月15日至8月15日的公開貼文，再篩選煙火／花火／夏日節相關內容，進行詞頻統計及文字雲分析，以找出活動期間常見的討論主題，作為後續研究候選變數的參考。文字雲不被當成因果證據。
>
> 申請threads_keyword_search，是因為研究需要搜尋符合關鍵字與日期範圍的公開討論；目前未獲批准時只能搜尋授權者本人的貼文，無法取得所需樣本。threads_basic用於必要的使用者授權與API存取。這是學術探索用途，非代客管理品牌或自動發布／回覆貼文。
>
> 本機僅保存貼文ID、文字、發文時間、原文連結及擷取紀錄，不保存作者帳號、不下載媒體或完整留言串。原始資料取得後最多保存90天，刪除申請依公開說明處理；研究輸出以彙總詞頻及文字雲呈現，不將作者與個別乘客或刷卡紀錄連結。資料只依Meta核准的範圍使用。目前為本機研究原型，可測試入口與操作流程須在送審前補齊。

官方依據：[關鍵字搜尋](https://developers.facebook.com/docs/threads/keyword-search/)、[權限允許用途](https://developers.facebook.com/docs/permissions/#threads_keyword_search)、[送審指南](https://developers.facebook.com/docs/app-review/submission-guide/)、[存取驗證](https://developers.facebook.com/docs/development/release/access-verification/)。

## 可填入 Meta 的欄位

| 欄位 | 值 |
|---|---|
| 隱私政策網址 | https://dadaocheng-research-notices.sammyjimmy08080811.chatgpt.site/privacy.html |
| 用戶資料刪除：選「資料刪除指示網址」 | https://dadaocheng-research-notices.sammyjimmy08080811.chatgpt.site/data-deletion.html |
| 服務條款網址 | https://dadaocheng-research-notices.sammyjimmy08080811.chatgpt.site/terms.html |
| 專題首頁 | https://dadaocheng-research-notices.sammyjimmy08080811.chatgpt.site/ |
| App icon | `web/meta-review/dist/assets/app-icon-1024.png` |

圖示是 1024 × 1024、不透明 RGB PNG，43,319 bytes。原始 SVG 一併保留。

## 目前完成／未完成

已完成公開說明頁與圖示、UTF-8／內部連結檢查、桌面頁面與窄螢幕排版檢查。聯絡信箱與期限由使用者確認：sammy151516@gmail.com、原始貼文最多保存 90 天、可核對的刪除申請 30 天內處理。

截至2026-09-29發布當時，沒有代使用者填寫或提交Meta審查，也未取得Threads Token、下載貼文或產生文字雲。2026-09-30進度見上節。這個網站是靜態說明頁，不是可供操作的研究應用程式、OAuth回呼或自動資料刪除callback。現在已實作程式執行時的90天清理，但沒有常駐排程；保存期限及刪除申請仍由負責人履行。

API 測試可以在送審前進行；`data: []` 本身無法判定權限原因。建議先測 `me?fields=id,username` 確認基本授權，再檢查自己的貼文與公開搜尋範圍。

## 原始碼與部署紀錄

網站原始碼：`web/meta-review/`；其 Git 為獨立的 Sites 來源版本紀錄，未推送研究專案既有變更到 GitHub。

- Project ID：`appgprj_6abbb4d15700819196d9fe2cfec7588e`
- Source commit：`6f849cf728f6e07b960d6ec45cbf628cdfd981af`
- Version ID：`appgprj_6abbb4d15700819196d9fe2cfec7588e~appgver_60f960d3e90c81919328f44324588684`
- Deployment ID：`appgdep_6abbb5faf438819186b47480a686a46a`
- Audience：public

本機 Sites 技能文件提供的部署輔助腳本路徑不存在，搜尋快取也未找到；因此以 `.local/publish-notices.mjs` 完成等效的獨立來源提交、推送、遠端 SHA 核對與 tar 包裝，再經 Sites 原生工具儲存版本和部署。憑證只經隱藏標準輸入及子程序環境變數使用，不落盤、不放進命令列參數。重新部署需取得新短期憑證。

部署套件僅包含 `.openai/hosting.json` 與 `dist/`；無研究資料或 API 密鑰。
