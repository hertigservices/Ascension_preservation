"""Called by cache publish.py only AFTER its existing complete privacy/quality audit.
The repository storage marker activates release storage without changing the intake UX.
"""
import json,subprocess,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'data-storage'))
import dataset

def enabled(repo):
    marker=Path(repo)/'.ascension-storage.json'
    return marker.exists() and json.loads(marker.read_text(encoding='utf-8-sig')).get('cache')=='github-releases'

def deliver(repo, data, staging, push):
    repo=Path(repo)
    def git(*args):return subprocess.check_output(['git','-C',str(repo),*args],text=True,encoding='utf-8').strip()
    if git('ls-files','cachedata'):raise ValueError('Finish the verified cache migration before activating release publication')
    if git('diff','--cached','--name-only'):raise ValueError('Unrelated staged changes block release publication')
    target=repo/'datasets/cache.json';old=json.loads(target.read_text(encoding='utf-8')) if target.exists() else None
    manifest=dataset.prepare(data,staging,'cache',prefix='cachedata',previous=old)
    if push:dataset.publish(staging)
    dataset.atomic(target,manifest)
    git('add','--','datasets/cache.json')
    if git('diff','--cached','--name-only'):git('commit','-m','Update verified cache dataset manifest')
    return manifest
