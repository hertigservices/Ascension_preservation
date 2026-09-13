"""S3-shaped adapter for the bounded, authenticated publication Worker.
Uses a dedicated publication token; no Cloudflare account credential is needed in CI.
"""
import io,json,urllib.request,urllib.error
from pathlib import Path

class RemoteError(RuntimeError):
    def __init__(self,status):
        super().__init__(f'Publication service returned HTTP {status}')
        self.response={'Error':{'Code':str(status)}}

class Gateway:
    def __init__(self,url,token):
        if not url.startswith('https://'):raise ValueError('Publication requires HTTPS')
        self.url=url.rstrip('/');self.token=token
    def request(self,method,key,body=None,headers=None):
        from urllib.parse import quote
        headers={'User-Agent':'AscensionPreservation/1.0',**(headers or {}),'Authorization':'Bearer '+self.token}
        request=urllib.request.Request(self.url+'/_publish/'+quote(key,safe='/'),data=body,headers=headers,method=method)
        try:return urllib.request.urlopen(request,timeout=180)
        except urllib.error.HTTPError as e:raise RemoteError(e.code) from None
    def get_object(self,*,Bucket,Key):
        r=self.request('GET',Key);return {'Body':r,'ETag':r.headers['ETag']}
    def head_object(self,*,Bucket,Key):
        with self.request('HEAD',Key) as r:return {'ContentLength':int(r.headers['Content-Length']),'Metadata':{'sha256':r.headers.get('X-Object-Sha256','')},'ETag':r.headers['ETag']}
    def put_object(self,*,Bucket,Key,Body,ContentType='application/octet-stream',CacheControl='no-store',ContentLength=None,Metadata=None,IfMatch=None,IfNoneMatch=None,**ignored):
        import hashlib
        if isinstance(Body,bytes):length=len(Body);sha=hashlib.sha256(Body).hexdigest()
        else:length=ContentLength;sha=(Metadata or {})['sha256']
        if length is None or length>64*1024*1024:raise ValueError('Gateway uploads must be at most 64 MiB; use S3 for larger objects')
        headers={'Content-Type':ContentType,'Content-Length':str(length),'X-Object-Sha256':sha,'Cache-Control':CacheControl}
        if IfMatch:headers['If-Match']=IfMatch
        if IfNoneMatch:headers['If-None-Match']=IfNoneMatch
        with self.request('PUT',Key,Body,headers) as r:return json.load(r)

    def upload_files(self,bucket,root,prefix,files,workers=4):
        import hashlib,mimetypes,uuid
        from concurrent.futures import ThreadPoolExecutor
        groups=[];group=[];size=0
        for name,e in files.items():
            if group and (len(group)==8 or size+e['bytes']>4*1024*1024):groups.append(group);group=[];size=0
            group.append((name,e));size+=e['bytes']
        if group:groups.append(group)
        def upload(group):
            if len(group)==1 and group[0][1]['bytes']>4*1024*1024:
                name,e=group[0];path=Path(root)/name
                with path.open('rb') as source:actual=hashlib.file_digest(source,'sha256').hexdigest()
                if path.stat().st_size!=e['bytes'] or actual!=e['sha256']:raise ValueError('Publication input changed')
                with path.open('rb') as f:
                    try:self.put_object(Bucket=bucket,Key=prefix+name,Body=f,ContentLength=e['bytes'],Metadata={'sha256':e['sha256']},IfNoneMatch='*',ContentType=mimetypes.guess_type(name)[0] or 'application/octet-stream')
                    except RemoteError as err:
                        if err.response['Error']['Code']!='412':raise
                        existing=self.head_object(Bucket=bucket,Key=prefix+name)
                        if existing['ContentLength']!=e['bytes'] or existing['Metadata']['sha256']!=e['sha256']:raise ValueError('Immutable object differs')
                return
            boundary='Ascension'+uuid.uuid4().hex;body=io.BytesIO();meta=[]
            def field(name,value,filename=None):
                body.write(('--'+boundary+'\r\nContent-Disposition: form-data; name="'+name+'"'+('; filename="'+filename+'"' if filename else '')+'\r\n\r\n').encode());body.write(value);body.write(b'\r\n')
            for i,(name,e) in enumerate(group):
                data=(Path(root)/name).read_bytes()
                if len(data)!=e['bytes'] or hashlib.sha256(data).hexdigest()!=e['sha256']:raise ValueError('Publication input changed')
                field('file'+str(i),data,'object');meta.append({'key':prefix+name,**e,'contentType':'application/gzip' if name.endswith('.gz') else mimetypes.guess_type(name)[0] or 'application/octet-stream'})
            field('metadata',json.dumps(meta).encode());body.write(('--'+boundary+'--\r\n').encode());payload=body.getvalue()
            with self.request('POST','batch',payload,{'Content-Type':'multipart/form-data; boundary='+boundary,'Content-Length':str(len(payload))}) as r:verified=json.load(r)
            if verified!=[{k:e[k] for k in ('key','sha256','bytes')} for e in meta]:raise ValueError('Batch verification differs')
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for i,_ in enumerate(pool.map(upload,groups),1):
                if i%100==0 or i==len(groups):print(f'Uploaded/verified batches: {i}/{len(groups)}',flush=True)
