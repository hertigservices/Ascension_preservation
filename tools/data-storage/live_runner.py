"""Local scheduled catalog publisher. Credentials stay off the WD data volume."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from dataset import atomic,canonical
from live_catalog import Archive,Budget,make_client,pointer,current_files,list_objects,promote,cleanup,retained_keys
from live_backup import ArchiveIndex,archive_objects
from r2_publish import inventory


def git(root,*args):return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()


def input_fingerprint(root,source_ref):
    """Ignore receipt and packaging-location-only commits; retain logical file content."""
    entries=[]
    for line in git(root,'ls-tree','-r','HEAD').splitlines():
        meta,path=line.split('\t',1);blob=meta.split()[2]
        if path.startswith(('.github/','cachedata/contributions/')):continue
        if path.startswith('datasets/') and path.endswith('.json'):
            manifest=json.loads((Path(root)/path).read_text())
            if manifest.get('schema')=='ascension-dataset-1':
                entries.append((path,{name:{k:v[k] for k in ('bytes','sha256')} for name,v in manifest['files'].items()}));continue
        entries.append((path,blob))
    return hashlib.sha256(canonical({'source_ref':source_ref,'inputs':entries})).hexdigest()


def checkout(path,repo,ref='main'):
    new=not (path/'.git').exists()
    if new:
        subprocess.run(['git','clone','--filter=blob:none','--no-checkout','https://github.com/'+repo+'.git',str(path)],check=True)
        git(path,'config','core.filemode','false')
    if not new and git(path,'status','--porcelain','--untracked-files=no'):raise ValueError('Publisher checkout has local tracked edits')
    git(path,'fetch','--no-tags','origin',ref)
    git(path,'checkout','--detach','--force','FETCH_HEAD')
    return git(path,'rev-parse','HEAD')


def maintain(client,archive,index,run):
    current,_=pointer(client);retain=retained_keys(client,current)
    objects=list_objects(client)
    obsolete={k:v for k,v in objects.items() if k.startswith('catalog/') and k not in retain}
    if not obsolete:return {'deleted_objects':0,'remaining_objects':len(objects),'remaining_bytes':sum(o['bytes'] for o in objects.values())}
    archive_objects(client,archive,index,obsolete)
    return cleanup(client,archive,index,journal=run/'delete.jsonl')


def run(config,*,force=False):
    archive=Archive(config['backup_root'],config['volume_uuid'])
    if shutil.disk_usage(archive.root).free < config.get('minimum_free_bytes',20000000000):raise ValueError('WD backup disk has insufficient free space')
    client=make_client(config['credential_file']);index=ArchiveIndex(archive.root)
    started=dt.datetime.now(dt.timezone.utc);run_dir=archive.root/'runs'/started.strftime('%Y%m%dT%H%M%S%fZ');run_dir.mkdir(parents=True)
    state_path=archive.root/'publisher-state.json'
    state=json.loads(state_path.read_text()) if state_path.exists() else {}
    def status(**value):
        value.update(checked_at=dt.datetime.now(dt.timezone.utc).isoformat(),run_directory=str(run_dir))
        atomic(archive.root/'publisher-status.json',value)
        print(json.dumps(value),flush=True)
    try:
        # No background deletion while the legacy CI writer might be active.
        workflow=json.loads(subprocess.check_output(['gh','api','repos/hertigservices/ascension-data/actions/workflows/database-site.yml'],text=True))
        if workflow['state']!='disabled_manually':raise ValueError('Legacy website workflow must remain disabled')
        runs=json.loads(subprocess.check_output(['gh','run','list','-R','hertigservices/ascension-data','--limit','10','--json','status,workflowName'],text=True))
        if any(r['status']!='completed' and r['workflowName']=='Update public database website' for r in runs):raise ValueError('Legacy publication still active')
        if pointer(client)[0].get('storage')!='ascension-live-layout-1':raise ValueError('Complete the WD-backed live-only migration before scheduling publication')
        pending_path=archive.root/'pending-publication.json'
        status(state='checking_retention')
        # Saved uploads are active work, not garbage. Reuse their exact uploaded keys.
        retention=({'deferred_for_pending_publication':True} if pending_path.exists() else maintain(client,archive,index,run_dir))
        status(state='checking_inputs',retention=retention)
        work=archive.root/'work';work.mkdir(exist_ok=True)
        data=work/'data';data_ref=checkout(data,'hertigservices/ascension-data')
        caller=(data/'.github/workflows/database-site.yml').read_text()
        match=re.search(r'^\s*source_ref:\s*([0-9a-f]{40})\s*$',caller,re.M)
        if not match:raise ValueError('Data workflow must specify an immutable source_ref for the local builder')
        source_ref=match[1];fingerprint=input_fingerprint(data,source_ref)
        if fingerprint==state.get('fingerprint'):
            status(state='unchanged',current=pointer(client)[0],retention=retention);return
        last=state.get('published_at',0)
        if not force and time.time()-last < config.get('minimum_publication_interval_seconds',86400):
            status(state='waiting_for_daily_publication',next_eligible_epoch=last+config.get('minimum_publication_interval_seconds',86400),current=pointer(client)[0]);return
        # A completed staged output is reused byte-for-byte after upload failure.
        if pending_path.exists():
            pending=json.loads(pending_path.read_text());stage=Path(pending['stage']);report=json.loads((stage.parent/'storage-report.json').read_text())
            if not stage.is_relative_to(work):raise ValueError('Pending output outside WD work directory')
            fingerprint=pending['fingerprint'];data_ref=pending['data_ref'];source_ref=pending['source_ref']
            # inventory revalidates structure, identity, and actual output bytes before a resumed upload.
            if inventory(stage)!=report:raise ValueError('Pending validated output changed')
        else:
            source=work/'source';checkout(source,'hertigservices/Ascension_preservation',source_ref)
            build=work/'builds'/started.strftime('%Y%m%dT%H%M%S%fZ');stage=build/'catalog';build.mkdir(parents=True)
            before=pointer(client)[0]
            status(state='building',data_ref=data_ref,source_ref=source_ref)
            with (build/'build.log').open('w') as log:
                subprocess.run([sys.executable,'-u',str(source/'tools/ascension-db/build.py'),'--data',str(data),'--out',str(stage),'--cache',str(work/'build-cache'),'--hosting','r2','--icon-base','https://ascension-public-data.ascension-archive.workers.dev/images/icons/'],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=10800)
            report=inventory(stage);atomic(build/'storage-report.json',report)
            if pointer(client)[0]!=before:raise ValueError('Current changed during build; publication requires reconciliation')
            pending={'stage':str(stage),'fingerprint':fingerprint,'data_ref':data_ref,'source_ref':source_ref}
            atomic(pending_path,pending)
        status(state='archiving_and_publishing',snapshot=report['snapshot'])
        budget=Budget(archive.root/'write-budget.json',limit=config['monthly_write_attempt_limit'],day=config.get('billing_cycle_day',10))
        result=promote(client,archive,budget,report,stage,max_public_bytes=config['maximum_public_bucket_bytes'],max_new_objects=config['maximum_new_objects_per_publication'])
        state={'fingerprint':fingerprint,'published_at':time.time(),'data_ref':data_ref,'source_ref':source_ref,'snapshot':report['snapshot']}
        atomic(state_path,state)
        if pending_path.exists():pending_path.unlink()
        status(state='removing_superseded_objects',publication=result)
        # The incoming tree is already archived. Save exact remote receipts before garbage collection.
        post_run=run_dir/'after-promotion';post_run.mkdir()
        retention=maintain(client,archive,index,post_run)
        status(state='published',publication=result,retention=retention,budget=budget.data)
    except Exception as exc:
        try:current=pointer(client)[0]
        except Exception:current=None
        status(state='paused',reason=str(exc),current=current)
        raise
    finally:index.db.close()


def main():
    # Filesystem mounting and the production process lock belong to the Linux entry point.
    # Fingerprinting and archive rules remain importable for cross-platform verification.
    import fcntl
    ap=argparse.ArgumentParser();ap.add_argument('--config',type=Path,required=True);ap.add_argument('--publish-now',action='store_true',help='Waive only the daily interval; all archive/storage/write limits still apply');args=ap.parse_args()
    config=json.loads(args.config.read_text())
    lock_path=args.config.with_suffix('.lock')
    with lock_path.open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:print('Catalog publisher already running');return
        run(config,force=args.publish_now)


if __name__=='__main__':main()
