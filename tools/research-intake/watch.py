"""Poll the private drop folder; unchanged submissions require no parsing or hashing."""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import time
import intake


def fingerprint(path):
    h=hashlib.sha256();count=0
    def walk(folder):
        nonlocal count
        if intake.linked(folder):raise ValueError('Drop folder contains a symlink/junction')
        entries=list(itertools.islice(folder.iterdir(),100001))
        if len(entries)>100000:raise ValueError('Drop directory exceeds the entry budget')
        for p in sorted(entries):
            if intake.linked(p):raise ValueError('Drop folder contains a symlink/junction')
            if p.is_dir():yield from walk(p)
            elif p.is_file():
                count+=1
                if count>100000:raise ValueError('Drop folder exceeds the file budget')
                yield p
    for p in walk(path):
        s=p.stat();h.update(intake.encoded([p.relative_to(path).as_posix(),s.st_size,s.st_mtime_ns])+b'\n')
    return h.hexdigest()



def sweep(root,stable_seconds=60):
    root=Path(root); inbox=root/'inbox';inbox.mkdir(parents=True,exist_ok=True)
    state_path=root/'watch-state.json'
    state=json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
    results=[]
    for folder in sorted(inbox.iterdir()):
        if not folder.is_dir() or intake.linked(folder):continue
        try:
            signature=fingerprint(folder);previous=state.get(folder.name,{})
            if previous.get('fingerprint')!=signature:
                state[folder.name]={'fingerprint':signature,'first_seen':time.time()};continue
            if previous.get('processed') or time.time()-previous['first_seen']<stable_seconds:continue
            with intake.Intake(root) as e:
                report=e.ingest(folder,folder.name)
                policy=root/'policies'/(folder.name+'.json')
                if policy.exists():
                    public=intake.export(e,folder.name,policy,root.parent/(root.name+'-public-staging'))
                    report['public_export']=str(public)
                # Failed copies retry after another quiet period; held readers stay explicit
                # until a parser/policy is added and the operator chooses reprocess.
                previous['processed']=not report['copy_failures'];previous['first_seen']=time.time()
                previous['report']=report['run'];results.append(report)
        except (ValueError,OSError,RuntimeError) as e:
            results.append({'source':folder.name,'status':'needs-attention','detail':str(e)})
    intake.atomic_json(state_path,state)
    return results


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,default=intake.DEFAULT_ROOT);ap.add_argument('--once',action='store_true');ap.add_argument('--interval',type=int,default=20);a=ap.parse_args()
    if a.interval<5:ap.error('Interval must be at least five seconds')
    while True:
        results=sweep(a.root)
        if results:print(json.dumps(results,ensure_ascii=False,indent=2),flush=True)
        if a.once:return
        time.sleep(a.interval)

if __name__=='__main__':main()
