"""Readable comparisons from already-selected plans; never choose using evaluation."""
from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np

START = '<!-- dispatch-cost-results:start -->'
END = '<!-- dispatch-cost-results:end -->'


def validate_selection(chosen, candidates):
    keys = ['event_date','capacity_case','min_separation','km_per_train','waiting_weight']
    if chosen.duplicated(keys).any() or candidates.duplicated(keys+['n_extra']).any():
        raise ValueError('Duplicate cost comparison keys')
    expected = candidates.sort_values(['objective_twd','n_extra']).groupby(keys, sort=False).head(1)
    actual = candidates[candidates.selected]
    def selection(frame, count):
        return set(map(tuple,frame[keys+[count]].itertuples(index=False,name=None)))
    if selection(expected,'n_extra') != selection(actual,'n_extra') or selection(expected,'n_extra') != selection(chosen,'selected_n_extra'):
        raise ValueError('Selected plan does not minimize training cost')
    if not np.isfinite(candidates[['objective_twd','evaluation_objective_twd']]).all().all():
        raise ValueError('Nonfinite costs')


def detailed_results(cfg, out, chosen, candidates):
    validate_selection(chosen,candidates)
    ref = chosen[(chosen.km_per_train==cfg['reference_km_per_train']) &
                 (chosen.waiting_weight==cfg['reference_waiting_cost_weight']) &
                 (chosen.capacity_case=='same_as_regular') & (chosen.min_separation==2)].copy().sort_values('event_date')
    ref['wait_reduction_percent'] = 100*ref.wait_person_minutes_reduction/ref.baseline_wait_person_minutes
    ref.to_csv(out/'reference_summary.csv',index=False,encoding='utf-8-sig')
    summary = chosen.assign(hit_cap=chosen.selected_n_extra==cfg['max_extra_trains'],
                            worse=chosen.evaluation_objective_change_twd>1e-6).groupby(
                            ['capacity_case','min_separation','km_per_train','waiting_weight']).agg(
                            event_count=('event_date','nunique'),total_extra=('selected_n_extra','sum'),
                            at_search_cap=('hit_cap','sum'),evaluation_worse_events=('worse','sum'),
                            operating_cost_twd=('operating_cost_twd','sum'),
                            baseline_wait=('baseline_wait_person_minutes','sum'),
                            selected_wait=('selected_wait_person_minutes','sum'),
                            total_cost_change_twd=('evaluation_objective_change_twd','sum')).reset_index()
    summary['wait_reduction_percent']=100*(summary.baseline_wait-summary.selected_wait)/summary.baseline_wait
    summary.to_csv(out/'scenario_summary.csv',index=False,encoding='utf-8-sig')
    ref[['event_date','selected_n_extra','northbound','southbound']].to_csv(out/'reference_timetable.csv',index=False,encoding='utf-8-sig')
    plt.rcParams.update({'font.family':'Microsoft JhengHei','axes.unicode_minus':False})
    (out/'figures').mkdir(exist_ok=True)
    fig,ax=plt.subplots(figsize=(11,4.8),layout='constrained')
    x=np.arange(len(ref)); width=.36
    ax.bar(x-width/2,ref.baseline_p95_exceed_share*100,width,color='#aaa',label='原班表')
    ax.bar(x+width/2,ref.selected_p95_exceed_share*100,width,color='#426b70',label='成本選定方案')
    ax.axhline(5,color='#aa5656',linestyle='--',linewidth=1,label='5% 參考線（非本版硬限制）')
    for i,r in enumerate(ref.itertuples()):
        ax.annotate(f'{r.selected_p95_exceed_share:.1%}',(i+width/2,r.selected_p95_exceed_share*100),
                    xytext=(0,4),textcoords='offset points',ha='center',fontsize=9)
    ax.set_xticks(x,ref.event_date,rotation=30,ha='right',fontsize=9)
    ax.set_ylabel('P95 候車超過 10 分鐘的情境比例（%）')
    ax.grid(axis='y',alpha=.15);ax.set_axisbelow(True)
    ax.legend(frameon=False,loc='upper left',fontsize=9)
    ax.set_ylim(0,max(12,ref.baseline_p95_exceed_share.max()*100+9))
    fig.suptitle('降低加權總成本，不保證消除長時間候車\n120 秒、一般剩餘容量、單程服務里程、λ=1；300 次獨立模擬評估',fontsize=11)
    fig.savefig(out/'figures/p95_risk_comparison.png',dpi=170);plt.close(fig)
    lines=[f"**完整比較結果。**{chosen.event_date.nunique()} 場 × {chosen.min_separation.nunique()} 種班距 × {chosen.capacity_case.nunique()} 種容量 × {chosen.km_per_train.nunique()} 種里程成本 × {chosen.waiting_weight.nunique()} 種候車權重，共 {len(chosen)} 組選擇；每組比較 0–{cfg['max_extra_trains']} 班，共 {len(candidates):,} 筆成本比較。最早一場缺過去誤差而跳過。", '',
           f"**下表條件：**120 秒、加班車剩餘容量同一般列車、僅計單程服務 {cfg['reference_km_per_train']:g} km、$\\lambda={cfg['reference_waiting_cost_weight']:g}$。", '',
           '| 活動 | 增班 | 總等待降低 | 平均候車中位數：原→新（分） | P95 > 10 分情境：原→新 | 加權總成本差（元） |',
           '|---|---:|---:|---|---|---:|']
    for r in ref.itertuples():
        lines.append(f'| {r.event_date} | {r.selected_n_extra} | {r.wait_reduction_percent:.1f}% | '
                     f'{r.baseline_mean_wait_median:.2f} → {r.selected_mean_wait_median:.2f} | '
                     f'{r.baseline_p95_exceed_share:.1%} → {r.selected_p95_exceed_share:.1%} | {r.evaluation_objective_change_twd:+,.0f} |')
    waits=100*(1-ref.selected_wait_person_minutes.sum()/ref.baseline_wait_person_minutes.sum())
    worse=ref[ref.evaluation_objective_change_twd>1e-6]
    bad_text='；'.join(f'{r.event_date} 增 {r.selected_n_extra} 班，評估總成本反增約 {r.evaluation_objective_change_twd:,.0f} 元' for r in worse.itertuples())
    over=ref[ref.selected_p95_exceed_share>.05]
    over_text='、'.join(over.event_date)
    cap=int((ref.selected_n_extra==cfg['max_extra_trains']).sum())
    example=chosen[(chosen.event_date=='2024-08-10')&(chosen.capacity_case=='same_as_regular')&
                   (chosen.min_separation==2)&(chosen.km_per_train==max(cfg['incremental_km_per_train_scenarios']))&
                   (chosen.waiting_weight==cfg['reference_waiting_cost_weight'])]
    example_text=(f"2024/8/10 若另計假設全程空車回送，選 {int(example.iloc[0].selected_n_extra)} 班；" if len(example)==1 else '')
    lines += ['', f"八場合計增 {ref.selected_n_extra.sum()} 班，試算營運支出約 {ref.operating_cost_twd.sum():,.0f} 元；評估總等待量降低 {waits:.1f}%。含候車估值的加權總成本差為 {ref.evaluation_objective_change_twd.sum():+,.0f} 元，**不是現金節省**。這是八場情境期望值相加，不是八場實測績效。",'',
              f'- {(ref.selected_n_extra==0).sum()} 場選不增班。'+(bad_text+'；訓練選得好，不保證另一批情境仍較好。' if bad_text else '本輪評估未見總成本增加的場次，仍不保證新活動結果。'),
              f"- {cap} 場選到 {cfg['max_extra_trains']} 班的搜尋上限，不能當成不限班數的最優解。"+(over_text+' 的長候車情境仍超過 5% 參考線；最小成本不等於滿足候車服務門檻。' if len(over) else '本輪參考情境均未超過 5% 參考線，但這不是硬限制。'),
              '- '+example_text+'費用與候車權重會改變選擇。全組合保留在下方 CSV，未用評估表挑一組最好看的參數。','',
              '總等待降低＝各情境總候車人分鐘的平均值比較；平均候車中位數則是各情境「每人平均等待」的中位數，兩者不同。P95 超標比例的分母是 300 次模擬情境，未疏運完也列為超標；不是有多少百分比乘客等超過 10 分鐘。', '',
              '![長候車情境比較](result/dispatch_cost/figures/p95_risk_comparison.png)', '',
              '[完整逐場指標](result/dispatch_cost/reference_summary.csv)／[選定北門離站時刻](result/dispatch_cost/reference_timetable.csv)／[全部條件彙總](result/dispatch_cost/scenario_summary.csv)', '',
              '**實作範圍：**成本、候車權重、120／180 秒間隔及獨立亂數評估已執行；起站就緒、備車、全線衝突與即時滾動調度尚未納入。同一種子重跑是可重現性檢查，沒有增加獨立活動樣本。']
    return '\n'.join(lines)


def update_readme(path, body):
    text=path.read_text(encoding='utf-8')
    if text.count(START)!=1 or text.count(END)!=1 or text.index(START)>text.index(END):
        raise ValueError('README result markers missing or duplicated')
    a,b=text.split(START,1);_,c=b.split(END,1)
    path.write_text(a+START+'\n\n'+body+'\n\n'+END+c,encoding='utf-8')
