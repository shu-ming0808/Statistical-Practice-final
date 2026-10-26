"""Import official shortest OD distances and derive along-line service mileage.

The fare table is not a train/depot mileage log. Only adjacent pairs along the
official link-time route are summed. Depot, turnback and return remain unknown.
"""
from __future__ import annotations

import argparse
import csv
from decimal import Decimal
import io
import json
import re

from load_data import mysql
from metro_operations import ROOT, DB, SCHEMA, fetch, source, sha, import_sql

PAGE = 'https://data.taipei/dataset/detail?id=4acb4911-0360-4063-808d-fcee629508b3'
URL = 'https://data.taipei/api/frontstage/tpeod/dataset/resource.download?rid=893c2f2a-dcfd-407b-b871-394a14105532'
EFFECTIVE = '2026-09-29'
ROUTES = ('松山-新店', '松山-台電大樓')
DISTANCE_SCHEMA = {
    'metro_source_snapshot': SCHEMA['metro_source_snapshot'],
    'metro_od_distance': ('source_id,origin,destination', '''
        source_id CHAR(64) NOT NULL, origin VARCHAR(64) NOT NULL,
        destination VARCHAR(64) NOT NULL, shortest_km DECIMAL(7,2) NOT NULL,
        full_fare_twd INT NOT NULL, concession_fare_twd INT NOT NULL,
        PRIMARY KEY(source_id,origin,destination),
        FOREIGN KEY(source_id) REFERENCES metro_source_snapshot(source_id),
        CHECK(shortest_km>=0), CHECK(full_fare_twd>=0), CHECK(concession_fare_twd>=0)
    '''),
    'metro_route_distance_segment': ('distance_source_id,link_source_id,link_no', '''
        distance_source_id CHAR(64) NOT NULL, link_source_id CHAR(64) NOT NULL,
        link_no INT NOT NULL, station_a VARCHAR(64) NOT NULL, station_b VARCHAR(64) NOT NULL,
        PRIMARY KEY(distance_source_id,link_source_id,link_no),
        FOREIGN KEY(distance_source_id,station_a,station_b)
            REFERENCES metro_od_distance(source_id,origin,destination),
        FOREIGN KEY(link_source_id,link_no) REFERENCES metro_link_time(source_id,link_no)
    '''),
}


def parse_fares(content, source_id):
    reader = csv.DictReader(io.StringIO(content.decode('cp950')))
    expected = ['起站', '訖站', '全票票價[金額]',
                '敬老卡愛心卡愛心陪伴卡及臺北市與新北市兒童優惠票價[金額]', '距離']
    if reader.fieldnames != expected:
        raise ValueError('Official fare/distance schema changed')
    rows, pairs = [], {}
    for r in reader:
        a, b = r['起站'].strip(), r['訖站'].strip()
        km = Decimal(r['距離'])
        if not a or not b or not km.is_finite() or km < 0 or km != km.quantize(Decimal('.01')):
            raise ValueError('Invalid station/distance')
        if a == b and km != 0:
            raise ValueError('Invalid diagonal/distance')
        full, concession = int(r[expected[2]]), int(r[expected[3]])
        if min(full, concession) < 0:
            raise ValueError('Negative fare')
        row = dict(source_id=source_id, origin=a, destination=b,
                   shortest_km=f'{km:.2f}', full_fare_twd=full, concession_fare_twd=concession)
        if (a,b) in pairs:
            # Interchange stations repeat in this source's line-based matrix.
            # Collapse only exact normalized duplicates; conflicting values abort.
            if pairs[a,b] != row:
                raise ValueError('Conflicting duplicate OD pair')
            continue
        pairs[a,b] = row
        rows.append(row)
    stations = {a for a, _ in pairs}
    if not stations or len(pairs) != len(stations)**2 or {b for _, b in pairs} != stations:
        raise ValueError('Expected complete square OD matrix')
    if any(pairs.get((b,a), {}).get('shortest_km') != row['shortest_km'] for (a,b), row in pairs.items()):
        raise ValueError('Asymmetric OD distances')
    return rows


