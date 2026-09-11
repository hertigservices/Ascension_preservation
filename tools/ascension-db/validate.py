"""Validate the complete catalog before publishing any version."""
import argparse,collections,gzip,json
from pathlib import Path
from validate_atlas import validate_atlas

def validate(root,allow_sample=False):
    m=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    if m.get('sample') and not allow_sample: raise ValueError('Refusing to publish a sample catalog')
    coverage=json.load(gzip.open(root/'coverage.json.gz','rt',encoding='utf-8'))
    if len(coverage)!=m['total_files'] or len({x['path'] for x in coverage})!=len(coverage): raise ValueError('Incomplete or duplicate source coverage')
    if sum(x['records'] for x in coverage)+m['recovery']['pages']!=m['records']: raise ValueError('Coverage record count mismatch')
    if sum(x['records'] for x in m['kinds'].values())!=m['records']: raise ValueError('Collection count mismatch')
    total=0
    part_counts={}
    for fid,info in m['files'].items():
        count=0
        for p in sorted((root/'records'/fid).glob('*.json.gz')):
            rows=json.load(gzip.open(p,'rt',encoding='utf-8'))
            part_counts[(fid,p.name.split('.')[0])]=len(rows)
            for r in rows:
                if len(r)!=6 or not all(isinstance(x,str) for x in r[:5]) or not isinstance(r[5],dict): raise ValueError(f'Invalid record in {p}')
            count+=len(rows)
        if count!=info['records']: raise ValueError(f'Records missing for {info["path"]}: {count} != {info["records"]}')
        total+=count
    if total!=m['records']: raise ValueError('Total detail count mismatch')
    for group,v in m['search'].items():
        count=0
        for path in v['parts']:
            for r in json.load(gzip.open(root/path,'rt',encoding='utf-8')):
                fid,part,offset=r[0].split('/')
                if fid not in m['files'] or (fid,part) not in part_counts or not 0 <= int(offset) < part_counts[(fid,part)]:raise ValueError('Search result points at a missing record')
                count+=1
        if count!=v['count']: raise ValueError('Search partition count mismatch')
    atlas=validate_atlas(root,m,part_counts)
    print('ATLAS VALID:',atlas,flush=True)
    print(f'VALID: {total:,} source records; {len(coverage):,} published files; all detail and search partitions readable.',flush=True)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('--allow-sample',action='store_true');a=p.parse_args();validate(a.root,a.allow_sample)
