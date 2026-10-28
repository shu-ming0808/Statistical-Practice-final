"""Private, past-only demand and route preparation for Green-line experiments.

Shortest-time routes are hypotheses, not observed transfer choices. The four
project stations use the frozen annual XGBoost; other origins use earlier
same-weekday non-event OD averages. Current published schedules are applied
retrospectively. No target-event actual OD enters the simulation.
"""
from __future__ import annotations

import hashlib
import heapq
import json
from pathlib import Path

import duckdb
import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FOUR = ['北門', '大橋頭站', '雙連', '民權西路']
GROUPS = FOUR + ['其他旅次']
ROUTES = {'松山-新店':'G', '淡水-廣慈/奉天宮':'R', '南勢角-蘆洲':'O',
          '南勢角-迴龍':'O', '南港展覽館-頂埔':'BL', '動物園-南港展覽館':'BR',
          '新北產業園區-大坪林':'Y', '七張-小碧潭':'GG', '北投-新北投':'RR'}


def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def canonical(name):
    return {'大橋頭站':'大橋頭', 'BL板橋':'板橋', 'Y板橋':'板橋', 'G大坪林':'大坪林',
            '台北車':'台北車站', 'O景安':'景安', 'Y景安':'景安',
            'O頭前庄':'頭前庄', 'Y頭前庄':'頭前庄'}.get(name, name)


class Network:
    def __init__(self, tables, cfg):
        self.tables, self.cfg = tables, cfg
        self.graph, self.routes, self.positions = {}, {}, {}
        for route, line in ROUTES.items():
            edges = [r for r in tables['metro_link_time'] if r['route_name'] == route]
            if not edges: raise ValueError(f'Missing route: {route}')
            nodes = [canonical(r['station_a']) for r in edges] + [canonical(edges[-1]['station_b'])]
            pos = np.r_[0., np.cumsum([(r['run_seconds']+r['dwell_at_a_seconds'])/60 for r in edges])]
            self.routes[route] = nodes; self.positions[route] = pos
            for a,b,minutes in zip(nodes,nodes[1:],np.diff(pos)):
                for u,v in [((line,a),(line,b)), ((line,b),(line,a))]:
                    self.graph.setdefault(u,{})[v] = float(minutes)
        by_station = {}
        for node in self.graph: by_station.setdefault(node[1], []).append(node)
        for states in by_station.values():
            for a in states:
                for b in states:
                    if a != b: self.graph[a][b] = cfg['transfer_walk_minutes']
        self.by_station = by_station
        self.green = self.routes['松山-新店']  # Songshan -> Xindian
        self.gpos = self.positions['松山-新店']
        self.beimen = self.green.index('北門')
        self.paths, self.schedule_cache = {}, {}
        for origin in by_station:
            queue = [(0., (n,)) for n in sorted(by_station[origin])]
            heapq.heapify(queue); best = {}
            while queue:
                distance, path = heapq.heappop(queue); node = path[-1]
                if node in best: continue
                best[node] = (distance,path)
                for nxt, length in self.graph[node].items():
                    if nxt not in best: heapq.heappush(queue,(distance+length,path+(nxt,)))
            for target, states in by_station.items():
                values = [best[n] for n in states if n in best]
                if values: self.paths[origin,target] = min(values)[1]

    def fixed_schedule(self, line, origin, dest, day):
        """Propagate a published R/O reference timetable using official link times.

These are derived connecting departures, not separately published timetables.
Only paths before a branch/short-turn boundary are accepted.
        """
        key = line,origin,dest,day
        if key in self.schedule_cache: return self.schedule_cache[key]
        candidates = []
        for route,nodes in self.routes.items():
            if ROUTES[route] != line or origin not in nodes or dest not in nodes: continue
            sign = 1 if nodes.index(dest)>nodes.index(origin) else -1
            pos = self.positions[route]
            for table in self.tables['metro_timetable']:
                ref = canonical(table['station_label'])
                if table['line_code'] != line or ref not in nodes: continue
                # Direction and route coverage are checked through terminal metadata.
                dep = []
                for r in self.tables['metro_departure']:
                    if r['source_id'] != table['source_id'] or r['day_type'] != day: continue
                    terminal = r['terminal']
                    if terminal not in nodes:
                        if line == 'O' and terminal in ('蘆洲','迴龍') and nodes.index(dest)<=nodes.index('大橋頭'):
                            terminal = nodes[-1]
                        else: continue
                    if sign*(nodes.index(terminal)-nodes.index(ref)) <= 0: continue
                    if sign*(nodes.index(terminal)-nodes.index(dest)) < 0: continue
                    delta = sign*(pos[nodes.index(origin)]-pos[nodes.index(ref)])
                    dep.append(r['departure_minute']+delta-self.cfg['simulation_start_hour']*60)
                if dep: candidates.append((abs(pos[nodes.index(origin)]-pos[nodes.index(ref)]),sorted(set(dep))))
        answer = np.array(min(candidates)[1]) if candidates else np.array([])
        self.schedule_cache[key] = answer
        return answer

    def legs(self, path):
        result = []; start = path[0]; elapsed = 0.
        for a,b in zip(path,path[1:]):
            if a[0] != b[0]:
                if start[1] != a[1]: result.append((start[0],start[1],a[1],elapsed))
                result.append(('walk',a[1],b[1],self.cfg['transfer_walk_minutes']))
                start=b;elapsed=0.
            else: elapsed += self.graph[a][b]
        if start[1] != path[-1][1]: result.append((start[0],start[1],path[-1][1],elapsed))
        return result


