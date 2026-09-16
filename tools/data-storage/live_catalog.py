"""WD-backed, live-only R2 catalogs. Public URLs remain snapshot-pinned.

Only new content is written. Every output is durably archived before promotion;
garbage collection requires a verified local copy of each exact remote object.
"""
import datetime as dt
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor

from dataset import atomic, canonical, safe_path

BUCKET = 'ascension-public-data'
SHA = re.compile(r'[0-9a-f]{64}')
LAYOUT_SCHEMA = 'ascension-live-layout-1'


def hashed(path):
    sha, md5, size = hashlib.sha256(), hashlib.md5(), 0
    with Path(path).open('rb') as stream:
        while block := stream.read(1024 * 1024):
            sha.update(block); md5.update(block); size += len(block)
    return sha.hexdigest(), md5.hexdigest(), size


def check_volume(root, uuid):
    root = Path(root)
    # Touching the parent triggers the configured automount, without making a fallback root.
    if not root.parent.is_dir(): raise ValueError('Backup volume is not mounted')
    actual = subprocess.check_output(['findmnt', '-T', str(root.parent), '-n', '-o', 'UUID'], text=True).split()
    if uuid not in actual: raise ValueError('Wrong backup volume; publication stopped')
    if root.is_symlink(): raise ValueError('Backup root must not be a symlink')
    root.mkdir(parents=True, exist_ok=True)


def make_client(path):
    import boto3
    from botocore.config import Config
    path = Path(path)
    if path.is_symlink() or path.stat().st_mode & 0o077: raise ValueError('Credential must be an owner-only regular file')
    data = json.loads(path.read_text())
    if data.get('bucket') != BUCKET: raise ValueError('Wrong credential bucket')
    endpoint = data['ASCENSION_R2_ENDPOINT'].rstrip('/')
    if not re.fullmatch(r'https://[a-f0-9]{32}\.r2\.cloudflarestorage\.com', endpoint): raise ValueError('Invalid endpoint')
    return boto3.client('s3', endpoint_url=endpoint, region_name='auto',
                       aws_access_key_id=data['AWS_ACCESS_KEY_ID'], aws_secret_access_key=data['AWS_SECRET_ACCESS_KEY'],
                       config=Config(connect_timeout=15, read_timeout=90, max_pool_connections=32,
                                     retries={'mode': 'standard', 'total_max_attempts': 4}))


def missing(exc):
    return str(getattr(exc, 'response', {}).get('Error', {}).get('Code')) in ('404', 'NoSuchKey', 'NotFound')


def read(client, key):
    try: response = client.get_object(Bucket=BUCKET, Key=key)
    except Exception as exc:
        if missing(exc): return None, None
        raise
    with response['Body'] as stream: return stream.read(), response['ETag']


def list_objects(client, prefix=''):
    if not prefix:
        direct,leaves={},[]
        def partition(base):
            for page in client.get_paginator('list_objects_v2').paginate(Bucket=BUCKET,Prefix=base,Delimiter='/'):
                for o in page.get('Contents',[]):direct[o['Key']]={'key':o['Key'],'bytes':o['Size'],'etag':o['ETag']}
                for entry in page.get('CommonPrefixes',[]):
                    child=entry['Prefix']
                    if child in ('catalog/','catalog/snapshots/','images/'):partition(child)
                    else:leaves.append(child)
        partition('')
        with ThreadPoolExecutor(max_workers=16) as pool:
            for found in pool.map(lambda value:list_objects(client,value),leaves):
                if direct.keys()&found.keys():raise ValueError('Overlapping listing partitions')
                direct.update(found)
        return direct
    result = {}
    for page in client.get_paginator('list_objects_v2').paginate(Bucket=BUCKET, Prefix=prefix):
        for o in page.get('Contents', []):
            if o['Key'] in result: raise ValueError('Repeated listing key')
            result[o['Key']] = {'key': o['Key'], 'bytes': o['Size'], 'etag': o['ETag']}
    return result


