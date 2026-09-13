"""Catalog publication through R2's S3 API. Upload first; compare-and-swap pointer last.
Credentials are environment variables consumed by boto3, never stored in source or manifests.
"""
import argparse, base64, hashlib, json, mimetypes, os, re, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from dataset import atomic, canonical, digest, safe_path

MAX_OBJECT=512*1024*1024

def inventory(root, kind='catalog'):
    root=Path(root).resolve()
    if kind=='catalog':
        # Structural/record validation is mandatory before it can become current.
        import subprocess
        subprocess.run([sys.executable,'-B',str(Path(__file__).resolve().parent.parent/'ascension-db/validate.py'),str(root)],check=True)
        manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
        if manifest.get('sample'):raise ValueError('Sample catalogs cannot be published')
    files={}
    for p in sorted(root.rglob('*')):
        if p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()):raise ValueError('Links cannot enter publication')
        if p.is_file():
            name=safe_path(p.relative_to(root).as_posix());size=p.stat().st_size
            if size>MAX_OBJECT:raise ValueError('Split oversized objects before uploading')
            files[name]={'sha256':digest(p),'bytes':size}
    if not files:raise ValueError('Empty publication')
    content={'schema':'ascension-objects-1','kind':kind,'files':files}
    content['snapshot']=hashlib.sha256(canonical(content)).hexdigest()
    return content

def missing(exc):return getattr(exc,'response',{}).get('Error',{}).get('Code') in ('404','NoSuchKey','NotFound')

def publish(client,bucket,root,report,channel='catalog',workers=4):
    if not re.fullmatch('[a-z0-9][a-z0-9-]{0,63}',channel):raise ValueError('Invalid channel')
    root=Path(root);prefix=f"{channel}/snapshots/{report['snapshot']}/";pointer=f'{channel}/current.json'
    try:
        prior=client.get_object(Bucket=bucket,Key=pointer)
        previous=json.loads(prior['Body'].read());etag=prior['ETag']
        if previous.get('snapshot')==report['snapshot']:return previous
    except Exception as e:
        if not missing(e):raise
        previous=None;etag=None
    def put(entry):
        name,meta=entry;key=prefix+name;p=root/name
        try:
            existing=client.head_object(Bucket=bucket,Key=key)
            if existing['ContentLength']!=meta['bytes'] or existing.get('Metadata',{}).get('sha256')!=meta['sha256']:raise ValueError('Immutable remote object differs')
            return
        except Exception as e:
            if not missing(e):raise
        # ContentMD5 is checked by storage. SHA-256 in metadata supports retry verification.
        sha=hashlib.sha256();md5=hashlib.md5()
        with p.open('rb') as f:
            while block:=f.read(1024*1024):sha.update(block);md5.update(block)
        if sha.hexdigest()!=meta['sha256'] or p.stat().st_size!=meta['bytes']:raise ValueError('Publication changed after validation')
        content_type='application/gzip' if name.endswith('.gz') else (mimetypes.guess_type(name)[0] or 'application/octet-stream')
        with p.open('rb') as f:
            client.put_object(Bucket=bucket,Key=key,Body=f,ContentLength=meta['bytes'],ContentMD5=base64.b64encode(md5.digest()).decode(),ContentType=content_type,CacheControl='public,max-age=31536000,immutable',Metadata={'sha256':meta['sha256']},IfNoneMatch='*')
        checked=client.head_object(Bucket=bucket,Key=key)
        if checked['ContentLength']!=meta['bytes'] or checked.get('Metadata',{}).get('sha256')!=meta['sha256']:raise ValueError('Remote object verification failed')
    if hasattr(client,'upload_files'):client.upload_files(bucket,root,prefix,report['files'],workers)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for _ in pool.map(put,report['files'].items()):pass
    payload=canonical(report)
    try:
        client.put_object(Bucket=bucket,Key=prefix+'storage-manifest.json',Body=payload,ContentType='application/json',IfNoneMatch='*')
    except Exception as e:
        if getattr(e,'response',{}).get('Error',{}).get('Code') not in ('412','PreconditionFailed'):raise
        if client.get_object(Bucket=bucket,Key=prefix+'storage-manifest.json')['Body'].read()!=payload:raise ValueError('Remote storage manifest differs')
    current={'schema':'ascension-current-1','snapshot':report['snapshot'],'prefix':prefix,'previous':previous.get('snapshot') if previous else None}
    condition={'IfMatch':etag} if etag else {'IfNoneMatch':'*'}
    client.put_object(Bucket=bucket,Key=pointer,Body=canonical(current),ContentType='application/json',CacheControl='no-store',**condition)
    return current

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--input',type=Path,required=True);ap.add_argument('--report',type=Path,required=True);ap.add_argument('--kind',choices=['catalog','media'],default='catalog');ap.add_argument('--channel',default='catalog');ap.add_argument('--bucket',default='ascension-public-data');ap.add_argument('--upload',action='store_true');a=ap.parse_args()
    report=inventory(a.input,a.kind);atomic(a.report,report)
    if not a.upload:print(json.dumps({'snapshot':report['snapshot'],'objects':len(report['files']),'bytes':sum(v['bytes'] for v in report['files'].values()),'uploaded':False}));return
    gateway=os.environ.get('ASCENSION_PUBLICATION_URL')
    if gateway:
        from gateway import Gateway
        client=Gateway(gateway,os.environ['ASCENSION_PUBLICATION_TOKEN'])
        print(json.dumps(publish(client,a.bucket,a.input,report,a.channel)));return
    import boto3
    endpoint=os.environ.get('ASCENSION_R2_ENDPOINT')
    if not endpoint or not re.fullmatch(r'https://[a-f0-9]+\.r2\.cloudflarestorage\.com',endpoint):raise ValueError('Set ASCENSION_R2_ENDPOINT to the account S3 endpoint')
    client=boto3.client('s3',endpoint_url=endpoint,region_name='auto')
    print(json.dumps(publish(client,a.bucket,a.input,report,a.channel)))
if __name__=='__main__':main()