def frozen_four():
    cfg = json.loads((ROOT/'configs/forecast.json').read_text('utf-8'))
    path = ROOT/cfg['input']
    if digest(path) != cfg['input_sha256']: raise ValueError('Frozen model input changed')
    data = pd.read_csv(path,parse_dates=['event_date'])
    blocks=[]
    for year in cfg['years']:
        model_path=ROOT/f'.local/forecast/models/{year}_xgboost_weighted.joblib'
        fit=joblib.load(model_path)
        part=data[data.year==year].copy()
        # predict receives features only; y is never supplied to the estimator.
        part['predicted']=fit.predict(part.drop(columns=['y']))
        blocks.append(part[['event_date','station','hour','predicted','y']])
    data=pd.concat(blocks)
    anchor=pd.read_csv(ROOT/'.local/forecast/predictions.csv',parse_dates=['event_date'])
    check=data[data.station=='北門'].merge(anchor,on=['event_date','station','hour'])
    if len(check)!=54 or np.max(abs(check.predicted-check.frozen_w4))>1e-5:
        raise ValueError('Existing Beimen forecasts not reproduced')
    return data


def past_od(event,cfg):
    cache=ROOT/'.local/green_dispatch';cache.mkdir(parents=True,exist_ok=True)
    path=cache/f'od_{event}.csv'
    events={r['event_date'] for r in json.loads((ROOT/'data/interim/events_snapshot.json').read_text('utf-8'))}
    con=duckdb.connect()
    candidates=con.execute("SELECT DISTINCT service_date FROM read_parquet(?) WHERE service_date < CAST(? AS DATE) AND service_date >= CAST(? AS DATE)-INTERVAL 56 DAY ORDER BY service_date DESC", [str(ROOT/'data/processed/station_hourly.parquet'),event,event]).fetchall()
    date=pd.Timestamp(event)
    days=[d[0].isoformat() for d in candidates if d[0].weekday()==date.weekday() and d[0].isoformat() not in events][:cfg['background_max_days']]
    if len(days)<2: raise ValueError(f'{event}: fewer than two prior same-weekday dates')
    # Regenerate from private clean parquet; never load target-event OD.
    months=sorted({d[:7].replace('-','') for d in days})
    files=[str(ROOT/f'data/interim/clean_od/{m}.parquet') for m in months]
    quoted=','.join('?' for _ in days)
    d=con.execute(f"SELECT source_hour, origin_station, destination_station, SUM(passengers)/? AS passengers FROM read_parquet(?) WHERE service_date IN ({quoted}) AND source_hour BETWEEN 17 AND 22 GROUP BY ALL ORDER BY source_hour, origin_station, destination_station",[len(days),files,*days]).df()
    con.close()
    d.to_csv(path,index=False,encoding='utf-8-sig')
    return d,days,{'od_sha256':digest(path),'months':months}