def validate_report(report):
    identity = dict(report); snapshot = identity.pop('snapshot')
    if report.get('schema') != 'ascension-objects-1' or report.get('kind') != 'catalog': raise ValueError('Invalid catalog report')
    if not SHA.fullmatch(snapshot) or hashlib.sha256(canonical(identity)).hexdigest() != snapshot: raise ValueError('Invalid snapshot identity')
    for name, meta in report['files'].items():
        safe_path(name)
        if not SHA.fullmatch(meta['sha256']) or type(meta['bytes']) is not int or meta['bytes'] < 0: raise ValueError('Invalid object metadata')
        if name in ('layout.json', 'storage-manifest.json') or name.startswith('_index/'): raise ValueError('Reserved catalog path')


def prefix(snapshot):
    if not SHA.fullmatch(snapshot): raise ValueError('Invalid snapshot')
    return f'catalog/snapshots/{snapshot}/'


def pointer(client):
    raw, etag = read(client, 'catalog/current.json')
    if raw is None: raise ValueError('Existing live catalog required')
    current = json.loads(raw)
    if current.get('schema') != 'ascension-current-1' or current.get('prefix') != prefix(current.get('snapshot', '')): raise ValueError('Invalid live pointer')
    return current, etag


def current_files(client, current):
    raw, _ = read(client, current['prefix'] + 'storage-manifest.json')
    if raw is None: raise ValueError('Current catalog manifest missing')
    report = json.loads(raw); validate_report(report)
    if report['snapshot'] != current['snapshot']: raise ValueError('Current identity differs')
    layout_raw, _ = read(client, current['prefix'] + 'layout.json')
    if layout_raw is None:
        return report, {name: {**meta, 'key': current['prefix'] + name} for name, meta in report['files'].items()}, None
    layout = json.loads(layout_raw)
    if layout.get('schema') != LAYOUT_SCHEMA or layout.get('snapshot') != current['snapshot']: raise ValueError('Invalid layout')
    files = {}
    def fetch_shard(shard):
        if not re.fullmatch('[0-9a-f]{2}',shard) or not SHA.fullmatch(layout['shards'][shard]):raise ValueError('Invalid shard identity')
        raw, _ = read(client, current['prefix'] + '_index/' + shard + '.json')
        if raw is None or hashlib.sha256(raw).hexdigest() != layout['shards'][shard]: raise ValueError('Invalid layout shard')
        return shard,json.loads(raw)
    with ThreadPoolExecutor(max_workers=16) as pool: shards=list(pool.map(fetch_shard,layout['shards']))
    for shard,entries in shards:
        for name, meta in entries.items():
            if name in files or hashlib.sha256(name.encode()).hexdigest()[:2] != shard: raise ValueError('Invalid shard membership')
            if not valid_physical(meta['key']): raise ValueError('Invalid storage reference')
            files[name] = meta
    if {name: {k:v[k] for k in ('bytes', 'sha256')} for name,v in files.items()} != report['files']: raise ValueError('Layout and catalog differ')
    return report, files, layout


def valid_physical(key):
    return bool(re.fullmatch(r'catalog/objects/[0-9a-f]{64}', key) or
                re.fullmatch(r'catalog/snapshots/[0-9a-f]{64}/[a-zA-Z0-9_./-]+', key)) and not any(p in ('.','..','') for p in key.split('/'))


