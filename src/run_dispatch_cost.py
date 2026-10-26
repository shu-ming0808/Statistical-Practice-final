"""Select extra-train count by monetary cost, before independent scenario evaluation.

Retrospective research scenarios, not an operational dispatch recommendation.
Service mileage has a sourced along-line proxy; depot/return remain assumptions.
"""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from dispatch_optimization import DirectionQueue, optimize_insertions
from metro_operations import ROOT, sha
from run_dispatch_optimization import sample_scenarios, evaluate_plan, clock
from run_queue_uncertainty import NORTH, SOUTH, beimen_trains, load_cohort

CONFIG = 'configs/dispatch_cost.json'
IMPLEMENTATION = [CONFIG, 'src/run_dispatch_cost.py', 'src/dispatch_optimization.py',
                  'src/run_dispatch_optimization.py', 'src/run_queue_uncertainty.py',
                  'src/queue_waits.py', 'src/queue_scenarios.py', 'src/queueing.py']


def causal_ratio_pool(ratios, event):
    """Keep this event only as a marker; sample_scenarios excludes its own ratio."""
    if event not in ratios.index:
        raise ValueError('Missing target event')
    dates = pd.to_datetime(ratios.index)
    return ratios.loc[dates <= pd.Timestamp(event)]


def score_family(metrics, km, cost_per_km, time_value, weight, residual_penalty):
    """The same mileage/cost per added train in either direction is an assumption."""
    inputs = [km, cost_per_km, time_value, weight, residual_penalty]
    if not np.isfinite(inputs).all() or min(inputs) < 0:
        raise ValueError('Cost parameters must be finite and nonnegative')
    rows = []
    for n, row in enumerate(metrics):
        operating = n * km * cost_per_km
        waiting = weight * time_value * (row['wait_mean'] + residual_penalty * row['residual_mean'])
        rows.append(dict(n_extra=n, operating_cost_twd=operating,
                         weighted_wait_cost_twd=waiting, objective_twd=operating + waiting))
    return rows


def select_plan_index(scores):
    """Training objective only; prefer fewer trains on an exact tie."""
    if not scores or not all(np.isfinite(r['objective_twd']) for r in scores):
        raise ValueError('Need finite candidate scores')
    return min(range(len(scores)), key=lambda i: (scores[i]['objective_twd'], scores[i]['n_extra']))


def queue_metrics(queues, trains, plan):
    wait = np.zeros(len(next(iter(queues.values())).demand))
    residual = np.zeros_like(wait)
    for d, queue in queues.items():
        w, r, _ = queue.evaluate(trains[d], plan[d])
        wait += w
        residual += r
    return dict(wait_mean=float(wait.mean()), residual_mean=float(residual.mean()),
                wait_se=float(wait.std(ddof=1) / np.sqrt(len(wait))) if len(wait) > 1 else 0.)


def baseline_guards(tables, day, first_hour):
    """Include pre-window departures: 18:00 must also avoid a 17:59 train."""
    result = {}
    for table in tables['metro_timetable']:
        if table['station_label'] != '北門':
            continue
        direction = NORTH if '松山' in table['direction_text'] else SOUTH
        result[direction] = sorted(r['departure_minute'] - first_hour * 60
                                   for r in tables['metro_departure']
                                   if r['source_id'] == table['source_id'] and r['day_type'] == day)
    return result


def case_extra_capacity(spaces, maximum, fraction):
    return spaces.copy() if fraction is None else np.full(len(spaces), maximum * fraction)


def mileage_reference(cfg):
    path = ROOT/cfg['route_mileage_csv']
    if sha(path) != cfg['route_mileage_sha256']:
        raise ValueError('Route mileage source changed')
    rows = pd.read_csv(path)
    route = rows[rows.route_name == cfg['route_name']]
    if len(route) != 1 or not np.isclose(route.iloc[0].service_km, cfg['service_km']):
        raise ValueError('Wrong along-line service distance')
    if not np.allclose(np.array(cfg['charged_route_multipliers'])*cfg['service_km'],
                       cfg['incremental_km_per_train_scenarios']):
        raise ValueError('Charged mileage does not match declared return scenarios')
    return route.iloc[0].to_dict()