def prepare_event(event, forecasts, network, cfg):
    """Build sparse, sorted minute cohorts, preserving OD destinations and groups."""
    od,days,audit=past_od(event,cfg)
    # GROUP BY without a stable row order can change connection IDs, cohort
    # ordering and therefore which Poisson variate reaches each cohort even with
    # an identical seed. Also enforce the order for supplied/cached OD frames.
    od=od.sort_values(['source_hour','origin_station','destination_station'],kind='stable').reset_index(drop=True)
    day='saturday' if pd.Timestamp(event).weekday()==5 else 'weekday'
    prediction=forecasts[forecasts.event_date==pd.Timestamp(event)].set_index(['station','hour']).predicted
    totals=od.groupby(['origin_station','source_hour']).passengers.sum()
    group_map={canonical(v):i for i,v in enumerate(FOUR)}
    # rows accumulate [expected count, count*pre-wait], key includes demand-source
    # group separately from reporting group (destination may be a project station).
    buckets=[{},{}];connections=[];connection_keys={};unrouted=0.;all_mass=0.;green_mass=0.
    bypass=np.zeros((5,2))
    for row in od.itertuples(index=False):
        hour,original,target,n=row.source_hour,row.origin_station,row.destination_station,float(row.passengers)
        o,t=canonical(original),canonical(target)
        if o==t or n<=0: continue
        source_group=group_map.get(o,4)
        group=source_group if source_group<4 else group_map.get(t,4)
        if source_group<4 and hour>=18:
            original_label=FOUR[source_group]
            denom=totals.get((original,hour),0)
            if denom<=0: raise ValueError('Empty destination mix')
            n*=float(prediction.loc[original_label,hour])/denom
        all_mass+=n
        path=network.paths.get((o,t))
        if path is None: unrouted+=n;continue
        legs=network.legs(path)
        glegs=[i for i,l in enumerate(legs) if l[0]=='G']
        if not glegs:
            bypass[group,0]+=n
            continue
        if len(glegs)!=1: raise ValueError('Route re-enters Green line')
        gi=glegs[0];gleg=legs[gi]
        a,b=network.green.index(gleg[1]),network.green.index(gleg[2])
        direction=0 if a<b else 1
        if direction==1: a,b=18-a,18-b
        post=-1
        # Following R/O connection: unchanged timetable, changed catchable departure.
        after=[l for l in legs[gi+1:] if l[0]!='walk']
        if after and after[0][0] in ('R','O'):
            l=after[0];key=l[0],l[1],l[2]
            sched=network.fixed_schedule(*key,day)
            if len(sched):
                if key not in connection_keys:
                    connection_keys[key]=len(connections);connections.append(sched)
                post=connection_keys[key]
        green_mass+=n
        for minute in range(60):
            current=(hour-cfg['simulation_start_hour'])*60+minute+1+cfg['platform_access_minutes']
            pre_wait=0.
            for line,begin,end,travel in legs[:gi]:
                if line in ('R','O'):
                    sched=network.fixed_schedule(line,begin,end,day)
                    idx=np.searchsorted(sched,current)
                    if len(sched) and idx<len(sched):
                        pre_wait+=sched[idx]-current;current=sched[idx]
                current+=travel
            arrival=int(np.ceil(current))
            # One-minute routing bins; source minute is kept to sample peak shape.
            # Background does not use an event peak profile; merge equivalent
            # cohorts instead of keeping the irrelevant source-minute identity.
            key=(a,arrival,b,group,post,source_group,hour if source_group<4 else 17,
                 minute if source_group<4 else 0)
            value=buckets[direction].setdefault(key,[0.,0.])
            value[0]+=n/60;value[1]+=n/60*pre_wait
    if unrouted/max(all_mass,1)>0.01: raise ValueError(f'Unrouted demand exceeds 1%: {unrouted/all_mass:.2%}')
    maxlen=max([len(t) for t in connections] or [1]);conn=np.full((max(1,len(connections)),maxlen),-1.)
    for i,c in enumerate(connections): conn[i,:len(c)]=c
    packed=[]
    for direction in (0,1):
        items=sorted(buckets[direction].items())
        keys=np.array([k for k,v in items],dtype=np.int64)
        vals=np.array([v for k,v in items])
        pos=network.gpos if direction==0 else network.gpos[-1]-network.gpos[::-1]
        bm=network.beimen if direction==0 else 18-network.beimen
        packed.append(dict(times=keys[:,1].astype(float),exits=keys[:,2],groups=keys[:,3],post_keys=keys[:,4],
                           sources=keys[:,5],hours=keys[:,6],source_minutes=keys[:,7],means=vals[:,0],
                           pre_wait=vals[:,1]/vals[:,0],station_bounds=np.searchsorted(keys[:,0],np.arange(20)),
                           offsets=pos-pos[bm]))
    audit.update(baseline_dates=days,day_type=day,unrouted_passengers=unrouted,
                 all_projected_passengers=all_mass,green_projected_passengers=green_mass,
                 bypass_passengers=bypass[:,0].tolist(),connections=len(connections),
                 cohorts=[len(p['times']) for p in packed])
    return packed,conn,audit


def sample_mass(packed, forecasts, event, rng, count):
    """Past-event whole-panel errors keep station/hour dependence; independent draws.

Background uses the historical mean and Poisson variation, not event-day truth.
Minute shape uncertainty applies to event station origins, not all commuters.
    """
    past=forecasts[forecasts.event_date<pd.Timestamp(event)].copy()
    donors=sorted(past.event_date.unique())
    if not donors: raise ValueError('No earlier event error panel')
    panels=[]
    for donor in donors:
        d=past[past.event_date==donor].set_index(['station','hour'])
        panels.append(np.array([[d.loc[(st,h),'y']/d.loc[(st,h),'predicted'] for h in range(18,23)] for st in FOUR]))
    choices=rng.integers(len(panels),size=count)
    shapes=rng.integers(3,size=count);centers=rng.choice([15,30,45],size=count)
    result=[]
    for p in packed:
        out=np.empty((count,len(p['times'])),dtype=np.float32)
        for s in range(count):
            rate=p['means'].copy()
            for st in range(4):
                for h in range(18,23):
                    mask=(p['sources']==st)&(p['hours']==h)
                    rate[mask]*=panels[choices[s]][st,h-18]
                    if shapes[s]:
                        sigma=20 if shapes[s]==1 else 5
                        profile=np.exp(-.5*((np.arange(60)+.5-centers[s])/sigma)**2)
                        profile*=60/profile.sum()
                        rate[mask]*=profile[p['source_minutes'][mask]]
            out[s]=rng.poisson(rate)
        result.append(out)
    return result, [str(pd.Timestamp(d).date()) for d in donors]
