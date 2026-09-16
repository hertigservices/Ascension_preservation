"""Validate the complete catalog before publishing any version."""
import argparse,collections,gzip,json
from pathlib import Path
from validate_atlas import validate_atlas
from validate_groups import GroupValidator

def _validate(root,allow_sample=False,owned=None):
    m=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    if m.get('sample') and not allow_sample: raise ValueError('Refusing to publish a sample catalog')
    coverage=json.load(gzip.open(root/'coverage.json.gz','rt',encoding='utf-8'))
    if len(coverage)!=m['total_files'] or len({x['path'] for x in coverage})!=len(coverage): raise ValueError('Incomplete or duplicate source coverage')
    if sum(x['records'] for x in coverage)+m['recovery']['pages']!=m['records']: raise ValueError('Coverage record count mismatch')
    if sum(x['records'] for x in m['kinds'].values())!=m['records']: raise ValueError('Collection count mismatch')
    groups=GroupValidator(m) if m.get("grouping") else None
    if groups: owned.append(groups)
    total=0
    part_counts={}
    for fid,info in m['files'].items():
        count=0
        for p in sorted((root/'records'/fid).glob('*.json.gz')):
            rows=json.load(gzip.open(p,'rt',encoding='utf-8'))
            part_counts[(fid,p.name.split('.')[0])]=len(rows)
            for offset,r in enumerate(rows):
                if groups: groups.record(info["path"],f'{fid}/{p.name.split(".")[0]}/{offset}',r)
                if len(r) not in (6,7) or not all(isinstance(x,str) for x in r[:5]) or not isinstance(r[5],dict): raise ValueError(f'Invalid record in {p}')
                if len(r)==7:
                    import attribution
                    expected=attribution.resolve(info['path'],r[5])
                    if expected is None or r[6]!=expected or r[3]!=attribution.mode_text(expected): raise ValueError(f'Attribution does not match preserved evidence in {p}')
            count+=len(rows)
        if count!=info['records']: raise ValueError(f'Records missing for {info["path"]}: {count} != {info["records"]}')
        total+=count
    if total!=m['records']: raise ValueError('Total detail count mismatch')
    zone_counts={}
    for group,v in m['search'].items():
        count=0
        facet_counts=collections.Counter()
        seen=set()
        for path in v['parts']:
            for r in json.load(gzip.open(root/path,'rt',encoding='utf-8')):
                fid,part,offset=r[0].split('/')
                if fid not in m['files'] or (fid,part) not in part_counts or not 0 <= int(offset) < part_counts[(fid,part)]:raise ValueError('Search result points at a missing record')
                count+=1
                if groups:
                    gid=r[7]['id']
                    if gid in seen: raise ValueError('Duplicate content group in search bucket')
                    seen.add(gid)
                    groups.row(group,r)
                if group.startswith('zone:'): facet_counts[r[2]]+=len(r[7]['members']) if groups else 1
        if count!=v['count']: raise ValueError('Search partition count mismatch')
        if group.startswith('zone:'): zone_counts[group[5:]]=dict(facet_counts)
    if groups: groups.finish()
    if 'zoneFilter' in m:
        facets=m['zoneFilter']
        if facets.get('schema')!='ascension-zones-1':raise ValueError('Invalid zone facet schema')
        expected={z['key']:z['kinds'] for z in facets['zones']}
        if len(expected)!=len(facets['zones']) or expected!=zone_counts:raise ValueError('Zone facet counts do not match search partitions')
        for zone in facets['zones']:
            if not zone['label'] or zone['count']!=sum(zone['kinds'].values()):raise ValueError('Invalid zone facet label/count')
            if any(n>facets['kinds'].get(k,0) for k,n in zone['kinds'].items()):raise ValueError('Invalid zone collection count')
    atlas=validate_atlas(root,m,part_counts)
    print('ATLAS VALID:',atlas,flush=True)
    print(f'VALID: {total:,} source records; {len(coverage):,} published files; all detail and search partitions readable.',flush=True)
def validate(root,allow_sample=False):
    owned=[]
    try: return _validate(root,allow_sample,owned)
    finally:
        for resource in owned: resource.close()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('--allow-sample',action='store_true');a=p.parse_args();validate(a.root,a.allow_sample)
