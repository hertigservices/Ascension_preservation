"""Private HTTP intake bridge. No raw archives, Lua execution, or public inbound port.
Run under a dedicated non-admin account. Secrets are read from the environment or a private token file.
"""
from __future__ import annotations
import logging
import threading
import urllib.error
from logging.handlers import RotatingFileHandler
import argparse, hashlib, json, os, re, shutil, subprocess, sys, time, urllib.request, uuid
from pathlib import Path

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MAX_FILE = 4 * 1024 * 1024
ROOT = Path(__file__).resolve().parent

def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2), encoding='utf-8')
    os.replace(temp, path)

class Client:
    def __init__(self, url, token):
        self.url = url.rstrip('/')
        if not self.url.startswith('https://') and not re.match(r'^http://(127\.0\.0\.1|localhost):\d+$', self.url):
            raise ValueError('Collector URL must be HTTPS (loopback permitted for tests)')
        self.token = token
    def call(self, path, payload=None, lease=None, binary=False):
        headers = {'Authorization': 'Bearer ' + self.token, 'User-Agent': 'AscensionArchive-Collector/1.0'}
        if lease: headers['X-Lease'] = lease
        data = None if payload is None else json.dumps(payload).encode()
        if data is not None: headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(self.url + '/api/' + path, data=data, headers=headers)
        # Never forward the collector bearer secret to an HTTP redirect.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs): return None
        with urllib.request.build_opener(NoRedirect).open(req, timeout=45) as response:
            result = response.read(MAX_FILE + 1 if binary else 250001)
            if len(result) > (MAX_FILE if binary else 250000): raise ValueError('Response exceeds limit')
            return result if binary else json.loads(result)

def policy_contract():
    return json.loads((ROOT.parent/'shared'/'policy-contract.json').read_text(encoding='utf-8'))


def validate_manifest(m):
    contract=policy_contract()
    if not isinstance(m,dict):raise ValueError('Invalid manifest')
    files=m.get('files')
    if type(m.get('policy')) is not int or m['policy']!=contract['policy'] or not isinstance(files,list) or not 1<=len(files)<=256:raise ValueError('Invalid manifest')
    total=0
    for i,f in enumerate(files):
        if not isinstance(f,dict) or type(f.get('index')) is not int or f['index']!=i or type(f.get('size')) is not int or not 0<f['size']<=MAX_FILE:raise ValueError('Invalid file descriptor')
        if f.get('name') not in contract['names'] or f.get('mode') not in contract['modes'] or not isinstance(f.get('sha256'),str) or not re.fullmatch('[a-f0-9]{64}',f['sha256']):raise ValueError('Invalid file descriptor')
        total+=f['size']
    if total>512*1024*1024:raise ValueError('Oversized manifest')
    return files


