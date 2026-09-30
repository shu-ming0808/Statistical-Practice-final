# 參考論文

[返回專案首頁](../README.md)

以下整理各文獻能支持的專題工作；「用途」是本專題的應用建議，不代表已完成重現或已證明適用於大稻埕煙火。

### 活動人流與車站背景：組員提供的三篇

- **He, Y., Zhao, Y., & Tsui, K. L. (2018).** [*An Analysis of Factors Influencing Metro Station Ridership: Insights from Taipei Metro*](https://arxiv.org/abs/1904.01280). IEEE ITSC, 1598–1603；arXiv 預印本於 2019 年上傳。[免費全文](https://arxiv.org/pdf/1904.01280)

  用途：以北捷車站周邊土地使用、公車可及性、人口與路網指標解釋進出站量，可參考其變數設計，建立各站平常人流的背景模型，再比較煙火日增加的需求。研究使用 2015 年一週、108 站的資料，適合提供車站背景特徵，不是大型活動的即時預測模型。

- **Fernandes, P., Santos, L., Bandeira, J. M., & Macedo, E. (2025).** [*Multiple linear regression of metro ridership in the context of football events: a case study of Metro de Lisboa*](https://doi.org/10.1016/j.trpro.2025.04.020). Transportation Research Procedia, 86, 151–158.

  用途：參考活動日與一般日的比較方法，以及把活動特性、日期、時間和天氣轉成解釋變數的做法；研究觀察到的散場尖峰也提示應對齊「活動結束時間」分析。須注意，其多元迴歸應變數是球場到場人數，捷運流量另做分析；模型包含賽後結果，事前預測不能照搬這些事後變數（原文 §2.2、§3.2）。

- **Santanam, T., Trasatti, A., Van Hentenryck, P., & Zhang, H. (2021).** [*Public Transit for Special Events: Ridership Prediction and Train Optimization*](https://arxiv.org/abs/2106.05359). arXiv:2106.05359.[免費全文](https://arxiv.org/pdf/2106.05359)

  用途：最接近本專題的整體架構：用 AFC 刷卡資料區分活動與日常需求，以迴歸／隨機森林預測人流，再用模擬比較班次調整後的等待時間與車廂負載。原文還以旅次資料推估乘客搭上的列車；目前公開逐時 OD 無法直接重現該部分，應把等待時間與上車列車標示為模擬推估，並另外處理免費戶外活動缺少票券到場人數的問題。

### 人流網路、排隊與調度

- **Xu, Q., Mao, B., & Bai, Y. (2016).** [*Network structure of subway passenger flows*](https://doi.org/10.1088/1742-5468/2016/03/033404). Journal of Statistical Mechanics: Theory and Experiment, 2016(3), 033404.[免費全文](https://arxiv.org/pdf/1601.06340)

  用途：分析車站進出量、區間流量、時段變化與階層式人流群集，可用來比較煙火日和一般日的主要流向，辨識需求集中車站。原文區分外部進出站量與相鄰站區間流量；我們可先建立以 OD 人次為邊權重的有向圖，但若要估列車區間負載，仍需加入路徑分配、轉乘及旅行時間假設（原文 §2.2、§3）。

- **Larson, R. C., & Odoni, A. R. (1981).** [*Urban Operations Research*, Chapter 4: Introduction to Queueing Theory and Its Applications](https://web.mit.edu/urban_or_book/www/book/chapter4/contents4.html). Prentice-Hall.[M/M/m 多服務者模型 §4.6.2](https://web.mit.edu/urban_or_book/www/book/chapter4/4.6.2.html)；[時變排隊分析 §4.11](https://web.mit.edu/urban_or_book/www/book/chapter4/4.11.html)

  用途：提供 M/M/c（教材以 m 表示服務者數）的理論基準，用於閘門等平行服務設施的簡化分析；其 Poisson 到達、指數服務時間等假設需明確列出。煙火散場需求快速變動時，分時套用穩態公式可能失效。列車一次載走一批乘客，月臺應採時變到達率、班距與剩餘容量的批次服務模擬，不能把列車數直接當成 c。

- **Blanco, V., Conde, E., Hinojosa, Y., & Puerto, J. (2020).** [*An optimization model for line planning and timetabling in automated urban metro subway networks. A case study*](https://doi.org/10.1016/j.omega.2019.102165). Omega, 92, 102165.[作者預印本](https://arxiv.org/abs/1903.08617)；[機構典藏](https://idus.us.es/items/7832dbb3-f66b-482f-a8e5-85cfec3fe340)

  用途：將 OD、時變需求與特殊活動造成的成批到達納入路線和時刻表最佳化，考慮班距、容量與短線折返，可結合資工的最佳化與啟發式搜尋。專題可先限縮一條路線，比較固定班距、提早加開及候車量觸發加開；最小安全班距、可用車輛、折返與列車剩餘容量須另取得資料或列為情境參數。

### 即時資料工程與活動事前資訊

- **Akidau, T., et al. (2015).** [*The Dataflow Model: A Practical Approach to Balancing Correctness, Latency, and Cost in Massive-Scale, Unbounded, Out-of-Order Data Processing*](https://doi.org/10.14778/2824032.2824076). Proceedings of the VLDB Endowment, 8(12), 1792–1803.[免費全文](https://www.vldb.org/pvldb/vol8/p1792-Akidau.pdf)

  用途：以事件時間、處理時間、視窗、watermark 與延遲修正設計未來逐筆刷卡管線，滾動更新最近 30 分鐘人流，並注入延遲、亂序與重送測試系統。watermark 不會自動去除重複事件，仍需穩定 event_id、唯一鍵及冪等寫入；目前逐時 OD 僅能生成假設情境，不能當成真實逐筆資料。

- **Tu, Q., Geng, G., & Zhang, Q. (2023).** [*Multi-Step Subway Passenger Flow Prediction under Large Events Using Website Data*](https://doi.org/10.17559/TV-20230227000384). Tehnički vjesnik, 30(5), 1585–1593.[出版頁與免費全文](https://hrcak.srce.hr/en/307745)

  用途：從網站擷取活動相關資訊，結合歷史人流做大型活動下的多步車站預測，可參考「活動文本特徵＋人流時間序列」架構，納入閉幕、無人機、演唱會、延期等已公告資訊。它支持事件資訊值得測試，但未證明 Google Trends、Threads 或 IG 對大稻埕有效；深度學習也需依可取得的活動樣本數評估。

- **West, R. (2020).** [*Calibration of Google Trends Time Series*](https://doi.org/10.1145/3340531.3412075). ACM CIKM.[免費全文](https://arxiv.org/abs/2007.13861)；[G-TAB 程式碼](https://github.com/epfl-dlab/GoogleTrendsAnchorBank)

  用途：透過共同參考詞與 anchor bank 校準不同 Google Trends 查詢的尺度，降低分次查詢不可直接比較及低搜尋量整數化的問題。適合處理不同活動年度與活動名稱的相對搜尋熱度；校準後仍不是絕對搜尋次數，更不是到場人數，不能直接用比例換算捷運乘客。
