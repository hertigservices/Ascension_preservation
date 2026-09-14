"""Release-backed cache data with small Git receipts for upload acknowledgments.
Called only AFTER the existing complete privacy/quality audit.
"""
import json,re,subprocess,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'data-storage'))
import dataset

def enabled(repo):
    marker=Path(repo)/'.ascension-storage.json'
    return marker.exists() and json.loads(marker.read_text(encoding='utf-8-sig')).get('cache')=='github-releases'

def receipt_path(name):
    return re.fullmatch(r'cachedata/contributions/[0-9a-f]{64}\.json',name) is not None

def check_receipts(data):
    folder=Path(data)/'contributions'
    for path in folder.rglob('*') if folder.exists() else []:
        if path.is_dir():raise ValueError('Unexpected contribution receipt directory')
        name='cachedata/'+path.relative_to(data).as_posix()
        if path.is_symlink() or not receipt_path(name) or path.stat().st_size>4096:raise ValueError('Invalid contribution receipt path or size')
        proof=json.loads(path.read_text(encoding='utf-8'))
        if set(proof)!={'schema','bundle','revision','no_op_eligible'} or proof['schema']!=2 or proof['bundle']!=path.stem or not isinstance(proof['no_op_eligible'],bool) or not re.fullmatch('[0-9a-f]{64}',proof['revision']):raise ValueError('Invalid contribution receipt')

def deliver(repo, data, staging, push):
    repo=Path(repo)
    def git(*args):return subprocess.check_output(['git','-C',str(repo),*args],text=True,encoding='utf-8').strip()
    tracked=git('ls-files','cachedata').splitlines()
    if any(not receipt_path(p) for p in tracked):raise ValueError('Finish the verified cache migration before activating release publication')
    if git('diff','--cached','--name-only'):raise ValueError('Unrelated staged changes block release publication')
    check_receipts(data)
    target=repo/'datasets/cache.json';old=json.loads(target.read_text(encoding='utf-8')) if target.exists() else None
    manifest=dataset.prepare(data,staging,'cache',prefix='cachedata',previous=old)
    if push:dataset.publish(staging)
    # The receipt in Git must be the exact bytes included in the public snapshot.
    check_receipts(data)
    for name,entry in manifest['files'].items():
        if receipt_path(name) and dataset.digest(repo/name)!=entry['sha256']:raise ValueError('Contribution receipt changed during publication')
    dataset.atomic(target,manifest)
    git('add','--','datasets/cache.json')
    if tracked or (Path(data)/'contributions').exists():git('add','-A','--','cachedata/contributions')
    staged=git('diff','--cached','--name-only').splitlines()
    if any(p!='datasets/cache.json' and not receipt_path(p) for p in staged):raise ValueError('Unexpected staged publication path')
    if staged:git('commit','-m','Update verified cache dataset manifest and contribution receipts')
    return manifest