def run(cfg, out):
    mileage = mileage_reference(cfg)
    forecasts, ratios = load_cohort(ROOT / cfg['prediction_path'], cfg['prediction_sha256'],
                                    cfg['prediction_column'], cfg['truth_column'],
                                    range(cfg['first_hour'], cfg['last_hour'] + 1))
    snapshot = ROOT / '.local/transit_reference' / cfg['operations_snapshot']
    tables = json.loads((snapshot / 'normalized.json').read_text(encoding='utf-8'))
    for source in tables['metro_source_snapshot']:
        if sha(snapshot / source['local_file']) != source['content_sha256']:
            raise ValueError('Official source snapshot changed')
    maximum = tables['metro_capacity_reference'][0]['max_train_persons']
    implementation = {p: sha(ROOT / p) for p in IMPLEMENTATION}
    signature = hashlib.sha256(json.dumps(dict(config=cfg, implementation=implementation,
                                snapshot=sha(snapshot / 'normalized.json')), sort_keys=True).encode()).hexdigest()
    cache = ROOT / '.local/dispatch_cost' / signature
    cache.mkdir(parents=True, exist_ok=True)
    streams = np.random.SeedSequence(cfg['seed']).spawn(2 * len(forecasts))
    candidates, selected, plan_rows, skipped = [], [], [], []
    for i, (event, hourly) in enumerate(forecasts.iterrows()):
        pool = causal_ratio_pool(ratios, event)
        donors = [e for e in pool.index if e != event]
        if not donors:
            skipped.append(dict(event_date=event, reason='no_prior_error_donors'))
            print(event, 'skipped: no earlier event errors', flush=True)
            continue
        weekday = date.fromisoformat(event).weekday()
        if weekday not in (2, 5):
            raise ValueError('Need explicit holiday/service-day mapping')
        day = 'weekday' if weekday == 2 else 'saturday'
        trains = beimen_trains(tables, day, cfg['first_hour'])
        guards = baseline_guards(tables, day, cfg['first_hour'])
        if max(max(t) for t in trains.values()) > cfg['horizon_minutes']:
            raise ValueError('Horizon must include last baseline train')
        arrays = []
        for j, count in enumerate([cfg['optimization_draws_per_event'], cfg['evaluation_draws_per_event']]):
            arrays.append(sample_scenarios(hourly.to_numpy(), pool, event, cfg, maximum,
                                          np.random.default_rng(streams[2*i+j]), count))
        (opt_arr, opt_spaces), (ev_arr, ev_spaces) = arrays
        for capacity, fraction in cfg['capacity_cases'].items():
            opt_extra = case_extra_capacity(opt_spaces, maximum, fraction)
            ev_extra = case_extra_capacity(ev_spaces, maximum, fraction)
            queues = {d: DirectionQueue(opt_arr[d], opt_spaces, cfg['horizon_minutes'], opt_extra,
                                        include_terminal_wait=True) for d in trains}
            ev_queues = {d: DirectionQueue(ev_arr[d], ev_spaces, cfg['horizon_minutes'], ev_extra,
                                           include_terminal_wait=True) for d in trains}
            for sep in cfg['min_separation_minutes']:
                checkpoint = cache / f'{event}_{capacity}_{sep}.json'
                if checkpoint.exists():
                    plans = json.loads(checkpoint.read_text(encoding='utf-8'))
                else:
                    plans = optimize_insertions(queues, trains, cfg['max_extra_trains'], sep,
                                                tuple(cfg['insertion_window_minutes']),
                                                cfg['residual_penalty_minutes'], feasibility_trains=guards)
                    checkpoint.write_text(json.dumps(plans, ensure_ascii=False), encoding='utf-8')
                for n, plan in enumerate(plans):
                    if sum(map(len, plan.values())) != n:
                        raise ValueError('Plan count mismatch')
                    for d in trains:
                        for m in plan[d]:
                            others = guards[d] + [t for t in plan[d] if t != m]
                            if any(abs(m - t) < sep for t in others):
                                raise ValueError('New train violates baseline/extra departure separation')
                        plan_rows.append(dict(event_date=event, capacity_case=capacity,
                                              min_separation=sep, n_extra=n, direction=d,
                                              inserted=' '.join(clock(m, cfg['first_hour']) for m in plan[d])))
                opt_metrics = [queue_metrics(queues, trains, p) for p in plans]
                # Plan counts are selected before evaluation outcomes are computed/read.
                choices = []
                for km in cfg['incremental_km_per_train_scenarios']:
                    for weight in cfg['waiting_cost_weights']:
                        scores = score_family(opt_metrics, km, cfg['cost_per_train_km_twd'],
                                              cfg['hourly_time_value_twd']/60, weight, cfg['residual_penalty_minutes'])
                        choices.append((km, weight, scores, select_plan_index(scores)))
                ev_metrics = [queue_metrics(ev_queues, trains, p) for p in plans]
                waits = {n: evaluate_plan(ev_arr, ev_spaces, ev_extra, trains, plans[n], cfg['p95_target_minutes'])
                         for n in sorted({0} | {choice[-1] for choice in choices})}
                for km, weight, scores, chosen in choices:
                    ev_scores = score_family(ev_metrics, km, cfg['cost_per_train_km_twd'],
                                             cfg['hourly_time_value_twd']/60, weight, cfg['residual_penalty_minutes'])
                    common = dict(event_date=event, day_type=day, donor_count=len(donors),
                                  latest_donor=max(donors), capacity_case=capacity, min_separation=sep,
                                  km_per_train=km, waiting_weight=weight)
                    for n, (score, ev_score) in enumerate(zip(scores, ev_scores)):
                        candidates.append(dict(**common, **score, selected=n == chosen,
                                               train_wait_mean=opt_metrics[n]['wait_mean'],
                                               train_residual_mean=opt_metrics[n]['residual_mean'],
                                               evaluation_objective_twd=ev_score['objective_twd'],
                                               evaluation_wait_mean=ev_metrics[n]['wait_mean'],
                                               evaluation_residual_mean=ev_metrics[n]['residual_mean']))
                    selected.append(dict(**common, selected_n_extra=chosen,
                                         train_objective_twd=scores[chosen]['objective_twd'],
                                         operating_cost_twd=scores[chosen]['operating_cost_twd'],
                                         evaluation_objective_twd=ev_scores[chosen]['objective_twd'],
                                         baseline_evaluation_objective_twd=ev_scores[0]['objective_twd'],
                                         evaluation_objective_change_twd=ev_scores[chosen]['objective_twd']-ev_scores[0]['objective_twd'],
                                         baseline_wait_person_minutes=ev_metrics[0]['wait_mean'],
                                         selected_wait_person_minutes=ev_metrics[chosen]['wait_mean'],
                                         wait_person_minutes_reduction=ev_metrics[0]['wait_mean']-ev_metrics[chosen]['wait_mean'],
                                         baseline_mean_wait_median=waits[0]['mean_wait_median'],
                                         selected_mean_wait_median=waits[chosen]['mean_wait_median'],
                                         baseline_p95_exceed_share=waits[0]['share_p95_over_target'],
                                         selected_p95_exceed_share=waits[chosen]['share_p95_over_target'],
                                         selected_censored_share=waits[chosen]['censored_share'],
                                         selected_residual_mean=ev_metrics[chosen]['residual_mean'],
                                         northbound=' '.join(clock(m, cfg['first_hour']) for m in plans[chosen][NORTH]),
                                         southbound=' '.join(clock(m, cfg['first_hour']) for m in plans[chosen][SOUTH])))
                print(event, capacity, f'{sep}min', 'complete', flush=True)
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in [('candidates', candidates), ('selected_plans', selected), ('plans', plan_rows), ('skipped', skipped)]:
        pd.DataFrame(rows).to_csv(out / f'{name}.csv', index=False, encoding='utf-8-sig')
    audit = dict(config=cfg, signature=signature, model_refitted=False, selection_split='optimization_draws_only',
                 evaluation_used_for_selection=False, input_prediction_sha256=sha(ROOT/cfg['prediction_path']),
                 normalized_snapshot_sha256=sha(snapshot/'normalized.json'),
                 implementation_sha256=implementation, future_donors_allowed=False,
                 event_release_times_verified=False, route_costs_verified=False,
                 mileage_reference=mileage, actual_full_incremental_mileage_known=False,
                 full_timetable_redesigned=False, dispatch_lead_times_enforced=False,
                 globally_optimal=False, skipped=skipped)
    (out/'audit.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def render_report(cfg, out):
    mileage_reference(cfg)
    chosen = pd.read_csv(out/'selected_plans.csv')
    candidates = pd.read_csv(out/'candidates.csv')
    ref = chosen[(chosen.km_per_train == cfg['reference_km_per_train']) &
                 (chosen.waiting_weight == cfg['reference_waiting_cost_weight']) &
                 (chosen.capacity_case == 'same_as_regular') & (chosen.min_separation == 2)].copy()
    plt.rcParams.update({'font.family': 'Microsoft JhengHei', 'axes.unicode_minus': False})
    (out/'figures').mkdir(exist_ok=True)
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), layout='constrained')
    x = np.arange(len(ref)); width = .36
    axes[0].bar(x-width/2, ref.baseline_wait_person_minutes/1000, width, color='#aaa', label='原班表')
    axes[0].bar(x+width/2, ref.selected_wait_person_minutes/1000, width, color='#426b70', label='成本選定方案')
    axes[0].set_ylabel('評估情境平均總等待（千人分鐘）')
    axes[0].legend(frameon=False)
    for i, row in enumerate(ref.itertuples()):
        axes[0].annotate(f'+{row.selected_n_extra} 班', (i+width/2, row.selected_wait_person_minutes/1000),
                         xytext=(0, 4), textcoords='offset points', ha='center', fontsize=8)
    colors = ['#426b70' if v < 0 else '#aa5656' for v in ref.evaluation_objective_change_twd]
    axes[1].bar(x, ref.evaluation_objective_change_twd/1000, color=colors)
    axes[1].axhline(0, color='#555', linewidth=.8)
    axes[1].set_ylabel('評估加權總成本差（千元；負值較好）')
    for ax in axes:
        ax.set_xticks(x, ref.event_date, rotation=45, ha='right', fontsize=8)
        ax.grid(axis='y', alpha=.15); ax.set_axisbelow(True)
    fig.suptitle(f"北門成本方案比較：120 秒、每班計價 {cfg['reference_km_per_train']:g} km、λ=1\n"
                 '訓練情境選班數；獨立模擬評估。成本含時間估值，不是實際現金節省。', fontsize=11)
    fig.savefig(out/'figures/cost_wait_comparison.png', dpi=170); plt.close(fig)
    small = chosen[(chosen.capacity_case == 'same_as_regular') & (chosen.min_separation == 2)]
    fig, axes = plt.subplots(1, len(cfg['incremental_km_per_train_scenarios']), figsize=(11, 5.6), layout='constrained', sharey=True, squeeze=False)
    axes = axes.ravel()
    for i, (ax, km) in enumerate(zip(axes, cfg['incremental_km_per_train_scenarios'])):
        grid = small[small.km_per_train == km].pivot(index='event_date', columns='waiting_weight', values='selected_n_extra')
        im = ax.imshow(grid, vmin=0, vmax=cfg['max_extra_trains'], cmap='Greys', aspect='auto')
        ax.set_title(('單程服務' if i == 0 else '單程服務＋假設全程回送')+f'：{km:g} km')
        ax.set_xticks(range(len(grid.columns)), [f'λ={v:g}' for v in grid.columns])
        ax.set_yticks(range(len(grid.index)), grid.index)
        for y in range(len(grid)):
            for xx in range(len(grid.columns)):
                v = int(grid.iloc[y, xx]); ax.text(xx, y, str(v), ha='center', va='center', color='white' if v >= 5 else 'black')
    fig.colorbar(im, ax=axes, label='選定新增班數')
    fig.suptitle('成本與候車權重敏感度（120 秒；加班車剩餘容量同一般列車）\n'
                 '格內為訓練情境選定班數；沿線里程有官方來源，回送與單位成本仍為假設。', fontsize=11)
    fig.savefig(out/'figures/weight_sensitivity.png', dpi=170); plt.close(fig)
    lines = ['# 北門增班成本與候車加權比較', '',
             '**保留原班表，在每個班數的啟發式方案中選加權總成本最低者。不是完整重排班表、全域最佳證明或可直接執行的調度命令。**', '',
             '## 方法與輸入', '',
             '- 固定原四站加權 XGBoost 北門預測；只借用日期早於目標活動的誤差。2024-07-17 沒有較早誤差而跳過，其餘八場可用 1–8 場誤差；不得把模擬次數當獨立活動樣本數。',
             '- 每場 200 次訓練情境搜尋 0–8 班方案，再從訓練成本選班數；另外 300 次情境只用來評估，沒有用其結果改選方案。各成本／權重共用同一批亂數情境。',
             '- 均勻、寬峰、集中峰各 1/3；方向 40–60%、一般班次剩餘容量 25–75%、Poisson 分鐘到達。加班車比較同一般剩餘容量與 90% 空間兩種情境。',
             '- 同方向新增與原有／新增班次相隔至少 120 或 180 秒，連 18:00 前最近原班也檢查；不移動原班。120／180 秒不是已核實的號誌安全極限。',
             '- 共同評估終點為 18:00 後 420 分鐘，末班後仍累計等待；未疏運者額外每人加 60 分鐘的研究懲罰。', '',
             r'$$J(x)=un(x)L+\lambda v\frac1S\sum_{s=1}^S[W_s(x)+60R_s(x)].$$', '',
             '- $u=128.05$ 元／列車公里沿用使用者提供值，車公里口徑及年度尚未核實。松山—新店逐站加總為 18.77 km；比較僅計單程服務 $L=18.77$，及假設另有一次全程空車回送 $L=37.54$ km。每加一班服務列車才計一次成本；回送不計為額外服務班次，也未模擬其載客效益。',
             '- [官方最短站間距離](https://data.taipei/dataset/detail?id=4acb4911-0360-4063-808d-fcee629508b3)不能直接用端點距離代替營運路線；本版依官方綠線順序加總相鄰站。車庫、折返及實際回送里程未知，沒有把未知值當實測零。單程情境只計已知服務部分；雙程也不是完整成本上界。[計算與資料庫紀錄](../metro_distances/report.md)',
             '- $v=196/60$ 元／人分鐘沿用前述時間價值代理；$\lambda=0.5,1,2$ 表示偏好，不是從測試資料擬合。不是北捷支付給乘客的費用。',
             '- $W_s$ 是雙方向整段時間的總候車人分鐘，$R_s$ 是期末未疏運人數。模型比較的是期望總量，不是各情境候車中位數。',
             '- 固定班數且每班價格相同時，加成本不改變班次位置搜尋；在 0–8 班之間以成本選擇。評估成本比原班表高時仍照實報告，不用評估資料事後退回原班表。', '',
             '## 參考情境結果', '',
             f"120 秒、一般剩餘容量、每班計價 {cfg['reference_km_per_train']:g} km、λ=1（僅計服務里程）。這些是試算條件，**不是經核實的最佳營運設定**。", '',
             '| 活動 | 過去誤差場數 | 選定增班 | 增班費用（元） | 平均等待中位數：原→新（分） | 評估加權總成本差（元） |',
             '|---|---:|---:|---:|---|---:|']
    for r in ref.itertuples():
        lines.append(f'| {r.event_date} | {r.donor_count} | {r.selected_n_extra} | {r.operating_cost_twd:,.0f} | '
                     f'{r.baseline_mean_wait_median:.2f} → {r.selected_mean_wait_median:.2f} | {r.evaluation_objective_change_twd:+,.0f} |')
    lines += ['', '總成本差＝新方案減原班表；負值表示在此估值下較好，並非營運現金節省。平均等待的中位數僅供解讀，選班數仍使用平均總等待人分鐘。', '',
              '![成本與候車比較](figures/cost_wait_comparison.png)', '', '![權重敏感度](figures/weight_sensitivity.png)', '',
              '## 與組員結果的關係及限制', '',
              '- 組員原版本借用其他八場（含較晚活動）的誤差；此版排除未來場。因此不應直接用兩份報告的數字當成單純加成本的效果。兩邊都沒有重訓人流模型。',
              '- 僅依活動日期限制誤差；原始資料實際發布時間尚未全面核實。仍使用 2026/8/30 一般班表對歷史需求，不是歷史實際班表重建或已驗證嚴格 D−1 部署。',
              '- P95 10 分鐘／5% 並非本版硬限制；這版最小化成本。所有 P95 超標比例均為假設分布下的模擬頻率，不是現場風險機率或校準信賴區間。',
              '- 未接起站發車、備車、司機、折返、整條路線衝突及動態滾動更新；只以松山—新店全程服務估成本，沒有驗證列車能依時抵達北門。官方路線里程仍是相鄰站最短距離加總的近似值；完整新增里程及邊際成本未核實。此版不宣稱可執行營運。',
              '- 找到的是既有候選啟發式方案中的最小值；若選到 8 班，可能仍受搜尋上限影響。沒有證明全域最佳，也不能把無達標方案寫成已證明必須超過 8 班。', '',
              '## 重跑', '', '```powershell', 'uv sync --inexact --extra transit --group analysis',
              'uv run --extra transit --group analysis python -X utf8 src/run_dispatch_cost.py', '```', '',
              '需要私下取得 `.local/forecast/predictions.csv` 與 `.local/transit_reference/20261015/` 原始官方快照；來源雜湊會核對。Git 不包含私有輸入。', '',
              '[成本候選比較](candidates.csv)／[選定方案及評估](selected_plans.csv)／[各班數時刻](plans.csv)／[跳過場次](skipped.csv)／[計算設定](../../configs/dispatch_cost.json)／[來源與稽核](audit.json)', '']
    (out/'report.md').write_text('\n'.join(lines), encoding='utf-8')
    audit = json.loads((out/'audit.json').read_text(encoding='utf-8'))
    audit['outputs'] = {str(p.relative_to(out)).replace('\\', '/'): sha(p) for p in sorted(out.rglob('*'))
                        if p.is_file() and p.name != 'audit.json'}
    (out/'audit.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report-only', action='store_true')
    args = parser.parse_args()
    cfg = json.loads((ROOT/CONFIG).read_text(encoding='utf-8'))
    out = ROOT/cfg['output_dir']
    if not args.report_only:
        run(cfg, out)
    render_report(cfg, out)
    print('Saved', out, flush=True)
