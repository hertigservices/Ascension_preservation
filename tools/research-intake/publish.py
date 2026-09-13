"""Deliver a verified snapshot using a dedicated clean data checkout.
No broad git-add, forced push, credential handling, or live database writes.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import intake
import sys
# Source and installed deployments both place dataset.py in this lookup path.
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'data-storage'))
import dataset


def git(repo,*args):
    r=subprocess.run(['git','-C',str(repo),*args],capture_output=True,text=True,encoding='utf-8')
    if r.returncode:raise RuntimeError(r.stderr.strip() or 'Git operation failed')
    return r.stdout.strip()


def deliver(snapshot,checkout,push=False,branch='main',identity_guard=None):
    snapshot=Path(snapshot).resolve();checkout=Path(checkout).resolve()
    manifest=intake.verify_export(snapshot,identity_guard)
    if not (checkout/'.ascension-data.json').is_file():raise ValueError('Target must be an ascension-data checkout')
    # The operator creates this opt-in marker only in a dedicated publication
    # worktree. Keep it inside the worktree's private Git directory.
    gd=Path(git(checkout,'rev-parse','--absolute-git-dir'))
    if not (gd/'research-publisher.json').exists():raise ValueError('Use a dedicated checkout initialized with --initialize-checkout; never the active cache publisher')
    with intake.Lock(gd/'research-publication.lock'):
        if git(checkout,'status','--porcelain'):raise ValueError('Publication checkout has unrelated/uncommitted changes')
        if push:
            git(checkout,'fetch','origin',branch)
            git(checkout,'merge','--ff-only','origin/'+branch)
        prefix='supplemental/research-intake/'+manifest['source']+'/'+manifest['snapshot']
        staging=gd/'research-packages'/manifest['snapshot']
        package=dataset.prepare(snapshot,staging,'research-'+manifest['source'],prefix=prefix)
        # Publish and verify bulk assets before a Git commit can advertise them remotely.
        if push:dataset.publish(staging)
        rel=Path('datasets')/('research-'+manifest['source']+'-'+manifest['snapshot']+'.json')
        target=checkout/rel
        if target.exists():
            if json.loads(target.read_text(encoding='utf-8'))!=package:raise ValueError('Existing dataset manifest differs')
        else:
            intake.atomic_json(target,package)
            git(checkout,'add','--',rel.as_posix())
            git(checkout,'commit','-m','Catalog preserved research evidence: '+manifest['source'])
        commit=git(checkout,'rev-parse','HEAD')
        if push:
            # Non-fast-forward rejects safely if the cache publisher won the race.
            # Retry this same command after reconciling the dedicated worktree.
            git(checkout,'push','origin','HEAD:refs/heads/'+branch)
            remote=git(checkout,'ls-remote','origin','refs/heads/'+branch).split()[0]
            if remote!=commit:raise RuntimeError('Remote advanced before receipt verification; publication status requires a fresh ancestry check')
        return {'commit':commit,'snapshot':manifest['snapshot'],'records':manifest['public_records'],'pushed':push,'path':str(target),'package_dir':str(staging),'storage':'github-releases'}


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,default=intake.DEFAULT_ROOT);ap.add_argument('--snapshot',type=Path);ap.add_argument('--checkout',type=Path,required=True);ap.add_argument('--push',action='store_true');ap.add_argument('--branch',default='main');ap.add_argument('--initialize-checkout',action='store_true');a=ap.parse_args()
    if a.initialize_checkout:
        if not (a.checkout/'.ascension-data.json').is_file():ap.error('Not an ascension-data checkout')
        gd=Path(git(a.checkout,'rev-parse','--absolute-git-dir'))
        # A secondary worktree has a .git FILE. Do not opt in the ordinary live checkout.
        if not (a.checkout/'.git').is_file():ap.error('Create a dedicated git worktree first')
        intake.atomic_json(gd/'research-publisher.json',{'schema':1,'checkout':str(a.checkout.resolve())})
        print('Dedicated publication checkout initialized.')
    elif a.snapshot:print(json.dumps(deliver(a.snapshot,a.checkout,a.push,a.branch,intake.privacy.configured(a.root)),indent=2))
    else:ap.error('Pass --snapshot or --initialize-checkout')
if __name__=='__main__':main()