class Archive:
    def __init__(self, root, volume_uuid=None):
        self.volume_uuid=volume_uuid
        if volume_uuid:check_volume(root,volume_uuid)
        self.root = Path(root); self.blobs = self.root / 'blobs'; self.blobs.mkdir(parents=True, exist_ok=True)

    def check_volume(self):
        if self.volume_uuid:check_volume(self.root,self.volume_uuid)

    def path(self, sha):
        if not SHA.fullmatch(sha): raise ValueError('Invalid archive hash')
        return self.blobs / sha[:2] / sha

    def verify(self, meta, expected_etag=None):
        sha, md5, size = hashed(self.path(meta['sha256']))
        if sha != meta['sha256'] or size != meta['bytes']: raise ValueError('Archive content differs')
        if expected_etag and (not re.fullmatch(r'"?[0-9a-f]{32}"?', expected_etag) or md5 != expected_etag.strip('"')):
            raise ValueError('Archive does not match listed remote ETag')
        return md5

    def local(self, path, meta):
        sha, md5, size = hashed(path)
        if sha != meta['sha256'] or size != meta['bytes']: raise ValueError('Publication output changed')
        target = self.path(sha); target.parent.mkdir(exist_ok=True)
        if not target.exists():
            pending = target.with_suffix('.pending')
            with Path(path).open('rb') as src, pending.open('wb') as dst:
                while block := src.read(1024*1024): dst.write(block)
                dst.flush(); os.fsync(dst.fileno())
            os.replace(pending, target)
        self.verify(meta)
        return md5

    def remote(self, client, obj, expected=None):
        if expected:
            try:
                self.verify(expected, obj['etag']); return {**obj, 'sha256': expected['sha256']}
            except (FileNotFoundError, ValueError): pass
        response = client.get_object(Bucket=BUCKET, Key=obj['key'], IfMatch=obj['etag'])
        pending = self.root / ('download-' + hashlib.sha256(obj['key'].encode()).hexdigest())
        with response['Body'] as body, pending.open('wb') as dst:
            while block := body.read(1024*1024): dst.write(block)
            dst.flush(); os.fsync(dst.fileno())
        sha, md5, size = hashed(pending)
        if size != obj['bytes'] or not re.fullmatch(r'"?[0-9a-f]{32}"?',obj['etag']) or md5 != obj['etag'].strip('"'): raise ValueError('Downloaded object differs')
        if expected and sha != expected['sha256']: raise ValueError('Remote SHA differs from manifest')
        target = self.path(sha); target.parent.mkdir(exist_ok=True); os.replace(pending, target)
        return {**obj, 'sha256': sha}


class Budget:
    """Reserve writes before dispatch, including worst-case SDK retries. Never refund errors."""
    def __init__(self, path, limit=250000, day=10, now=None):
        self.path, self.limit = Path(path), limit
        now = now or dt.datetime.now(dt.timezone.utc)
        month = now.month if now.day >= day else now.month-1
        year = now.year
        if month == 0: year, month = year-1, 12
        self.period = f'{year:04}-{month:02}-{day:02}'
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {'periods': {}}

    def reserve(self, writes):
        # Four SDK attempts can each be billable. Count every potential attempt.
        units = writes * 4
        used = self.data['periods'].get(self.period, 0)
        if used + units > self.limit: raise ValueError('Monthly publication write budget reached; current site retained')
        self.data['periods'][self.period] = used + units
        atomic(self.path, self.data)

    def settle(self, writes, attempts):
        if not writes <= attempts <= writes*4: raise ValueError('Invalid write accounting')
        self.data['periods'][self.period] -= writes*4-attempts
        atomic(self.path, self.data)


def put(client, key, body, *, etag=None, content_type='application/json'):
    condition = {'IfMatch': etag} if etag is not None else {'IfNoneMatch': '*'}
    sha = hashlib.sha256(body).hexdigest()
    try:
        response=client.put_object(Bucket=BUCKET, Key=key, Body=body, ContentType=content_type,
                          ContentMD5=__import__('base64').b64encode(hashlib.md5(body).digest()).decode(),
                          Metadata={'sha256':sha}, CacheControl='no-store' if key.endswith('current.json') else 'public,max-age=31536000,immutable', **condition)
        return 1+response.get('ResponseMetadata',{}).get('RetryAttempts',0)
    except Exception as exc:
        # Immutable retry succeeds only after exact byte readback; CAS conflicts never retry as a new pointer.
        if etag is not None or str(getattr(exc,'response',{}).get('Error',{}).get('Code')) not in ('412','PreconditionFailed'): raise
        existing, _ = read(client, key)
        if existing != body: raise ValueError('Immutable object differs')
        return 4


