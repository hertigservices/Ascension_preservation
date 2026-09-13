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
