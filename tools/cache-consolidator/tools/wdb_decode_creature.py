"""Decode raw creaturecache.wdb records (SMSG_CREATURE_QUERY_RESPONSE payloads,
build 12340) into a creature_template-shaped TSV.

Self-validating: each record's payload length is known from the WDB record walk, so a
correct field layout consumes EXACTLY that many bytes. We tally exact/leftover/overrun
per record; ~100% exact == the binary layout is proven.

Unions every distinct (by sha) non-empty creaturecache across all realms/modes, dedup by
entry (first snapshot wins; conflicts are not merged).
Native python can't see /c/... -> C:/... paths only.
"""
import os, sys, struct, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wdblib, config

# SCAN_ROOTS, not EXTRA_SCAN_ROOTS: the latter omits _inbox, so a bare .wdb dropped
# straight in there was ledgered by intake and then silently skipped at decode time.
SCAN = [config.EXTRACT] + config.SCAN_ROOTS
OUT_TSV = config.WORK + "/decoded/creature.tsv"
OUT_REP = config.WORK + "/decoded/creature_report.md"

COLS = ["entry","name","subname","IconName","type_flags","type","family","rank",
        "KillCredit1","KillCredit2","modelid1","modelid2","modelid3","modelid4",
        "HealthModifier","ManaModifier","RacialLeader","movementId"] + ["questItem%d" % i for i in range(1, 7)]

def sanitize(s):
    return s.replace("\t"," ").replace("\r"," ").replace("\n"," ").replace("\\","/")

class Cur:
    __slots__=("b","o","n")
    def __init__(self,b): self.b=b; self.o=0; self.n=len(b)
    def u32(self):
        v=struct.unpack_from("<I",self.b,self.o)[0]; self.o+=4; return v
    def i32(self):
        v=struct.unpack_from("<i",self.b,self.o)[0]; self.o+=4; return v
    def u8(self):
        v=self.b[self.o]; self.o+=1; return v
    def f32(self):
        v=struct.unpack_from("<f",self.b,self.o)[0]; self.o+=4; return v
    def cstr(self):
        e=self.b.find(b"\x00",self.o)
        if e<0: e=self.n
        s=self.b[self.o:e]; self.o=e+1
        return wdblib.decode_str(s)

def fmtf(v):
    # keep float readable: 1.0 not 1; drop trailing noise
    if v==int(v): return f"{v:.1f}"
    return repr(round(v,6))

def decode_creature(entry, payload):
    # WDB record body starts at Name[0]; `entry` is from the record header, NOT repeated.
    c=Cur(payload); d={k:0 for k in COLS}
    d["entry"]=entry
    n0=c.cstr(); c.cstr(); c.cstr(); c.cstr()          # Name[0..3]
    d["name"]=sanitize(n0)
    d["subname"]=sanitize(c.cstr())                    # SubName / title
    d["IconName"]=sanitize(c.cstr())                   # IconName
    d["type_flags"]=c.u32()
    d["type"]=c.u32()
    d["family"]=c.u32()
    d["rank"]=c.u32()
    d["KillCredit1"]=c.u32()
    d["KillCredit2"]=c.u32()
    d["modelid1"]=c.u32(); d["modelid2"]=c.u32(); d["modelid3"]=c.u32(); d["modelid4"]=c.u32()
    d["HealthModifier"]=fmtf(c.f32())
    d["ManaModifier"]=fmtf(c.f32())
    d["RacialLeader"]=c.u8()
    for i in range(1, 7): d["questItem%d" % i] = c.u32()
    d["movementId"]=c.u32()
    return d, c.o

def iter_caches():
    seen=set()
    for root in SCAN:
        if not os.path.isdir(root): continue
        for dp,_dn,fs in os.walk(root):
            for fn in fs:
                if fn.lower()!="creaturecache.wdb": continue
                p=os.path.join(dp,fn)
                b=open(p,"rb").read()
                h=hashlib.sha256(b).hexdigest()
                if h in seen: continue
                seen.add(h)
                yield p.replace("\\","/"), b

