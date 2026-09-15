"""Read-only census of indexed source records and evidence-backed corrections.

Inputs are published/resolved cachedata and supplemental roots, never credentials
or a general filesystem scan. Output contains public paths and aggregate counts.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import attribution
import build


def audit(roots):
    totals=Counter(); files=[]
    for prefix, root in roots:
        for source in sorted(root.rglob('*')):
            if not source.is_file(): continue
            path=prefix+'/'+source.relative_to(root).as_posix()
            kind,_=build.adapter(path)
            totals['files']+=1
            if kind=='reference':
                totals['reference_files']+=1
                continue
            counts=Counter(); before=Counter(); after=Counter(); bases=Counter()
            for row in build.rows(source,kind):
                counts['records']+=1
                if not isinstance(row,dict): row={'value':row}
                old=path.split('/')[2] if path.startswith('cachedata/by-mode/') else str(row.get('_modes',row.get('modes','Unspecified')))
                info=attribution.resolve(path,row)
                new=attribution.mode_text(info) if info else old
                before[old]+=1; after[new]+=1
                if new!=old: counts['changed_mode_records']+=1
                if old.lower() in attribution.EMPTY and info and info['modes']: counts['recovered_mode_records']+=1
                if info:
                    counts[info['status']+'_records']+=1
                    if info['realms']: counts['records_with_realm']+=1
                    for e in info['evidence']: bases[e['basis']]+=1
                if any(k in row for k in ('realm','realms','mode')) and not attribution.affected(path):
                    counts['unhandled_attribution_fields']+=1
            digest=hashlib.sha256(source.read_bytes()).hexdigest()
            files.append({'path':path,'sha256':digest,**counts,'before':dict(before),'after':dict(after),'evidence_basis':dict(bases)})
            totals.update(counts);totals['indexed_files']+=1
    return {'schema':1,'scope':'Published source-record views; duplicate views are counted separately. No realm inferred from matching items or modes.',
            'totals':dict(totals),'files':files}


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--cachedata',type=Path,required=True)
    ap.add_argument('--supplemental',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args()
    result=audit([('cachedata',args.cachedata),('supplemental',args.supplemental)])
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result['totals'],indent=2))
