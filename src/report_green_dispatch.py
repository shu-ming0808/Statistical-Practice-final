"""Render audited aggregate Green-line results and publish an allowlisted bundle."""
from __future__ import annotations
import json
from pathlib import Path
import shutil
import hashlib
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parents[1]
START='<!-- green-dispatch:start -->';END='<!-- green-dispatch:end -->'


def records(df): return json.loads(df.to_json(orient='records',force_ascii=False))


def report(out,cfg,update_readme=False):
    comparison=pd.read_csv(out/'comparison.csv')
    stations=pd.read_csv(out/'station_impacts.csv')
    timetable=pd.read_csv(out/'timetable.csv').fillna('')
    audit=json.loads((out/'audit.json').read_text('utf-8'))
    main=comparison[comparison.waiting_weight==cfg['reference_weight']].copy()
    totals=[]
    for weight,g in comparison.groupby('waiting_weight'):
        totals.append(dict(waiting_weight=float(weight),events=len(g),added=int(g.added.sum()),
            removed=int(g.removed.sum()),retimed=int(g.retimed.sum()),
            baseline_services=int(g.baseline_services.sum()),optimized_services=int(g.optimized_services.sum()),
            baseline_operating_twd=float(g.baseline_operating_twd.sum()),
            optimized_operating_twd=float(g.optimized_operating_twd.sum()),
            operating_change_twd=float(g.operating_change_twd.sum()),
            baseline_wait_person_minutes=float(g.baseline_wait_person_minutes.sum()),
            optimized_wait_person_minutes=float(g.optimized_wait_person_minutes.sum()),
            baseline_mean_wait=float(g.baseline_wait_person_minutes.sum()/g.modeled_passengers.sum()),
            optimized_mean_wait=float(g.optimized_wait_person_minutes.sum()/g.modeled_passengers.sum()),
            wait_change_percent=float((g.optimized_wait_person_minutes.sum()/g.baseline_wait_person_minutes.sum()-1)*100),
            evaluation_total_cost_change=float(g.evaluation_total_cost_change.sum())))
    pd.DataFrame(totals).to_csv(out/'summary.csv',index=False,encoding='utf-8-sig')
    figdir=out/'figures';figdir.mkdir(exist_ok=True)
    plt.rcParams.update({'font.family':'Microsoft JhengHei','axes.unicode_minus':False,'figure.dpi':160})
    x=np.arange(len(main));width=.36
    fig,axes=plt.subplots(1,2,figsize=(13,5),layout='constrained')
    for ax,left,right,title,unit in [(axes[0],'baseline_operating_twd','optimized_operating_twd','可變營運成本（萬元）',10000),
                                     (axes[1],'baseline_mean_wait','optimized_mean_wait','模型涵蓋旅次平均候車（分鐘）',1)]:
        ax.bar(x-width/2,main[left]/unit,width,label='原班表',color='#b6b7b5')
        ax.bar(x+width/2,main[right]/unit,width,label='增減／移時',color='#52766f')
        ax.set_xticks(x,[d[2:] for d in main.event_date],rotation=45,ha='right');ax.set_title(title)
        ax.spines[['top','right']].set_visible(False)
    handles,labels=axes[0].get_legend_handles_labels()
    fig.legend(handles,labels,loc='lower center',bbox_to_anchor=(.5,-.06),ncol=2,frameon=False)
    fig.suptitle('松山新店線 18:00–23:00｜λ=1｜獨立 300 次模擬評估',fontsize=13)
    fig.savefig(figdir/'cost_wait.png',bbox_inches='tight');plt.close(fig)
    focus=stations[(stations.waiting_weight==cfg['reference_weight'])&(stations.station!='其他旅次')]
    fig,axes=plt.subplots(2,2,figsize=(12,7),layout='constrained')
    for ax,(station,g) in zip(axes.ravel(),focus.groupby('station',sort=False)):
        g=g.sort_values('event_date');ax.plot(g.event_date,g.baseline_mean_wait,'o--',color='#999',label='原班表')
        ax.plot(g.event_date,g.optimized_mean_wait,'o-',color='#52766f',label='增減／移時')
        ax.set_title(station+'：涉及綠線旅次');ax.tick_params(axis='x',rotation=45,labelsize=8)
        ax.set_ylabel('模型涵蓋候車（分）');ax.spines[['top','right']].set_visible(False)
    handles,labels=axes[0,0].get_legend_handles_labels()
    fig.legend(handles,labels,loc='lower center',bbox_to_anchor=(.5,-.04),ncol=2,frameon=False)
    fig.suptitle('四站相關旅次的綠線候車與可推導紅／橘線轉乘等待',fontsize=13)
    fig.savefig(figdir/'station_impacts.png',bbox_inches='tight');plt.close(fig)
    case='2024-08-10' if '2024-08-10' in set(main.event_date) else main.event_date.iloc[0]
    rows=timetable[(timetable.event_date==case)&(timetable.waiting_weight==cfg['reference_weight'])]
    fig,ax=plt.subplots(figsize=(12,4),layout='constrained')
    for i,d in enumerate(['往松山','往新店']):
        g=rows[rows.direction==d]
        for shift,col,label,color in [(0,'old_beimen','原班表','#a9aaa9'),(.28,'new_beimen','新班表','#52766f')]:
            tt=[int(v[:2])*60+int(v[3:5]) for v in g[col] if v]
            ax.vlines(tt,i+shift-.08,i+shift+.08,color=color,label=label if i==0 else None)
    ax.set_yticks([.14,1.14],['往松山','往新店']);ax.set_xticks(range(1080,1381,30),[f'{t//60:02}:{t%60:02}' for t in range(1080,1381,30)])
    ax.set_xlim(1075,1385);ax.set_title(case+' 北門離站時刻｜上下對照原班表與新班表');ax.legend(frameon=False,loc='center right')
    ax.spines[['top','right']].set_visible(False);fig.savefig(figdir/'timetable.png');plt.close(fig)
    assumptions=[
        '只調整北門離站落在 18:00（含）至 23:00（不含）的松山—新店完整服務；其他時段與紅／橘線班表固定。',
        '四站採封存年度四站 XGBoost 預測；各場誤差只抽較早活動。其他 OD 採前 56 日內最多六個同星期、非活動日均值。歷史發布時點未全面核實。',
        '用官方站間時間推導最短時間路徑；轉乘步行與進站至月台各 5 分鐘均為研究假設。OD 沒有實際轉乘路徑，不能視為觀測。',
        '綠線逐站載客守恆，六車容量上限 2,220 人，先下後上，同分鐘旅次比例分配座位。來源 OD 的進站小時以分鐘分布轉為月台到達代理。',
        '紅／橘線連接班表由四站官方時刻表與站間時間平移推導，班次不變；可計算錯過下一班的等待，但未重建紅／橘線車上容量。其他路線候車未完整重建。',
        '四站影響按出發站優先歸屬；出發站不在四站時再按目的站歸屬，避免重複相加。不經綠線者另列為未調整旅次。',
        '17:00–18:00 用一般日 OD 暖機；需求輸入到 23:00，23:00 後固定班次繼續疏運至共同終點 02:00。沒有 P95 10 分鐘／5% 硬門檻，也沒有任意期末 60 分鐘懲罰。',
        '平均候車的分母為 17:00–23:00 進站且路徑涉及綠線的模型旅次，包含暖機與一般背景需求；不是北門單站或散場尖峰平均，不能直接與舊版北門剩餘容量情境的平均比較。',
        '同向新增或改動班次相隔至少 120 秒是研究假設，不是已確認的號誌安全極限；原有分鐘四捨五入的相鄰班次保留並稽核。',
        '保留、減班與移時只計單程服務的可變成本；新增服務另外計一次假設全程空車回送。128.05 元／列車公里仍為待核實代理；既有人員、折舊等固定成本不因減班扣除。',
        '17:00 前已在途的乘客、全線實際選路、車庫出入、備車、司機與折返接續尚未完整建模；不是可直接執行的營運命令。'
    ]
    sources=[
        {'title':'北捷各站時刻表與站間時間（原始來源索引）','url':'https://github.com/shu-ming0808/Statistical-Practice-final/blob/main/docs/metro_operations.md'},
        {'title':'北捷站間行駛與停靠時間','url':'https://data.taipei/dataset/detail?id=513e97fe-6a98-4a0d-b7dc-11122c8638d4'},
        {'title':'北捷分時 OD 原始資料','url':'https://data.taipei/dataset/detail?id=63f31c7e-7fc3-418b-bd82-b95158755b4d'},
        {'title':'勞動部：2026 年最低時薪 196 元（時間價值換算是本研究假設）','url':'https://www.mol.gov.tw/1607/28690/2282/2284/2290/7121/'},
        {'title':'北捷官方最短距離與沿路線里程加總','url':'https://github.com/shu-ming0808/Statistical-Practice-final/blob/main/result/metro_distances/report.md'},
        {'title':'Li et al. (2023)：營運成本、候車與班表整合最佳化','url':'https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0285932'},
        {'title':'Santanam et al. (2021)：特殊活動需求預測與列車模擬','url':'https://arxiv.org/abs/2106.05359'}
    ]
    bundle=dict(schema_version=1,generated_at=audit['generated_at'],decision_window='18:00–23:00',
        line='松山新店線',reference_weight=cfg['reference_weight'],waiting_weights=cfg['waiting_weights'],
        assumptions=assumptions,sources=sources,summary=totals,comparison=records(comparison),
        station_impacts=records(stations),timetable=records(timetable),
        training_draws=cfg['training_draws'],evaluation_draws=cfg['evaluation_draws'],
        no_wait_threshold=True,not_global_optimum=True)
    (out/'public_summary.json').write_text(json.dumps(bundle,ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n',encoding='utf-8')
    chosen=next(t for t in totals if t['waiting_weight']==cfg['reference_weight'])
    lines=['# 18:00–23:00 松山新店線增減班與移時比較','',
        '**這是列車載客守恆的回顧模擬與啟發式班表搜尋，不是已證明全域最佳或現場調度命令。**','',
        '## 方法','',r'$$\min_{x\in\mathcal P}\;\Delta C_{\rm op}(x)+\lambda\frac{196}{60}\frac1S\sum_s W_s(x).$$','',
        '- 原班表包含在候選中；允許增班、減班、前後移時。只選訓練情境中完成疏運的候選。',
        '- 每場 200 次訓練情境，其中前 12 次搜尋並初篩每個 λ 前 10 名（另保留原班表），再用完整 200 次重新評分；獨立 300 次只評估，不反過來選方案。這是新亂數，不是 300 場新的實測活動。',
        '- 移時依最終時刻一對一配對（最多 5 分鐘）；同一移時服務不再被重複算成增班及減班。',
        '- P95 為合併模擬乘客的近似候車分位數，使用分鐘分箱；同組同時到達的背景旅次，其轉乘前等待以加權平均合併，未保留這部分組內變異。不是「情境 P95 超過某門檻的機率」。',
        '- 沿用凍結人流模型，不重新調參。2024-07-17 因沒有較早的同口徑預測誤差，未納入這次八場比較。',
        '',r'$$\Delta C_{\rm op}=128.05\times18.77\times(2N_{\rm add}-N_{\rm remove}).$$','',
        '新增服務按完整松山—新店一趟加假設一趟空車回送計費；減班只扣可避免的單程服務費。班數是服務趟次，不是需要的實體車組數。λ=0.5、1、2 表示不同時間價值偏好，不能跨 λ 比較加權總成本來選偏好。',
        '', '## 獨立模擬評估（λ=1）','',
        '| 活動 | 原班數→新班數 | 增／減／移時 | 可變成本差（元） | 平均候車原→新（分） | 總等待變化 |',
        '|---|---:|---:|---:|---|---:|']
    for r in main.itertuples():
        lines.append(f'| {r.event_date} | {r.baseline_services}→{r.optimized_services} | +{r.added}／−{r.removed}／{r.retimed} | {r.operating_change_twd:+,.0f} | {r.baseline_mean_wait:.2f}→{r.optimized_mean_wait:.2f} | {r.wait_change_percent:+.1f}% |')
    lines+=['',f'合計增 {chosen["added"]} 班、減 {chosen["removed"]} 班、移時 {chosen["retimed"]} 班；可變營運成本差 {chosen["operating_change_twd"]:+,.0f} 元，總候車人分鐘變化 {chosen["wait_change_percent"]:+.1f}%。負的成本差才是本模型下的可變支出節省；不是實測節省。','',
        '![成本與等待](figures/cost_wait.png)','', '![四站轉乘影響](figures/station_impacts.png)','', '![班表對照](figures/timetable.png)','',
        '## 假設與界線','']+['- '+a for a in assumptions]+['','## 重現與輸出','',
        '```powershell','uv sync --inexact --group analysis --extra transit',
        'uv run --group analysis --extra transit python -X utf8 src/run_green_dispatch.py --reuse --update-readme',
        'uv run --group analysis python -X utf8 -m app.export_dispatch','```','',
        '[逐場成本等待](comparison.csv)／[四站影響](station_impacts.csv)／[班表變更](timetable.csv)／[權重比較](summary.csv)／[稽核](audit.json)','',
        '## 參考資料','']+[f'- [{s["title"]}]({s["url"]})' for s in sources]
    lines+=['','論文支持需求、候車與營運成本整合的研究方向；本案的分鐘化、搜尋、成本及步行參數是自行實作與假設，不是重現原論文最優解。','']
    weight_lines=['## 八場合計與權重敏感度','','| λ | 增／減／移時 | 可變營運成本原→新（元） | 平均候車原→新（分） | 總等待變化 |','|---:|---:|---:|---:|---:|']
    for t in totals:
        weight_lines.append(f'| {t["waiting_weight"]:g} | +{t["added"]}／−{t["removed"]}／{t["retimed"]} | {t["baseline_operating_twd"]:,.0f}→{t["optimized_operating_twd"]:,.0f} | {t["baseline_mean_wait"]:.2f}→{t["optimized_mean_wait"]:.2f} | {t["wait_change_percent"]:+.1f}% |')
    weight_lines += ['', '候車改善與營運成本須一起看；若減班未被選中，表示在目前需求、成本與時間估值下，搜尋較偏好保留／增加服務，不代表程式禁止減班。`paired_mc_se` 只衡量固定需求假設下的模擬誤差，不包含路徑、預測或成本參數的不確定性。', '']
    at=lines.index('## 假設與界線'); lines[at:at]=weight_lines
    transfer_lines=['## 四站相關旅次的候車拆分（λ=1，八場合併）','','| 相關車站 | 綠線平均候車原→新（分） | 可推導紅／橘線段候車原→新（分） |','|---|---:|---:|']
    for station,g in focus.groupby('station',sort=False):
        n=g.passengers.sum()
        bg=float((g.passengers*g.baseline_green_wait).sum()/n)
        ng=float((g.passengers*g.optimized_green_wait).sum()/n)
        br=float((g.passengers*(g.baseline_mean_wait-g.baseline_green_wait)).sum()/n)
        nr=float((g.passengers*(g.optimized_mean_wait-g.optimized_green_wait)).sum()/n)
        transfer_lines.append(f'| {station} | {bg:.2f}→{ng:.2f} | {br:.2f}→{nr:.2f} |')
    transfer_lines+=['','按模擬旅次人數加權。紅／橘線段包含綠線前的候車與可推導後續轉乘；前段班表固定，後續可趕上的班次仍可能改變。這不代表已模擬紅／橘線全線容量或月台排隊。','']
    at=lines.index('## 假設與界線');lines[at:at]=transfer_lines
    (out/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    if update_readme:
        table='\n'.join(lines[lines.index('| 活動 | 原班數→新班數 | 增／減／移時 | 可變成本差（元） | 平均候車原→新（分） | 總等待變化 |'):lines.index('![成本與等待](figures/cost_wait.png)')])
        content='\n'.join(['## 18:00–23:00 增減班與移時（目前版本）','',
            '只調整松山新店線。以原四站預測與過去一般日 OD 推估沿線載客及轉乘，紅／橘線班表不變。沒有無來源的候車合格門檻。平均涵蓋沿線背景旅次，不是北門散場尖峰平均，不能與舊版單站平均直接比較。','',
            r'$$\min_{x\in\mathcal P}\Delta C_{\rm op}(x)+\lambda(196/60)\,S^{-1}\sum_s W_s(x).$$','',
            '原班表也是候選；200 次訓練選擇、300 次獨立模擬評估，並非新活動實測驗證。下表 λ=1，成本包含新增服務的假設空車回送，減班只扣可避免的單程服務成本。', '',table,
            '![增減班成本與等待](result/green_dispatch/figures/cost_wait.png)','',
            '![原四站轉乘影響](result/green_dispatch/figures/station_impacts.png)','',
            '**解讀界線：**這是過去活動配現行班表的情境比較；其他路線的車上容量、備車、司機與折返未完整納入。OD 最短路徑與步行時間是假設，不能稱為實際轉乘軌跡。','',
            '[完整方法與權重比較](result/green_dispatch/report.md)／[逐班增減及起站時刻](result/green_dispatch/timetable.csv)／[四站數據](result/green_dispatch/station_impacts.csv)',''])
        path=ROOT/'README.md';text=path.read_text('utf-8')
        block=START+'\n'+content+'\n'+END
        if START in text:
            a=text.index(START);b=text.index(END,a)+len(END);text=text[:a]+block+text[b:]
        else:
            index=text.index('## 快速開始');text=text[:index]+block+'\n\n'+text[index:]
        path.write_text(text,encoding='utf-8')
