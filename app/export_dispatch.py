"""Publish only the audited dispatch aggregates; does not read MySQL or OD."""
from pathlib import Path
import hashlib
import json
import shutil

ROOT=Path(__file__).resolve().parents[1]


def export(output=None):
    source=ROOT/'result/green_dispatch';dest=output or ROOT/'app/frontend/public/data'
    data=json.loads((source/'public_summary.json').read_text('utf-8'))
    if not data['no_wait_threshold'] or data['decision_window']!='18:00–23:00':
        raise ValueError('Unexpected dispatch contract')
    if len(data['comparison'])!=24: raise ValueError('Expected eight events and three weights')
    shutil.copyfile(source/'public_summary.json',dest/'dispatch.json')
    for name in ['comparison','station_impacts','timetable','summary']:
        shutil.copyfile(source/f'{name}.csv',dest/f'dispatch-{name}.csv')
    for name in ['cost_wait','station_impacts','timetable']:
        shutil.copyfile(source/f'figures/{name}.png',dest/f'dispatch-{name}.png')
    p=dest/'manifest.json';manifest=json.loads(p.read_text('utf-8'))
    # Existing forecast / observations timestamps are not falsely advanced.
    manifest['dispatch_generated_at']=data['generated_at']
    manifest['outputs']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in dest.iterdir() if p.is_file() and p.name!='manifest.json'}
    p.write_text(json.dumps(manifest,ensure_ascii=False,allow_nan=False,indent=2)+'\n',encoding='utf-8')
    print('Exported dispatch aggregates; existing observation snapshots unchanged.')


if __name__=='__main__':export()
