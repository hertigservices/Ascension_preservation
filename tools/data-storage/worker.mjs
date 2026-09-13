// Public reads and bounded maintainer publication use a dedicated bucket and token.
const safe = s => /^[a-zA-Z0-9_./-]+$/.test(s) && !s.split('/').some(p => p === '..' || p === '.');
const valid = s => safe(s) && /^(catalog|media)\/(current\.json|snapshots\/[0-9a-f]{64}\/.+)$/.test(s);
const publicHeaders = {'Access-Control-Allow-Origin':'*','X-Content-Type-Options':'nosniff'};
async function authorized(request, env) {
  if (!env.PUBLISH_TOKEN) return false;
  const hash = async s => new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s)));
  const a=await hash(request.headers.get('Authorization')||''),b=await hash('Bearer '+env.PUBLISH_TOKEN);
  let diff=0;for(let i=0;i<a.length;i++)diff|=a[i]^b[i];return diff===0;
}
export default {
  async fetch(request, env) {
    const url=new URL(request.url);let key;
    try {key=decodeURIComponent(url.pathname.slice(1));} catch {return new Response('Invalid path',{status:400});}
    const admin=key.startsWith('_publish/');
    if (admin) {
      if (!await authorized(request,env))return new Response('Unauthorized',{status:401});
      key=key.slice(9);
    }
    if (admin && key==='batch' && request.method==='POST') {
      const size=Number(request.headers.get('Content-Length'));
      if(!Number.isSafeInteger(size)||size<=0||size>5*1024*1024)return new Response('Invalid batch size',{status:400});
      const form=await request.formData();let entries;
      try{entries=JSON.parse(String(form.get('metadata')));}catch{return new Response('Invalid batch',{status:400});}
      if(!Array.isArray(entries)||!entries.length||entries.length>8)return new Response('Invalid batch',{status:400});
      // Validate the whole envelope before writing any member.
      for(let i=0;i<entries.length;i++) {
        const e=entries[i],file=form.get('file'+i);
        if(!valid(e.key)||e.key.endsWith('/current.json')||!Number.isSafeInteger(e.bytes)||e.bytes<0||!file||file.size!==e.bytes||!/^[0-9a-f]{64}$/.test(e.sha256))return new Response('Invalid member',{status:400});
      }
      const result=[];
      for(let i=0;i<entries.length;i++) {
        const e=entries[i];
        let object=await env.PUBLIC_DATA.put(e.key,await form.get('file'+i).arrayBuffer(),{sha256:e.sha256,onlyIf:new Headers({'If-None-Match':'*'}),httpMetadata:{contentType:e.contentType||'application/octet-stream',cacheControl:'public,max-age=31536000,immutable'},customMetadata:{sha256:e.sha256}});
        if(!object) {
          object=await env.PUBLIC_DATA.head(e.key);
          if(!object||object.size!==e.bytes||object.customMetadata?.sha256!==e.sha256)return new Response('Immutable object differs',{status:409});
        }
        result.push({key:e.key,sha256:e.sha256,bytes:e.bytes});
      }
      return Response.json(result);
    }
    const headers=admin?{'X-Content-Type-Options':'nosniff'}:publicHeaders;
    if (!(valid(key) || (!admin && safe(key) && /^images\/.+\.(png|jpe?g|webp|gif|avif|svg)$/i.test(key))))return new Response('Not found',{status:404,headers});
    if (admin && request.method==='PUT') {
      const size=Number(request.headers.get('Content-Length')),sha=request.headers.get('X-Object-Sha256')||'';
      if (!Number.isSafeInteger(size)||size<0||size>64*1024*1024||!/^[0-9a-f]{64}$/.test(sha))return new Response('Invalid upload',{status:400});
      let body=request.body;
      if (key.endsWith('/current.json')) {
        if(size>4096)return new Response('Invalid pointer',{status:400});
        const bytes=await request.arrayBuffer();let current;
        try{current=JSON.parse(new TextDecoder().decode(bytes));}catch{return new Response('Invalid pointer',{status:400});}
        const channel=key.split('/')[0];
        if(current.schema!=='ascension-current-1'||!/^[0-9a-f]{64}$/.test(current.snapshot)||current.prefix!==`${channel}/snapshots/${current.snapshot}/`)return new Response('Invalid pointer',{status:400});
        if(!await env.PUBLIC_DATA.head(current.prefix+'storage-manifest.json'))return new Response('Incomplete snapshot',{status:409});
        if(!request.headers.has('If-Match')&&!request.headers.has('If-None-Match'))return new Response('Conditional update required',{status:428});
        body=bytes;
      } else if (request.headers.get('If-None-Match')!=='*') return new Response('Immutable writes required',{status:428});
      const object=await env.PUBLIC_DATA.put(key,body,{sha256:sha,onlyIf:request.headers,httpMetadata:{contentType:request.headers.get('Content-Type')||'application/octet-stream',cacheControl:key.endsWith('/current.json')?'no-store':'public,max-age=31536000,immutable'},customMetadata:{sha256:sha}});
      if(!object)return new Response('Publication changed; retry from current state',{status:412});
      return Response.json({ETag:object.httpEtag});
    }
    if(!['GET','HEAD'].includes(request.method))return new Response('Read only',{status:405,headers:{...headers,Allow:'GET, HEAD'}});
    const object=await (request.method==='HEAD'?env.PUBLIC_DATA.head(key):env.PUBLIC_DATA.get(key));
    if(!object)return new Response('Not found',{status:404,headers});
    const h=new Headers(headers);object.writeHttpMetadata(h);h.set('ETag',object.httpEtag);h.set('X-Object-Sha256',object.customMetadata?.sha256||'');
    if(key.endsWith('.svg'))h.set('Content-Security-Policy',"default-src 'none'; style-src 'unsafe-inline'; sandbox");
    h.delete('Content-Encoding'); // AscensionDB decompresses .gz itself.
    h.set('Cache-Control',admin||key.endsWith('/current.json')?'no-store':'public, max-age=31536000, immutable');
    if(request.headers.get('If-None-Match')===object.httpEtag)return new Response(null,{status:304,headers:h});
    h.set('Content-Length',String(object.size));
    return new Response(request.method==='HEAD'?null:object.body,{headers:h});
  }
};
