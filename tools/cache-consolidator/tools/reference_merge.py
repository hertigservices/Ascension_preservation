"""Deduplicated, literal reference snapshots; never execute uploaded Lua.

These captures are preserved separately by content hash. Conflicting references
remain separate observations instead of inventing a field-level winner.
"""
import hashlib, os, re
import luaser

GLOBALS = {'ascension_coareader.lua': 'CoAReaderDB', 'ascensionharvest.lua': 'AscensionHarvestDB'}
DROP = set('profilekeys profiles identity spellsbychar lastguid lasttime player playername character charactername account accountname chat whispers macros hotbars toons calls tokens rapidrollingstate eligibility eligibilityreasons startingchoice'.split())
def table(values=None): return luaser.table_from(values or {})
def get(t,k): return t.get(k) if isinstance(t,luaser.Table) else None
def pairs(t):
    if not isinstance(t,luaser.Table): return []
    return list(enumerate(t.array,1))+list(t.hash.items())
def pick(t,keys): return table({k:get(t,k) for k in keys if get(t,k) is not None})
def nonempty(t): return bool(pairs(t))
def scrub(v,depth=0):
    if depth>64: raise ValueError('Reference nesting limit')
    if isinstance(v,luaser.Table):
        out={}
        for k,x in pairs(v):
            if isinstance(k,str) and k.lower() in DROP: continue
            clean_key=scrub(k,depth+1)
            if clean_key in out:raise ValueError('Redacted reference key collision')
            out[clean_key]=scrub(x,depth+1)
        return table(out)
    if isinstance(v,str):
        return re.sub(r'0x[0-9a-f]{16}','<redacted-guid>',re.sub(r'[\w.+-]+@[\w.-]+\.[\w.-]+','<redacted-email>',v),flags=re.I)
    return v

def ids(t,strings=False):
    return table({k:v for k,v in pairs(t) if ((type(k) is int and k>0) or (isinstance(k,str) and re.fullmatch(r'[1-9][0-9]*',k))) and (not strings or isinstance(v,str))})

def filtered(name,g):
    global_name=GLOBALS[name];value=g.get(global_name)
    if name=='ascension_coareader.lua':
        refs=pick(value,['edges','edgeStats','tree'])
        if not any(nonempty(get(refs,k)) for k in ['edges','tree']): return None
    else:
        branches={}
        for branch in ['realms','sweeps','watch']:
            realms={}
            for realm,record in pairs(get(value,branch)):
                if not isinstance(realm,str):continue
                clean={}
                if branch=='realms':
                    for k in ['items','tips']:
                        data=ids(get(record,k))
                        if nonempty(data):clean[k]=data
                elif branch=='sweeps':
                    for k in ['quest','questtitle']:
                        data=ids(get(get(record,k),'data'))
                        if nonempty(data):clean[k]=table({'data':data})
                else:
                    data=ids(get(record,'spells'),True)
                    if nonempty(data):clean['spells']=data
                if clean:realms[realm]=table(clean)
            if realms:branches[branch]=table(realms)
        if not branches:return None
        refs=table(branches)
    return {global_name:scrub(refs)}

def merge(name,state,g,sid,meta,conflicts):
    refs=filtered(name,g)
    if refs is None:return 0
    text=luaser.dumps(refs)
    digest=hashlib.sha256(text.encode('utf-8')).hexdigest()
    state.setdefault('reference_captures',{}).setdefault(name,{})[digest]=text
    return sum(1 for _ in luaser.walk(next(iter(refs.values()))))

def write(state,out):
    written=[]
    for name,captures in sorted(state.get('reference_captures',{}).items()):
        if name not in GLOBALS:raise ValueError('Unsupported reference capture')
        folder=os.path.join(out,'reference-captures',name[:-4]);os.makedirs(folder,exist_ok=True)
        for digest,text in sorted(captures.items()):
            if hashlib.sha256(text.encode('utf-8')).hexdigest()!=digest:raise ValueError('Reference checksum mismatch')
            if luaser.dumps(luaser.loads(text))!=text:raise ValueError('Reference round-trip mismatch')
            path=os.path.join(folder,digest+'.lua')
            with open(path,'w',encoding='utf-8',newline='\n') as f:f.write(text)
            written.append((path,'privacy-filtered reference snapshot; conflicts preserved separately'))
    return written
