"""Resolve public release manifests into a verified local build cache."""
import json,sys,hashlib,os,shutil
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'data-storage'))
import dataset

def resolve(data, entries, cache):
    files={p:(data/p,blob) for p,blob in entries};downloads={};used=set()
    for path,_ in entries:
        if not path.startswith('datasets/') or not path.endswith('.json'):continue
        m=dataset.validate(json.loads((data/path).read_text(encoding='utf-8-sig')))
        root=cache/'datasets'/m['snapshot']
        dataset.restore(m,root,cache/'download-packs')
        for name,e in m['files'].items():
            if name in used:raise ValueError('Overlapping dataset manifests: '+name)
            used.add(name);files[name]=(root/name,'sha256:'+e['sha256'])
            downloads[name]={'manifest':path,'release':f"https://github.com/{m['repository']}/releases/tag/{m['release']}"}
    view=data
    if downloads:
        view=cache/'dataset-views'/hashlib.sha256(dataset.canonical({p:b for p,(_,b) in files.items()})).hexdigest()
        for name,(source,_) in files.items():
            target=view/name
            if target.exists():continue
            target.parent.mkdir(parents=True,exist_ok=True)
            try:os.link(source,target)
            except OSError:shutil.copyfile(source,target)
    return sorted((p,blob) for p,(_,blob) in files.items()),{p:src for p,(src,_) in files.items()},downloads,view
