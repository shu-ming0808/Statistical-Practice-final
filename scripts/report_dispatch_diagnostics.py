"""Render cost/wait diagnostics from completed simulations; no plan reselection."""
import json
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.export_dispatch_diagnostics import SOURCES

def report():
    out=ROOT/'result/green_dispatch_diagnostics'; figdir=out/'figures';figdir.mkdir(exist_ok=True)
    points=pd.read_csv(out/'tradeoff_points.csv'); drivers=pd.read_csv(out/'cost_drivers.csv')
    cancellations=pd.read_csv(out/'selected_removals.csv'); audit=json.loads((out/'audit.json').read_text('utf-8'))
    loads=pd.read_csv(ROOT/'result/green_load_profile/beimen_load_profile.csv')
    events=sorted(points.event_date.unique())
    if len(events)!=8 or len(points)!=48: raise ValueError('Render only the complete eight-event diagnostic run')
    plt.rcParams.update({'font.family':['Microsoft JhengHei','DejaVu Sans'],'axes.unicode_minus':False,'figure.dpi':150})
    labels={'baseline':'原班表','current_0.5':'λ=0.5','current_1':'λ=1','current_2':'λ=2','fixed_8':'8 分配置','fixed_10':'10 分配置'}
    colors={'baseline':'#56565c','current_0.5':'#a0b2ad','current_1':'#557a70','current_2':'#25493f','fixed_8':'#b07c52','fixed_10':'#956744'}
    fig,axes=plt.subplots(4,2,figsize=(13,14),layout='constrained')
    for ax,event in zip(axes.ravel(),events):
        rows=points[points.event_date==event]
        for r in rows.itertuples():
            probe=r.kind=='reduction_probe'; color=colors[r.scenario_id]
            ax.scatter(r.operating_change_twd/10000,r.mean_wait,s=60,facecolors='white' if probe else color,edgecolors=color,linewidths=1.5,marker='D' if probe else 'o')
            ax.annotate(labels[r.scenario_id],(r.operating_change_twd/10000,r.mean_wait),xytext=(6,7),textcoords='offset points',fontsize=9,color=color)
        ax.axvline(0,color='#c5c5c5',lw=.8);ax.set_title(event,loc='left',fontsize=12)
        ax.set_xlim(-19,94);ax.set_ylim(2,11);ax.set_xlabel('相對原班表的可變支出差（萬元）');ax.set_ylabel('平均候車（分鐘）')
        ax.grid(axis='y',color='#eeeeee');ax.spines[['top','right']].set_visible(False)
    fig.suptitle('同一需求下的成本與候車取捨｜每場獨立 300 次模擬',fontsize=16)
    fig.supxlabel('空心菱形＝額外減班試算，未採用；各點是有限方案比較，不是全域最優前緣。',fontsize=11)
    fig.savefig(figdir/'cost_wait_tradeoff.png',bbox_inches='tight');plt.close(fig)

    case='2024-08-10'; subset=loads[(loads.event_date==case)&loads.within_decision_window]
    fig,axes=plt.subplots(1,2,figsize=(13,4.7),layout='constrained')
    for ax,direction in zip(axes,['往松山','往新店']):
        for plan,color,label in [('baseline','#858589','原班表'),('selected_lambda1','#52786e','增減／移時 λ=1')]:
            rows=subset[(subset.plan==plan)&(subset.direction==direction)].copy()
            rows['minute']=rows.departure_time.map(lambda v:sum(float(x)*u for x,u in zip(v.split(':'),(60,1,1/60))))
            rows=rows.sort_values('minute');x=rows.minute.to_numpy()
            ax.fill_between(x,rows.remaining_capacity_p05.to_numpy()/2220*100,rows.remaining_capacity_p95.to_numpy()/2220*100,color=color,alpha=.15)
            ax.plot(x,rows.remaining_capacity_mean/2220*100,color=color,label=label,lw=1.4)
        ax.axhline(50,color='#b07c52',ls='--',lw=1,label='舊固定 50% 示例')
        ax.set_xticks(range(1080,1381,60),[f'{h}:00' for h in range(18,24)])
        ax.set_ylim(0,100);ax.set_xlim(1080,1380);ax.set_ylabel('下客後剩餘容量（%）');ax.set_xlabel('北門離站時刻');ax.set_title(direction)
        ax.grid(axis='y',color='#eee');ax.spines[['top','right']].set_visible(False)
    axes[0].legend(frameon=False,fontsize=9,loc='lower left')
    fig.suptitle(case+'｜OD 推估逐班剩餘空間，並非實測載量',fontsize=14)
    fig.supxlabel('線＝300 次情境平均；色帶＝5–95 百分位，非信賴區間。原班表與 λ=1 使用相同需求情境。',fontsize=10)
    fig.savefig(figdir/'beimen_remaining_capacity.png',bbox_inches='tight');plt.close(fig)

    n=float(drivers.modeled_passengers.sum()); background=float(drivers.background_passengers.sum()/n)
    total=[]
    for scenario,g in points.groupby('scenario_id',sort=False):
        total.append(dict(scenario_id=scenario,label=labels[scenario],services=int(g.services.sum()),added=int(g.added.sum()),removed=int(g.removed.sum()),retimed=int(g.retimed.sum()),operating_change_twd=float(g.operating_change_twd.sum()),mean_wait=float(g.wait_person_minutes.sum()/n),wait_person_minutes=float(g.wait_person_minutes.sum()),unserved_mean_sum=float(g.unserved_mean.sum())))
    totals=pd.DataFrame(total);totals.to_csv(out/'tradeoff_summary.csv',index=False,encoding='utf-8-sig',lineterminator='\n')
    table=['| 方案 | 增／減／移時 | 相對原表支出差（元） | 平均候車（分） |','|---|---:|---:|---:|']
    for r in totals.itertuples(): table.append(f'| {r.label} | +{r.added}／−{r.removed}／{r.retimed} | {r.operating_change_twd:+,.0f} | {r.mean_wait:.3f} |')
    threshold=float(drivers.cancel_break_even_person_minutes_lambda1.iloc[0]); saving=float(drivers.single_cancel_saving_twd.iloc[0]); costadd=float(drivers.single_add_cost_twd.iloc[0])
    selected=cancellations[cancellations.waiting_weight==1]
    break_min,break_max=selected.break_even_waiting_weight.min(),selected.break_even_waiting_weight.max()
    lines=['# 為何增班卻沒有減班：成本、候車與逐班載量診斷','',
      '**結論：減班方案能完成本次模擬疏運，但在目前時間價值與需求下，額外候車代價高於省下的營運費。另有候選初篩限制，不能證明所有減班組合都較差。**','',
      '## 1. 直接檢查減班，而不只看最後入選方案','',
      '沿用原八場、18:00–23:00 決策窗、相同較早活動誤差與一般日 OD，以及原 200 訓練／300 評估亂數。新增固定 8／10 分鐘配置作診斷，窗外服務仍保護。方案的班距也檢查與窗外列車的間隔。這些是預先指定的額外試算，沒有按評估結果替換已採用班表。','',
      f'逐班取消共檢查 {int(drivers.cancellation_proposals.sum())} 個原班表服務，先以原 12 個訓練提案情境初篩，各 λ 保留前三名的聯集，再用完整 200 情境選擇，最後獨立 300 情境評估。沒有窮舉同時取消多班及所有重排組合。','']+table+['',
      '平均候車以同批模型旅次人數加權；涵蓋全綠線背景與可推導轉乘，不是北門散場單站平均。增／減／移時按原專案逐班配對，不能將時段熱圖的正負格直接加總成費用。','',
      '![成本與等待取捨](figures/cost_wait_tradeoff.png)','',
      '## 2. 三個原因','',
      f'1. **時間成本門檻。** 取消一班節省 {saving:,.2f} 元；新增一班含假設全程空車回送為 {costadd:,.2f} 元。λ=1 時，取消造成的額外等待必須低於 {threshold:,.2f} 人分鐘才划算。例如 500 人各多等 2 分鐘就是 1,000 人分鐘，不是只多等 2 分鐘。',
      f'2. **背景需求占比。** 八場合計模型旅次中，「其他旅次」占 {background:.1%}。目標會計算一般乘客等待，因此加密服務的效益不只出現在散場。',
      '3. **原搜尋先按偏好初篩。** 原完整訓練候選都沒有取消服務；稀疏方案可能在 12 情境初篩階段被排除。這次強制留下 8／10 分鐘減班試算，才能看到省費用與增加等待的另一側。','',
      r'$$\Delta J=-c_{\mathrm{cancel}}+\lambda v\,\Delta W,\qquad v=196/60. $$','',
      r'$$\Delta W>0\ \Longrightarrow\ \lambda_{\mathrm{break-even}}=\frac{c_{\mathrm{cancel}}}{v\Delta W}. $$','',
      f'各場經完整訓練選出的單班取消方案，其損益兩平 λ 約 {break_min:.3f}–{break_max:.3f}，低於現行最小值 0.5。這是依既定成本的局部診斷，不是建議事後把 λ 調低到一定能減班；λ 應反映明確的服務與支出偏好。',
      '所有被評估減班方案的未完成旅次、最大載量與班距見 CSV；模擬中能疏運，不代表已有實際備車、司機與折返可行性。','',
      '## 3. 固定 50% 可以怎麼改','',
      '**舊候車回放仍用固定剩餘空間情境，新版全線調度已用 OD 動態載客。** 剩餘空間 50% 與兩方向各 50% 是不同參數；新版用歷史 OD 起迄與假設路徑分配方向，再逐站先下後上。','',
      r'$$L_{k,s}^{\mathrm{pre}}=L_{k,s}^{\mathrm{arr}}-A_{k,s},\quad R_{k,s}=C-L_{k,s}^{\mathrm{pre}},\quad B_{k,s}=\min\{Q_{k,s},R_{k,s}\},\quad L_{k,s}^{\mathrm{dep}}=L_{k,s}^{\mathrm{pre}}+B_{k,s}. $$','',
      '`Larr` 為抵站車上人數、A 為下車人數、R 為可用容量、Q 為本班上車前隊伍、B 為上車人數；C=2,220。到下一站仍保留車上目的站結構，取消前車後的人潮會累積到後車，不能把每班空位重設為一半。','',
      '本次只為既有模擬增加讀值，重播原班表與 λ=1 各 300 次情境，逐場核對總候車與已發布結果一致，沒有重新選方案。線是逐班平均，帶是情境分位數；不能稱為實測或實際載量的信賴區間。','',
      '![OD逐班剩餘容量](figures/beimen_remaining_capacity.png)','',
      '**目前資料能做到：**小時 OD＋假設路徑＋官方時刻與站間時間，推估每列車沿線上下車。**目前不能做到：**唯一還原每個人搭哪班、準確分鐘到達或實際列車載量。若補刷卡進出時間、列車運行紀錄與載重感測，才可校準乘車機率與載量。現有方向比例與選路也仍須做敏感度分析。','',
      '文獻支持網路載客與乘客—列車配對的方向，本專案沒有宣稱重現文獻的校準精度。北捷 TIMES 證明載重感測與 AFC 校準具有實務依據，但該簡報未提供本案歷史綠線逐列車原始載量。','',
      '## 4. 下一步與資料界線','',
      '- 先核實每列車公里成本、回送里程與服務偏好，再決定要接受多少等待交換多少支出，不能按期待的減班數反推權重。',
      '- 保留減班候選作研究比較；若要改採用機制，應將省費用候選正式納入訓練選擇，並另留未反覆檢視的活動驗證。',
      '- 補全車組、折返、司機及回送軌道占用；本版不提供可直接執行的營運命令。',
      '- 全線普通日需求、最短時間路徑、分鐘分布及步行仍是模型假設；歷史資料發布時間未全面核實，保持回顧情境定位。','',
      '## 重現與輸出','',
      '```powershell','python -X utf8 src/diagnose_green_dispatch.py --reuse','python -X utf8 src/green_load_trace.py','python -X utf8 scripts/report_dispatch_diagnostics.py','python -X utf8 -m app.export_dispatch_diagnostics','```','',
      '[逐場取捨點](tradeoff_points.csv)／[合計](tradeoff_summary.csv)／[逐班取消提案](removal_proposals.csv)／[訓練選出的取消診斷](selected_removals.csv)／[成本來源拆分](cost_drivers.csv)／[逐站影響](station_impacts.csv)／[逐班載量](../green_load_profile/beimen_load_profile.csv)／[載量守恆與重播核對](../green_load_profile/reconciliation.csv)','',
      '## 參考資料','']+[f'- [{s["title"]}]({s["url"]})' for s in SOURCES]+['']
    (out/'report.md').write_text('\n'.join(lines),encoding='utf-8',newline='\n')
    readme=ROOT/'README.md';text=readme.read_text('utf-8');start='<!-- dispatch-diagnostics:start -->';end='<!-- dispatch-diagnostics:end -->'
    content=['### 為什麼沒有減班？成本與等待的實際取捨','',
      f'額外保留 8／10 分鐘配置，使用與原結果相同的獨立 300 次情境評估。**減班能完成本次模擬疏運，但會拉長等待；不是程式禁止減班。** 下表是八場合計，減班試算尚未採用。','']+table+['',
      f'λ=1 時，取消一班省約 {saving:,.0f} 元，但只要多出 {threshold:,.0f} 人分鐘等待，候車時間成本就抵銷節省。背景組占模型旅次約 {background:.1%}；因此目標也在改善沿線一般乘客候車。原搜尋另有初篩限制，不能據此宣稱所有減班組合都不值得。','',
      '![成本與候車取捨](result/green_dispatch_diagnostics/figures/cost_wait_tradeoff.png)','',
      '### 到站列車不必固定假設 50%','',
      '舊回放的「剩餘空間 50%」及「方向各 50%」是分開的敏感度假設。新版全線調度已用過去 OD 推估起迄與方向，配合班表逐站先下後上；本次再公開逐班抵站、下車、上車、剩餘空間及情境分位數，沒有重新訓練或更換人流模型。','',
      '![逐班剩餘空間](result/green_dispatch_diagnostics/figures/beimen_remaining_capacity.png)','',
      '**仍是推估。** 小時 OD 無法唯一識別實際搭乘班次；路徑、分鐘到達與步行待校準。下一步可用列車運行與 AFC／載重資料校準，方法依據見下列文獻。色帶是模擬情境分位數，不是實測載量的信賴區間。','',
      '[完整診斷與公式](result/green_dispatch_diagnostics/report.md)／[取捨數據](result/green_dispatch_diagnostics/tradeoff_points.csv)／[逐班載量](result/green_load_profile/beimen_load_profile.csv)','']
    content+=['參考：'+ '；'.join(f'[{s["title"]}]({s["url"]})' for s in SOURCES)+ '。','']
    block=start+'\n'+'\n'.join(content)+'\n'+end
    if start in text: a=text.index(start);b=text.index(end,a)+len(end);text=text[:a]+block+text[b:]
    else:
        at=text.index('<!-- green-dispatch:end -->')+len('<!-- green-dispatch:end -->');text=text[:at]+'\n\n'+block+text[at:]
    readme.write_text(text,encoding='utf-8',newline='\n')
    print(out/'report.md');print(totals.to_string(index=False));print('background share',background,'cancel break-even lambda',break_min,break_max)

if __name__=='__main__':report()