def derive_routes(fares, links, routes=ROUTES):
    lookup = {(r['origin'], r['destination']): r for r in fares}
    segments, totals, dispatch = [], [], []
    for route in routes:
        path = sorted([r for r in links if r['route_name'] == route], key=lambda r: r['route_seq'])
        if not path or [r['route_seq'] for r in path] != list(range(1, len(path)+1)):
            raise ValueError('Missing/duplicate route sequence')
        if len({r['source_id'] for r in path}) != 1:
            raise ValueError('Mixed topology snapshots')
        if any(a['station_b'] != b['station_a'] for a, b in zip(path, path[1:])):
            raise ValueError('Disconnected route')
        stations = [path[0]['station_a']] + [r['station_b'] for r in path]
        if len(set(stations)) != len(stations) or stations.count('北門') != 1:
            raise ValueError('Expected simple route through Beimen')
        distances = []
        for link in path:
            pair = lookup[link['station_a'], link['station_b']]
            if Decimal(pair['shortest_km']) <= 0:
                raise ValueError('Zero fare distance cannot serve as a rail segment')
            distances.append(Decimal(pair['shortest_km']))
            segments.append(dict(distance_source_id=pair['source_id'], link_source_id=link['source_id'],
                                 link_no=link['link_no'], station_a=link['station_a'], station_b=link['station_b']))
        total = sum(distances, Decimal(0))
        totals.append(dict(distance_source_id=pair['source_id'], link_source_id=path[0]['source_id'],
                           route_name=route, origin=stations[0], terminal=stations[-1],
                           adjacent_segments=len(path), service_km=f'{total:.2f}',
                           shortest_endpoint_km=lookup[stations[0], stations[-1]]['shortest_km'],
                           path_text=' → '.join(stations)))
        target = stations.index('北門')
        for origin, km, names in [(stations[0], sum(distances[:target]), stations[:target+1]),
                                  (stations[-1], sum(distances[target:]), stations[target:][::-1])]:
            dispatch.append(dict(route_name=route, origin=origin, destination='北門',
                                 along_line_km=f'{km:.2f}', path_text=' → '.join(names),
                                 depot_km=None, return_km=None,
                                 distance_source_id=pair['source_id'], link_source_id=path[0]['source_id']))
    return segments, totals, dispatch


