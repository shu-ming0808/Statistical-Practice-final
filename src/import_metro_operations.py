"""Download/validate official public data; --import-db inserts immutable snapshots."""
import argparse
import csv
import json
from metro_operations import ROOT, DB, collect, schema_sql, import_sql
from load_data import mysql


def export_beimen_holiday_baseline(tables, out):
    """Export the two official holiday columns without changing departure times."""
    sources = {row['source_id']: row for row in tables['metro_source_snapshot']}
    timetables = {row['source_id']: row for row in tables['metro_timetable']
                  if row['station_label'] == '北門'}
    day_labels = {'saturday': '週六', 'sunday_holiday': '週日及國定假日'}
    rows = []
    for departure in tables['metro_departure']:
        source_id = departure['source_id']
        day = departure['day_type']
        if source_id not in timetables or day not in day_labels:
            continue
        minute = int(departure['departure_minute'])
        source = sources[source_id]
        rows.append(dict(
            day_type=day, day_type_label=day_labels[day],
            direction=timetables[source_id]['direction_text'],
            terminal=departure['terminal'], departure_minute=minute,
            departure_time=f'{minute // 60:02d}:{minute % 60:02d}',
            effective_from=source['effective_from'], source_url=source['source_url'],
            source_id=source_id,
        ))
    if len(timetables) != 2 or {(row['source_id'], row['day_type']) for row in rows} != {
        (source_id, day) for source_id in timetables for day in day_labels
    }:
        raise ValueError('Expected both Beimen directions and both holiday columns')
    rows.sort(key=lambda row: (row['day_type'], row['direction'], row['departure_minute']))
    keys = [(row['source_id'], row['day_type'], row['departure_minute']) for row in rows]
    if len(set(keys)) != len(keys):
        raise ValueError('Duplicate Beimen holiday departure')
    with (out / 'beimen_holiday_baseline.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',default='20261015')
    parser.add_argument('--import-db',action='store_true')
    parser.add_argument('--login-path',default='codex-local')
    args=parser.parse_args()
    if not args.snapshot.isdigit() or len(args.snapshot)!=8: raise ValueError('Use YYYYMMDD snapshot')
    folder=ROOT/'.local/transit_reference'/args.snapshot
    tables=collect(folder)
    (folder/'normalized.json').write_text(json.dumps(tables,ensure_ascii=False,indent=2),encoding='utf-8')
    out=ROOT/'result/queue_dispatch_v1';out.mkdir(parents=True,exist_ok=True)
    for name,rows in tables.items():
        with (out/(name+'.csv')).open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    export_beimen_holiday_baseline(tables, out)
    report={'snapshot':args.snapshot,'counts':{t:len(r) for t,r in tables.items()},
            'sources':tables['metro_source_snapshot'],'historical_event_timetable':False}
    if args.import_db:
        existing=int(mysql(f'SELECT COUNT(*) FROM {DB}.analysis_event_station_hourly;',args.login_path))
        mysql(schema_sql(),args.login_path)
        report['inserted']=json.loads(mysql(import_sql(tables),args.login_path))
        report['idempotent_second_import']=json.loads(mysql(import_sql(tables),args.login_path))
        if any(report['idempotent_second_import'].values()): raise AssertionError('Import not idempotent')
        after=int(mysql(f'SELECT COUNT(*) FROM {DB}.analysis_event_station_hourly;',args.login_path))
        if existing!=after: raise AssertionError('Existing analysis cardinality changed')
        report['existing_analysis_rows_unchanged']=after
        # Read all snapshot rows back; counts alone would not catch a wrong terminal/time.
        for table,rows in tables.items():
            cols=list(rows[0]);select=','.join("'"+c+"',"+c for c in cols)
            actual=[json.loads(s) for s in mysql(f'SELECT JSON_OBJECT({select}) FROM {DB}.{table};',args.login_path).splitlines()]
            key=lambda r:tuple(str(r[c]) for c in cols if c!='retrieved_at_utc')
            if not set(map(key,rows)) <= set(map(key,actual)): raise AssertionError(f'DB mismatch {table}')
        report['database_readback_verified']=True
    (out/'import_audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='sources'},ensure_ascii=False,indent=2))


if __name__=='__main__': main()