def layout_for(snapshot, files):
    shards = {}
    for name, meta in files.items():
        part = hashlib.sha256(name.encode()).hexdigest()[:2]
        shards.setdefault(part, {})[name] = meta
    encoded = {part: canonical(value) for part,value in shards.items()}
    layout = {'schema': LAYOUT_SCHEMA, 'snapshot': snapshot,
              'shards': {part:hashlib.sha256(body).hexdigest() for part,body in encoded.items()}}
    return layout, encoded


def promote(client, archive, budget, report, root=None, *, max_public_bytes=7500000000, max_new_objects=25000):
    validate_report(report)
    current, etag = pointer(client)
    old_report, old_files, old_layout = current_files(client, current)
    if report['snapshot'] == current['snapshot'] and old_layout is not None:
        return {'unchanged': True, 'current': current, 'new_objects': 0}
    inventory = list_objects(client)
    old_by_hash = {v['sha256']:v for v in old_files.values()}
    files, uploads = {}, {}
    for name, meta in report['files'].items():
        if root is not None: archive.local(Path(root)/name, meta)
        else: archive.verify(meta)  # Migration must have already archived every live file.
        old = old_by_hash.get(meta['sha256'])
        if old and old['bytes'] == meta['bytes'] and old['key'] in inventory and inventory[old['key']]['bytes'] == meta['bytes']:
            archive.verify(meta, inventory[old['key']]['etag']); key = old['key']
        else:
            key = 'catalog/objects/' + meta['sha256']
            if key in inventory: archive.verify(meta, inventory[key]['etag'])
            else: uploads[key] = meta
        files[name] = {**meta, 'key': key}
    layout, shards = layout_for(report['snapshot'], files)
    base = prefix(report['snapshot'])
    metadata = {base+'_index/'+part+'.json':body for part,body in shards.items()}
    metadata[base+'storage-manifest.json'] = canonical(report)
    metadata[base+'layout.json'] = canonical(layout)
    fresh_metadata = {k:b for k,b in metadata.items() if k not in inventory}
    # Metadata is immutable too, including during a resumed same-snapshot migration.
    for key, body in metadata.items():
        if key in inventory:
            actual, _ = read(client,key)
            if actual != body: raise ValueError('Existing publication metadata differs')
    writes = len(uploads)+len(fresh_metadata)+1
    if writes > max_new_objects: raise ValueError('Per-publication write budget reached; current site retained')
    peak = sum(v['bytes'] for v in inventory.values())+sum(v['bytes'] for v in uploads.values())+sum(map(len,fresh_metadata.values()))+1024
    if peak > max_public_bytes: raise ValueError('Public bucket capacity budget reached; archive/cleanup required first')
    # WD stores the complete restorable manifest before any write, and every blob was rehashed above.
    archive_dir = archive.root/'catalogs'/report['snapshot']; archive_dir.mkdir(parents=True,exist_ok=True)
    atomic(archive_dir/'storage-manifest.json',report); atomic(archive_dir/'layout.json',layout)
    atomic(archive_dir/'publication-plan.json',{'prior':current,'files':files,'new_objects':writes,'peak_bytes':peak})
    archive.check_volume();budget.reserve(writes)
    def upload(item):
        key,meta=item
        body=archive.path(meta['sha256']).read_bytes()
        if len(body)!=meta['bytes'] or hashlib.sha256(body).hexdigest()!=meta['sha256']:raise ValueError('Archived upload changed')
        # MIME belongs to the logical path and is supplied by the read gateway.
        return put(client,key,body,content_type='application/octet-stream')
    attempts=0
    with ThreadPoolExecutor(max_workers=8) as pool:
        for used in pool.map(upload,uploads.items()):attempts+=used
    for key,body in fresh_metadata.items():attempts+=put(client,key,body)
    new_current={'schema':'ascension-current-1','snapshot':report['snapshot'],'prefix':base,'previous':None,'storage':LAYOUT_SCHEMA}
    # A stale or concurrent publisher cannot silently replace the current pointer.
    archive.check_volume()
    if pointer(client)!=(current,etag):raise ValueError('Current changed during upload; promotion stopped')
    attempts+=put(client,'catalog/current.json',canonical(new_current),etag=etag)
    observed,_=pointer(client)
    if observed!=new_current:raise ValueError('Pointer readback differs')
    budget.settle(writes,attempts)
    receipt={'current':new_current,'prior':current,'new_objects':writes,'new_payload_objects':len(uploads),'reused_file_references':len(files)-sum(v['key'] in uploads for v in files.values()),'peak_bytes':peak,'promoted_at':dt.datetime.now(dt.timezone.utc).isoformat()}
    atomic(archive_dir/'publication-receipt.json',receipt)
    return receipt