def main():
    os.makedirs(os.path.dirname(OUT_TSV), exist_ok=True)
    union={}                       # entry -> row
    per_file=[]                    # (label, records, new, path)
    exact=leftover=overrun=err=0
    gaps={}                        # (size-consumed) -> count for leftover diagnosis
    spot={}
    for path,b in iter_caches():
        info=wdblib.inspect(path)
        cnt=0; new=0
        if not info.standard:
            per_file.append((os.path.basename(os.path.dirname(path)),0,0,path,"non-standard/empty"))
            continue
        for entry,size,payload in wdblib.iter_records(b):
            try:
                row,consumed=decode_creature(entry,payload)
            except Exception:
                err+=1; continue
            if   consumed==size: exact+=1
            elif consumed<size:  leftover+=1; gaps[size-consumed]=gaps.get(size-consumed,0)+1
            else:                overrun+=1; gaps[size-consumed]=gaps.get(size-consumed,0)+1
            cnt+=1
            if entry not in union:
                union[entry]=row; new+=1
        per_file.append((os.path.basename(os.path.dirname(path)),cnt,new,path,""))

    with open(OUT_TSV,"w",encoding="utf-8",newline="") as w:
        w.write("\t".join(COLS)+"\n")
        for e in sorted(union):
            r=union[e]
            w.write("\t".join(str(r[c]) for c in COLS)+"\n")

    total=exact+leftover+overrun
    L=[]
    L.append("# WDB creaturecache decode -- union across all realms/modes\n")
    L.append(f"Distinct non-empty creaturecaches decoded: {sum(1 for x in per_file if x[1]>0)}\n")
    L.append(f"**Union of unique creature entries: {len(union):,}**\n")
    L.append("## Layout validation (bytes consumed vs declared record size)")
    L.append(f"- exact match : {exact:,}  ({100*exact/max(1,total):.2f}%)")
    L.append(f"- leftover (short parse) : {leftover:,}")
    L.append(f"- overrun (BAD layout)   : {overrun:,}")
    L.append(f"- decode error           : {err:,}\n")
    if gaps:
        L.append("## Consumption gap histogram (size - consumed) for non-exact")
        for g in sorted(gaps):
            L.append(f"- gap {g:+d} bytes : {gaps[g]:,} records")
        L.append("")

    # spot-checks: pick a few well-known-looking entries if present
    wanted=[6491, 3573, 197, 1, 68]  # Spirit Healer, Innkeeper-ish, guards etc (may vary)
    L.append("## Spot-check (first 6 entries with a subname, then low ids)")
    picks=[]
    for e in sorted(union):
        r=union[e]
        if r["subname"] and len(picks)<6:
            picks.append(r)
    for e in sorted(union)[:4]:
        picks.append(union[e])
    seen_e=set()
    for r in picks:
        if r["entry"] in seen_e: continue
        seen_e.add(r["entry"])
        L.append(f"- entry {r['entry']}: name='{r['name']}' subname='{r['subname']}' "
                 f"type={r['type']} rank={r['rank']} model1={r['modelid1']}")
    L.append("")

    L.append("## Per-creaturecache contribution (records / new-to-union)")
    for label,cnt,new,path,note in sorted(per_file,key=lambda x:-x[1]):
        tag=f"  [{note}]" if note else ""
        L.append(f"- {label:36} {cnt:6,} records, {new:6,} new{tag}")
    L.append("")

    # custom Ascension creatures: high entry ids / unusual
    highs=[union[e] for e in sorted(union) if e>=100000]
    L.append(f"## Custom / high-id creatures (entry >= 100000): {len(highs):,}")
    for r in highs[:30]:
        L.append(f"- entry {r['entry']}: name='{r['name']}' subname='{r['subname']}' type={r['type']}")
    L.append("")

    open(OUT_REP,"w",encoding="utf-8").write("\n".join(L)+"\n")
    print(f"union={len(union)} exact={exact} leftover={leftover} overrun={overrun} err={err}")
    if total: print(f"exact%={100*exact/total:.2f}")
    if gaps: print("gaps:",dict(sorted(gaps.items())))
    print("->",OUT_TSV)

if __name__=="__main__":
    main()