def schema_sql():
    statements = [f'USE {DB};']
    for table, (_, ddl) in DISTANCE_SCHEMA.items():
        statements.append(f'CREATE TABLE IF NOT EXISTS {table} ({ddl}) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;')
    statements.append('''CREATE OR REPLACE VIEW v_metro_route_distance AS
        SELECT s.distance_source_id,s.link_source_id,l.route_name,
               COUNT(*) AS adjacent_segments,SUM(d.shortest_km) AS service_km
        FROM metro_route_distance_segment s
        JOIN metro_link_time l ON l.source_id=s.link_source_id AND l.link_no=s.link_no
          AND l.station_a=s.station_a AND l.station_b=s.station_b
        JOIN metro_od_distance d ON d.source_id=s.distance_source_id
          AND d.origin=s.station_a AND d.destination=s.station_b
        GROUP BY s.distance_source_id,s.link_source_id,l.route_name;''')
    return '\n'.join(statements)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', default='20261026')
    parser.add_argument('--operations-snapshot', default='20261015')
    parser.add_argument('--import-db', action='store_true')
    parser.add_argument('--login-path', default='codex-local')
    args = parser.parse_args()
    if any(not re.fullmatch(r'\d{8}', x) for x in [args.snapshot, args.operations_snapshot]):
        raise ValueError('Snapshot must be YYYYMMDD')
    folder = ROOT/'.local/transit_distance'/args.snapshot
    folder.mkdir(parents=True, exist_ok=True)
    raw = fetch(folder, 'fares.csv', URL)
    page = fetch(folder, 'dataset.html', PAGE)
    # Future upstream revisions require an explicit version review, not a silent old date.
    html = page.read_text(encoding='utf-8')
    if '1150929' not in html or '最短距離' not in html or '公里' not in html:
        raise ValueError('Review new official version/unit metadata')
    src = source(raw, URL, 'fare_distance', EFFECTIVE)
    topology_folder = ROOT/'.local/transit_reference'/args.operations_snapshot
    topology = json.loads((topology_folder/'normalized.json').read_text(encoding='utf-8'))
    for r in topology['metro_source_snapshot']:
        if sha(topology_folder/r['local_file']) != r['content_sha256']:
            raise ValueError('Topology source hash mismatch')
    fares = parse_fares(raw.read_bytes(), src['source_id'])
    segments, totals, dispatch = derive_routes(fares, topology['metro_link_time'])
    tables = dict(metro_source_snapshot=[src], metro_od_distance=fares, metro_route_distance_segment=segments)
    out = ROOT/'result/metro_distances'
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in [('route_distances', totals), ('beimen_origin_distances', dispatch),
                       ('route_segments', [dict(**r, shortest_km=next(f['shortest_km'] for f in fares
                           if f['origin']==r['station_a'] and f['destination']==r['station_b'])) for r in segments])]:
        with (out/f'{name}.csv').open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    (folder/'normalized.json').write_text(json.dumps(tables, ensure_ascii=False, indent=2), encoding='utf-8')
    raw_count = len(list(csv.DictReader(io.StringIO(raw.read_bytes().decode('cp950')))))
    audit = dict(dataset_page=PAGE, distance_source=src, metadata_sha256=sha(page),
                 raw_rows=raw_count, identical_duplicate_rows_collapsed=raw_count-len(fares),
                 zero_off_diagonal=[dict(origin=r['origin'], destination=r['destination']) for r in fares
                                    if r['origin']!=r['destination'] and Decimal(r['shortest_km'])==0],
                 topology_snapshot_sha256=sha(topology_folder/'normalized.json'),
                 counts={k:len(v) for k,v in tables.items()},
                 method='Sum adjacent shortest-OD distances along official ordered route; rounded distance proxy, not train odometer/depot mileage.',
                 depot_and_return_known=False, routes=totals)
    if args.import_db:
        before = mysql(f'SELECT COUNT(*) FROM {DB}.analysis_event_station_hourly;', args.login_path)
        mysql(schema_sql(), args.login_path)
        audit['inserted'] = json.loads(mysql(import_sql(tables, DISTANCE_SCHEMA), args.login_path))
        audit['second_import'] = json.loads(mysql(import_sql(tables, DISTANCE_SCHEMA), args.login_path))
        if any(audit['second_import'].values()):
            raise AssertionError('Repeated import is not idempotent')
        after = mysql(f'SELECT COUNT(*) FROM {DB}.analysis_event_station_hourly;', args.login_path)
        if before != after:
            raise AssertionError('Existing analysis count changed')
        audit['existing_analysis_rows_unchanged'] = int(after)
        for table, rows in tables.items():
            cols = list(rows[0]); select = ','.join("'"+c+"',"+c for c in cols)
            actual = [json.loads(s) for s in mysql(f'SELECT JSON_OBJECT({select}) FROM {DB}.{table};', args.login_path).splitlines()]
            def key(row):
                return tuple(f"{Decimal(str(row[c])):.2f}" if c=='shortest_km' else str(row[c])
                             for c in cols if c!='retrieved_at_utc')
            if not set(map(key, rows)) <= set(map(key, actual)):
                raise AssertionError(f'DB readback mismatch {table}')
        actual = mysql(f'SELECT route_name,service_km FROM {DB}.v_metro_route_distance '
                       f"WHERE distance_source_id='{src['source_id']}';", args.login_path)
        if {tuple(r.split('\t')) for r in actual.splitlines()} != {(r['route_name'],r['service_km']) for r in totals}:
            raise AssertionError('DB derived route sum mismatch')
        audit['database_readback_verified'] = True
    audit['outputs'] = {p.name: sha(p) for p in out.glob('*.csv')}
    (out/'import_audit.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
