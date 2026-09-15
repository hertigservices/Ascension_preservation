"""Read-only summary of manifest-bound Auctionator data retained by the collector."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import luaser
import modes


def size_text(size):
    if size is None: return '—'
    if size < 1024: return f'{size:,} B'
    if size < 1048576: return f'{size / 1024:.1f} KiB'
    return f'{size / 1048576:.1f} MiB'


@lru_cache(maxsize=256)
def _auctionator(path, modified, size, expected_hash):
    data=Path(path).read_bytes()
    if len(data)!=size or hashlib.sha256(data).hexdigest()!=expected_hash:
        raise ValueError('Payload no longer matches its manifest')
    tree=luaser.loads(data.decode('utf-8')).get('AUCTIONATOR_PRICE_DATABASE')
    if not isinstance(tree,luaser.Table): raise ValueError('Missing price database')
    found=set();unknown=False
    for label,value in tree.hash.items():
        if label=='__dbversion': continue
        if not isinstance(value,luaser.Table): raise ValueError('Unrecognized market branch')
        if not len(value): continue
        matched=False
        for mode in sorted(modes.CANONICAL_MODE_LABELS,key=len,reverse=True):
            suffix=' - '+mode
            if isinstance(label,str) and label.endswith(suffix) and label[:-len(suffix)].strip():
                found.add((label[:-len(suffix)].strip(),modes.MODE_TABLE[mode][0]));matched=True;break
        if not matched: unknown=True
    return tuple(sorted(found)),unknown


def summarize(entry, state):
    """Keep unknown for any unattributed file; never label adjacent WDBs from Lua."""
    if not re.fullmatch(r'[a-f0-9-]{36}',str(entry.get('id',''))): return {}
    original=entry.get('modes',[])
    job=Path(state)/entry['id']
    try:
        provenance=json.loads((job/'provenance.json').read_text())
        if provenance.get('submission')!=entry['id']: return {}
        descriptors=provenance['manifest']['files']
        if not isinstance(descriptors,list) or not all(isinstance(x,dict) for x in descriptors): return {}
        accepted={x['index']:x for x in provenance['validation']['accepted']}
    except (OSError,ValueError,KeyError,TypeError,AttributeError): return {}
    known={x for x in original if x!='unknown'};realms=set();unknown=False;observed=False
    for descriptor in descriptors:
        mode=descriptor.get('mode','unknown')
        if mode!='unknown': known.add(mode)
        resolved=False
        record=accepted.get(descriptor.get('index'),{})
        if descriptor.get('name')=='auctionator_price_database.lua' and isinstance(descriptor.get('size'),int) and re.fullmatch(r'accepted/part-\d+/enUS/auctionator_price_database\.lua',str(record.get('path',''))) and record.get('sha256')==descriptor.get('sha256'):
            for folder in sorted(job.glob('validated-*'),reverse=True):
                p=folder/record['path']
                try:
                    st=p.stat()
                    if p.is_symlink() or not p.resolve().is_relative_to(job.resolve()) or st.st_size>32*1024*1024: continue
                    if st.st_size!=descriptor['size']: continue
                    pairs,unattributed=_auctionator(str(p),st.st_mtime_ns,st.st_size,descriptor['sha256'])
                    realms.update(x[0] for x in pairs);known.update(x[1] for x in pairs)
                    unknown |= unattributed;resolved=True;observed |= bool(pairs);break
                except (OSError,ValueError,UnicodeError,luaser.LuaError): continue
        if mode=='unknown' and not resolved: unknown=True
    if not observed: return {}
    return {'modes':sorted(known)+(['unknown'] if unknown else []),'realms':sorted(realms),
            'attribution_note':'Realm labels come from nonempty, hash-verified Auctionator branches. Other files retain their own mode evidence.'}