def validator_environment():
    return {k: v for k, v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','TEMP','TMP','PATH','PATHEXT'}}

def delivery_files(validated):
    accepted=validated/'accepted'
    return {p.relative_to(accepted).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(accepted.rglob('*')) if p.is_file()}

def delivery_name(validated):
    digest=hashlib.sha256()
    for rel,sha in delivery_files(validated).items():
        digest.update(str(Path(rel)).encode());digest.update(bytes.fromhex(sha))
    return 'web-'+digest.hexdigest()[:32]

def handoff(validated, inbox, submission):
    """Atomic directory arrival; retries compare every accepted file byte-for-byte."""
    accepted = validated / 'accepted'
    # Content-address the exact accepted bundle so repeat submissions do not
    # manufacture new provenance paths. Partial overlap is still not proof
    # of independent contributors; the existing archive records observations.
    destination = inbox / delivery_name(validated)
    if not accepted.exists(): return False
    if destination.exists():
        actual={p.relative_to(destination).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in destination.rglob('*') if p.is_file()}
        if actual!=delivery_files(validated):raise ValueError('Existing inbox delivery differs; refusing overwrite')
        return True
    inbox.mkdir(parents=True, exist_ok=True)
    # Staging must share the inbox volume for atomic rename. Caller enforces this.
    os.rename(accepted, destination)
    return True

def published_head(repo):
    def git(*args):
        return subprocess.check_output(['git','-C',str(repo),*args],text=True,timeout=60,creationflags=NO_WINDOW).strip()
    branch=git('symbolic-ref','--short','HEAD')
    head=git('rev-parse','HEAD')
    remote=git('ls-remote','origin','refs/heads/'+branch).split()
    if not remote or remote[0] != head: raise RuntimeError('Publication not confirmed at remote branch')
    return head

class Leases:
    """Keep all claims alive, including during slow Git and filesystem checks."""
    def __init__(self, client):
        self.client=client;self.claims={};self.renewed={};self.lost=set()
        self.lock=threading.RLock();self.renew_lock=threading.Lock();self.stopping=threading.Event();self.thread=None
    def start(self):
        def loop():
            while not self.stopping.wait(30):self.tick()
        self.thread=threading.Thread(target=loop,daemon=True);self.thread.start()
    def close(self):
        self.stopping.set()
        if self.thread:self.thread.join(timeout=5)
    def add(self, claim):
        with self.lock:self.claims[claim['id']]=claim;self.renewed[claim['id']]=time.monotonic()
    def remove(self, sid):
        with self.lock:self.claims.pop(sid,None)
    def assert_active(self, jobs):
        with self.lock:
            if any(j['claim']['id'] in self.lost for j in jobs):raise RuntimeError('Batch lost a submission lease before publishing')
    def tick(self, force=False):
        with self.renew_lock:
            with self.lock:claims=list(self.claims.items())
            for sid,claim in claims:
                with self.lock:
                    if sid not in self.claims or sid in self.lost:continue
                    if not force and time.monotonic()-self.renewed[sid]<300:continue
                try:
                    self.client.call(f'collector/{sid}/renew',{},claim['lease'])
                    with self.lock:self.renewed[sid]=time.monotonic()
                except Exception as error:
                    with self.lock:
                        self.renewed[sid]=time.monotonic()-270
                        if isinstance(error,urllib.error.HTTPError) and error.code==409:self.lost.add(sid)
                    logging.getLogger('ascension-upload-collector').exception('Lease renewal failed for %s',sid)


def processing_rules(config):
    paths=[ROOT/'validate.mjs', ROOT.parent/'shared'/'policy.mjs', ROOT.parent/'shared'/'policy-contract.json']
    if config.get('publisher'):
        folder=Path(config['publisher']).parent
        paths += sorted(folder.glob('*.py')) + sorted(folder.glob('*.json'))
    return [{'path':str(path.resolve()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()} for path in paths]


def processing_revision(config):
    """Invalidate no-op evidence when validation or consolidation rules change."""
    digest=hashlib.sha256(b'published-bundle-v1\0')
    for item in processing_rules(config):
        digest.update(Path(item['path']).name.encode()+b'\0'+bytes.fromhex(item['sha256']))
    return digest.hexdigest()


def bundle_key(manifest, revision):
    # Same bytes in another mode are meaningful provenance. Ignore only the
    # transport order, never mode/name, repeated parts, or processing version.
    parts=sorted((f['name'],f['mode'],f['size'],f['sha256']) for f in manifest['files'])
    return hashlib.sha256(json.dumps([revision,manifest['policy'],parts],separators=(',',':')).encode()).hexdigest()


def prepare_submission(client, claim, config, leases):
    sid=claim['id']
    if str(uuid.UUID(sid)) != sid: raise ValueError('Invalid submission ID')
    files=validate_manifest(claim['manifest'])
    state=Path(config['state']).resolve(); inbox=Path(config['inbox']).resolve()
    if state == inbox or inbox in state.parents or state in inbox.parents:
        raise ValueError('State and inbox must be separate sibling trees')
    if state.drive.lower()!=inbox.drive.lower(): raise ValueError('State and inbox must share a volume')
    job=state/sid; job.mkdir(parents=True,exist_ok=True)
    atomic_json(job/'retention.json',{'expires':claim.get('expires',int(time.time())+7*86400)})
    if (job/'result.json').exists():
        try:
            journal=json.loads((job/'result.json').read_text())
            revision=processing_revision(config);key=bundle_key(claim['manifest'],revision)
            result=journal['result']
            valid=journal.get('schema')==2 and journal.get('submission')==sid and journal.get('bundle')==key
            valid=valid and result.get('status') in ('published','needs_review')
            if valid and (result.get('commit') or result['status']=='published'):
                valid=verified_commit(config['publish_repo'],result.get('commit',''),key)
            if valid:return {'claim':claim,'job':job,'cached':result,'key':key,'revision':revision}
        except (OSError,ValueError,KeyError,TypeError):pass

    raw=job/'download';raw.mkdir(exist_ok=True);atomic_json(raw/'manifest.json',claim['manifest'])
    for f in files:
        leases.tick()
        path=raw/str(f['index'])
        if path.exists() and path.stat().st_size==f['size'] and hashlib.sha256(path.read_bytes()).hexdigest()==f['sha256']:continue
        data=client.call(f'collector/{sid}/files/{f["index"]}',lease=claim['lease'],binary=True)
        if len(data)!=f['size'] or hashlib.sha256(data).hexdigest()!=f['sha256']:raise ValueError('Download checksum mismatch')
        temp=path.with_suffix('.part');temp.write_bytes(data);os.replace(temp,path)
    validated=job/('validated-'+uuid.uuid4().hex);validated.mkdir()
    command=[config.get('node','node'),'--max-old-space-size=128',str(ROOT/'validate.mjs'),str(raw),str(validated)]
    with (job/'validation.log').open('wb') as log:
        timeout=max(120,min(1800,len(files)*20));started=time.monotonic()
        with subprocess.Popen(command,env=validator_environment(),stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,creationflags=NO_WINDOW) as process:
            while process.poll() is None:
                leases.tick()
                if time.monotonic()-started>timeout:
                    process.kill();process.wait();raise TimeoutError('Validator exceeded batch-scaled timeout')
                time.sleep(.1)
            if process.returncode:raise RuntimeError('Validation failed; see private validation.log')
    report=json.loads((validated/'validation.json').read_text())
    entries=report['accepted']+report['review']
    if report.get('policy')!=claim['manifest']['policy'] or sorted(e['index'] for e in entries)!=list(range(len(files))):raise ValueError('Incomplete validator report')
    contract=policy_contract();expected={}
    for kind in ('accepted','review'):
        for entry in report[kind]:
            f=files[entry['index']];want='review' if f['name'] in contract['review'] else 'accepted'
            folder=contract['folders'][f['mode']];folder='enUS' if folder=='unknown' else folder
            rel=f"{want}/part-{f['index']:04d}/{folder}/{f['name']}"
            if kind!=want or entry['path']!=rel or entry['sha256']!=f['sha256']:raise ValueError('Validator classification or path differs')
            expected[rel]=f['sha256']
    actual={p.relative_to(validated).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in validated.rglob('*') if p.is_file() and p.name!='validation.json'}
    if actual!=expected:raise ValueError('Validator output inventory differs')
    atomic_json(job/'provenance.json',{'submission':sid,'manifest':claim['manifest'],'validation':report,'expires':claim['expires'],'identity':'anonymous; not independent corroboration'})
    review=bool(report['review'])
    if review and not (job/'review').exists():os.rename(validated/'review',job/'review')
    return {'claim':claim,'job':job,'validated':validated,'accepted':bool(report['accepted']),'review':review}


def commit_has_bundle(repo, commit, key, require_noop=False):
    try:
        data=subprocess.check_output(['git','-C',repo,'show',f'{commit}:cachedata/contributions/{key}.json'],stderr=subprocess.DEVNULL,timeout=60,creationflags=NO_WINDOW)
        proof=json.loads(data)
        return (not require_noop or proof.get('no_op_eligible') is True) and proof.get('schema')==2 and proof.get('bundle')==key and bool(re.fullmatch('[a-f0-9]{64}',proof.get('revision','')))
    except (OSError,subprocess.SubprocessError,ValueError):return False


def verified_commit(repo, commit, key):
    try:
        if not re.fullmatch('[a-f0-9]{40}',commit):return False
        head=published_head(repo)
        result=subprocess.run(['git','-C',repo,'merge-base','--is-ancestor',commit,head],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60,creationflags=NO_WINDOW)
        return result.returncode==0 and commit_has_bundle(repo,commit,key)
    except (OSError,subprocess.SubprocessError,RuntimeError):return False


def save_result(job, result):
    atomic_json(job['job']/'result.json',{'schema':2,'submission':job['claim']['id'],'bundle':job.get('key'),'result':result})


def acknowledge(client, job, result):
    sid=job['claim']['id'];pending=job['job']/'pending-ack.json'
    # Write the outbox before HTTP; a lost response never downgrades published.
    atomic_json(pending,{'submission':sid,'lease':job['claim']['lease'],'result':result,'bundle':job.get('key'),'revision':job.get('revision')})
    client.call(f'collector/{sid}/ack',result,job['claim']['lease'])
    pending.unlink(missing_ok=True)


def replay_pending_acks(client,state,config=None):
    for pending in Path(state).glob('*/pending-ack.json'):
        try:
            item=json.loads(pending.read_text());sid=item['submission']
            if pending.parent.name!=sid or str(uuid.UUID(sid))!=sid:continue
            client.call(f'collector/{sid}/renew',{},item['lease'])
            client.call(f'collector/{sid}/ack',item['result'],item['lease'])
            pending.unlink(missing_ok=True)
            logging.getLogger('ascension-upload-collector').info('Submission %s acknowledgment recovered',sid)
        except urllib.error.HTTPError as error:
            if error.code==409 and config and item['result'].get('commit'):
                # Do not discard proof merely because the old lease expired,
                # especially after attempt five. The server refuses takeover of
                # a live claim or intentional review hold.
                if verified_commit(config['publish_repo'],item['result']['commit'],item['bundle']):
                    try:
                        client.call(f'collector/{sid}/finalize',{**item['result'],'bundle':item['bundle'],'revision':item['revision']})
                        pending.unlink(missing_ok=True)
                    except Exception:logging.getLogger('ascension-upload-collector').exception('Publication reconciliation remains pending')
        except Exception:logging.getLogger('ascension-upload-collector').exception('Pending result acknowledgment failed')


def verified_bundles(config, keys):
    path=Path(config['state'])/'published-bundles.json'
    if not path.exists():return {}
    try:data=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):return {}
    evidence=data.get('bundles',{}) if isinstance(data,dict) and data.get('schema')==1 and isinstance(data.get('bundles'),dict) else {}
    candidates={k:evidence[k] for k in keys if isinstance(evidence.get(k),str) and re.fullmatch('[a-f0-9]{40}',evidence[k])}
    if not candidates:return {}
    try:
        head=published_head(config['publish_repo'])
        verified={}
        for commit in set(candidates.values()):
            if not re.fullmatch('[a-f0-9]{40}',commit):continue
            result=subprocess.run(['git','-C',config['publish_repo'],'merge-base','--is-ancestor',commit,head],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60,creationflags=NO_WINDOW)
            if result.returncode==0:verified[commit]=True
        return {k:c for k,c in candidates.items() if c in verified and commit_has_bundle(config['publish_repo'],c,k,require_noop=True)}
    except (OSError,subprocess.SubprocessError,RuntimeError):
        # Offline/unpushed history is not evidence of publication. Use the
        # ordinary checked publishing path rather than guess success.
        return {}


def run_publisher(config, jobs, leases):
    leader=jobs[0]['job'];ids=[j['claim']['id'] for j in jobs]
    for item in jobs:
        atomic_json(item['job']/'batch.json',{'submissions':ids,'publisher_submission':ids[0]})
    unique={j['key']:j for j in jobs}
    plan={'schema':1,'rules':processing_rules(config),'jobs':[{'bundle':key,'revision':j['revision'],'wdb_only':all(f['name'].endswith('.wdb') for f in j['claim']['manifest']['files']),'source':str(j['validated']/'accepted'),'destination':delivery_name(j['validated']),'files':delivery_files(j['validated'])} for key,j in unique.items()]}
    plan_path=leader/'publication-plan.json';atomic_json(plan_path,plan)
    env=os.environ.copy();env['ASCENSION_BATCH_PLAN']=str(plan_path);env.update({'ASCENSION_CACHE_WORK':config['work'],'ASCENSION_CACHE_OUT':config['out'],'CONSOLIDATOR_REPO':config['publish_repo']})
    leases.tick(force=True);leases.assert_active(jobs)
    with (leader/'publisher.log').open('ab') as log:
        while True:
            leases.assert_active(jobs)
            process=subprocess.Popen([config.get('python',sys.executable),'-u','-B',config['publisher'],'--push'],env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,creationflags=NO_WINDOW)
            while process.poll() is None:
                leases.tick();time.sleep(1)
            if process.returncode==75:
                time.sleep(20);leases.tick();continue
            if process.returncode:raise RuntimeError('Publisher failed its checks or push; see private publisher.log')
            break
    commit=published_head(config['publish_repo'])
    if not all(commit_has_bundle(config['publish_repo'],commit,key) for key in unique):raise RuntimeError('Published commit lacks batch evidence')
    return commit


def finish_batch(client, jobs, config, leases, publish=True):
    results={}; fresh=[j for j in jobs if 'cached' not in j]
    for j in jobs:
        if 'cached' in j:results[j['claim']['id']]=j['cached']
    known={};revision=None
    if publish and fresh:
        revision=processing_revision(config)
        for j in fresh:
            j['revision']=revision;j['key']=bundle_key(j['claim']['manifest'],revision)
        known=verified_bundles(config,[j['key'] for j in fresh if not j['review']])
    pending=[]; representatives={}
    for j in fresh:
        sid=j['claim']['id']
        if publish and not j['review'] and j['key'] in known:
            results[sid]={'status':'published','commit':known[j['key']],'duplicate':True}
        elif not j['accepted']:
            results[sid]={'status':'needs_review'}
        else:
            # Within one batch, exact repeats share one delivered bundle. Keep
            # every contributor's separate receipt and private provenance.
            key=j.get('key',sid)
            if key not in representatives:
                if not publish:handoff(j['validated'],Path(config['inbox']),sid)
                representatives[key]=j
            pending.append(j)
    if not publish:
        for j in pending:results[j['claim']['id']]={'status':'staged','review':j['review']}
        # Preserve stage-only isolation: accepted jobs are never marked published.
        for j in jobs:
            sid=j['claim']['id']
            if results[sid]['status']!='staged':
                atomic_json(j['job']/'result.json',results[sid]);client.call(f'collector/{sid}/ack',results[sid],j['claim']['lease'])
        return results
    # Previously published duplicates and review-only jobs can finish even if
    # an unrelated new batch later fails. A bad peer must not strand them.
    failures=[]
    ready=[j for j in jobs if j['claim']['id'] in results]
    for j in ready:save_result(j,results[j['claim']['id']])
    for j in ready:
        sid=j['claim']['id']
        try:
            with leases.renew_lock:
                acknowledge(client,j,results[sid]);leases.remove(sid)
            logging.getLogger('ascension-upload-collector').info('Submission %s completed: %s',sid,results[sid]['status'])
        except Exception as error:failures.append(error)
    if pending:
        commit=run_publisher(config,pending,leases)
        for j in pending:results[j['claim']['id']]={'status':'needs_review' if j['review'] else 'published','commit':commit}
        # Persist proof before acknowledgments. A network failure in one ack
        # must not force the other completed jobs through another publication.
        path=Path(config['state'])/'published-bundles.json'
        try:evidence=json.loads(path.read_text())
        except (OSError,ValueError):evidence={'schema':1,'bundles':{}}
        if not isinstance(evidence,dict) or evidence.get('schema')!=1 or not isinstance(evidence.get('bundles'),dict):evidence={'schema':1,'bundles':{}}
        for j in pending:
            if not j['review'] and commit_has_bundle(config['publish_repo'],commit,j['key'],require_noop=True):evidence['bundles'][j['key']]=commit
        atomic_json(path,evidence)
    for j in pending:save_result(j,results[j['claim']['id']])
    for j in pending:
        sid=j['claim']['id']
        try:
            with leases.renew_lock:
                acknowledge(client,j,results[sid]);leases.remove(sid)
            logging.getLogger('ascension-upload-collector').info('Submission %s completed: %s',sid,results[sid]['status'])
        except Exception as error:failures.append(error)
    if failures:raise RuntimeError('Some result acknowledgments failed; durable results retained') from failures[0]
    return results


def process_one(client, claim, config, publish=True):
    leases=Leases(client);leases.add(claim);leases.start()
    try:
        job=prepare_submission(client,claim,config,leases)
        return finish_batch(client,[job],config,leases,publish)[claim['id']]
    finally:leases.close()


def process_batch(client, first, config):
    """Claim up to eight waiting jobs before preparing any large upload."""
    leases=Leases(client);leases.add(first);leases.start()
    jobs=[];claims=[first];started=time.monotonic();failed=False
    limit=max(1,min(8,int(config.get('batch_max_submissions',8))))
    window=max(0,min(300,int(config.get('batch_collect_seconds',60))))
    def retry(c):
        # A durable terminal outcome is replayed independently. Never downgrade
        # it to received just because its acknowledgment response was lost.
        if (Path(config['state'])/c['id']/'pending-ack.json').exists():return
        try:client.call(f'collector/{c["id"]}/ack',{'status':'received'},c['lease'])
        except Exception:logging.getLogger('ascension-upload-collector').exception('Could not return submission to retry queue')
        leases.remove(c['id'])
    try:
        while len(claims)<limit and time.monotonic()-started<window:
            try:claim=client.call('collector/claim',{})['submission']
            except Exception:
                logging.getLogger('ascension-upload-collector').exception('Batch queue lookup failed; finishing claimed jobs');break
            if not claim:break
            if any(c['id']==claim['id'] for c in claims):raise RuntimeError('Server returned a duplicate exclusive claim')
            claims.append(claim);leases.add(claim)
        # At most 8 x 512 MiB; each validator still has a 128 MiB heap.
        for claim in claims:
            logging.getLogger('ascension-upload-collector').info('Preparing submission %s for batch',claim['id'])
            try:
                job=prepare_submission(client,claim,config,leases);leases.assert_active([job]);jobs.append(job)
            except Exception:
                failed=True;logging.getLogger('ascension-upload-collector').exception('Submission validation failed');retry(claim)
        active_jobs=[j for j in jobs if j['claim']['id'] not in leases.lost]
        if len(active_jobs)!=len(jobs):failed=True
        jobs=active_jobs
        if jobs:
            try:finish_batch(client,jobs,config,leases)
            except Exception:
                failed=True;logging.getLogger('ascension-upload-collector').exception('Batch publication or acknowledgment failed')
                for j in jobs:
                    if j['claim']['id'] in leases.claims:retry(j['claim'])
        return not failed
    finally:leases.close()


def cleanup_local(state):
    state=Path(state).resolve()
    for job in state.iterdir() if state.exists() else []:
        if not job.is_dir() or job.is_symlink() or not re.fullmatch('[a-f0-9-]{36}',job.name): continue
        try: metadata=json.loads((job/'retention.json').read_text())
        except (OSError,ValueError): continue
        if metadata['expires'] >= time.time(): continue
        for name in ['download','review']:
            path=(job/name).resolve()
            if path.is_relative_to(state) and path.is_dir() and not path.is_symlink(): shutil.rmtree(path)
        for path in job.glob('validated-*'):
            resolved=path.resolve()
            if resolved.is_relative_to(state) and not path.is_symlink(): shutil.rmtree(resolved)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',required=True)
    p.add_argument('--once',action='store_true')
    p.add_argument('--stage-only',action='store_true',help='Validate and deliver without invoking publisher; use only with an isolated test inbox')
    args=p.parse_args()
    config=json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
    token=os.environ.get('ASCENSION_UPLOAD_COLLECTOR_TOKEN')
    if not token and config.get('token_file'):
        token=Path(config['token_file']).read_text(encoding='utf-8').strip()
    if not token: p.error('Set ASCENSION_UPLOAD_COLLECTOR_TOKEN outside source control')
    if Path(config['inbox']).resolve() != (Path(config['work'])/'_inbox').resolve(): p.error('Inbox must match the configured consolidator workspace')
    state=Path(config['state']).resolve();state.mkdir(parents=True,exist_ok=True)
    logger=logging.getLogger('ascension-upload-collector')
    logger.setLevel(logging.INFO)
    handler=RotatingFileHandler(state/'collector.log',maxBytes=1024*1024,backupCount=3,encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger.addHandler(handler)
    logger.info('Collector started')
    # OS advisory lock is released even if this collector crashes.
    lock=(state/'collector.lock').open('a+b');lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
    try:
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError: p.error('Another collector is already running')
    client=Client(config['url'],token)
    if not args.stage_only:
        sys.path.insert(0,str(Path(config['publisher']).parent))
        import intake_jobs
        intake_jobs.write(Path(config['work'])/'shared-collector.json',{'schema':1,'state':str(state),'enabled':True,'heartbeat':time.time()})
    def stream_status():
        while True:
            intake_jobs.write(Path(config['work'])/'shared-collector.json',{'schema':1,'state':str(state),'enabled':True,'heartbeat':time.time()})
            try:atomic_json(state/'stream-status.json',{'checked':time.time(),**client.call('collector/status')})
            except Exception:logger.warning('Stream status unavailable; previous snapshot retained')
            time.sleep(30)
    if not args.once and not args.stage_only:threading.Thread(target=stream_status,daemon=True).start()
    while True:
        try:
            replay_pending_acks(client,state,config)
            client.call('collector/cleanup',{})
            cleanup_local(state)
            claim=client.call('collector/claim',{})['submission']
            if claim:
                if args.stage_only:
                    result=process_one(client,claim,config,publish=False)
                    print(json.dumps({'id':claim['id'],**result}),flush=True)
                else:
                    ok=process_batch(client,claim,config)
                    if not ok and args.once:return 1
                    if not args.once:intake_jobs.process_manual(config)
            elif not args.stage_only and intake_jobs.process_manual(config):pass
            elif args.once: print('No submissions queued.')
        except Exception as e:
            logger.exception('Collector request failed')
            print('Collector unavailable: '+type(e).__name__,flush=True)
            if args.once: return 1
        if args.once: return 0
        time.sleep(30)

if __name__=='__main__': sys.exit(main())