def retained_keys(client, current):
    report, files, layout=current_files(client,current)
    retain={'catalog/current.json',current['prefix']+'storage-manifest.json'}|{v['key'] for v in files.values()}
    if layout:
        retain.add(current['prefix']+'layout.json')
        retain.update(current['prefix']+'_index/'+s+'.json' for s in layout['shards'])
    return retain


def cleanup(client, archive, archived, *, journal, allow_legacy=False):
    current,etag=pointer(client)
    if current.get('storage')!=LAYOUT_SCHEMA and not allow_legacy:raise ValueError('Live-only layout required before normal cleanup')
    retain=retained_keys(client,current)
    objects=list_objects(client)
    # Images are current project assets. Unknown namespaces are never guessed disposable.
    candidates=[o for k,o in objects.items() if k.startswith('catalog/') and k not in retain]
    candidate_keys={o['key'] for o in candidates}
    verified=set()
    for o in candidates:
        saved=archived.get(o['key'])
        if not saved or saved['bytes']!=o['bytes'] or saved['etag']!=o['etag']:raise ValueError('Object lacks exact WD recovery receipt: '+o['key'])
        signature=(saved['sha256'],o['etag'],o['bytes'])
        if signature not in verified:archive.verify(saved,o['etag']);verified.add(signature)
    # Check again after verification; no candidate is removed before complete backup validation.
    archive.check_volume()
    if pointer(client)!=(current,etag):raise ValueError('Live pointer changed before cleanup')
    Path(journal).parent.mkdir(parents=True,exist_ok=True)
    count=total=0
    with Path(journal).open('x') as log:
        for offset in range(0,len(candidates),1000):
            archive.check_volume()
            if pointer(client)!=(current,etag):raise ValueError('Live pointer changed during cleanup')
            batch=candidates[offset:offset+1000]
            response=client.delete_objects(Bucket=BUCKET,Delete={'Objects':[{'Key':o['key']} for o in batch],'Quiet':False})
            if response.get('Errors') or {x['Key'] for x in response.get('Deleted',[])}!={o['key'] for o in batch}:raise ValueError('Deletion response incomplete')
            log.write(json.dumps({'keys':[o['key'] for o in batch],'bytes':sum(o['bytes'] for o in batch)})+'\n');log.flush();os.fsync(log.fileno())
            count+=len(batch);total+=sum(o['bytes'] for o in batch)
    after=list_objects(client)
    if any(o['key'] in after for o in candidates):raise ValueError('Deleted object remains')
    if any(k not in after or after[k]!=v for k,v in objects.items() if k not in candidate_keys):raise ValueError('Retained object changed')
    if pointer(client)!=(current,etag):raise ValueError('Live pointer changed during verification')
    return {'deleted_objects':count,'deleted_bytes':total,'remaining_objects':len(after),'remaining_bytes':sum(v['bytes'] for v in after.values()),'current':current}
