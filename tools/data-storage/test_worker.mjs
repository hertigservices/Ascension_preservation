import assert from 'node:assert/strict';
import test from 'node:test';
import worker from './worker.mjs';
const env={PUBLIC_DATA:{get:async key=>({size:3,httpEtag:'"ok"',body:'png',customMetadata:{},writeHttpMetadata:h=>h.set('Content-Type','image/png')}),head:async()=>null}};
test('public images read; donor bucket and writes never exposed',async()=>{
  const image=await worker.fetch(new Request('https://example.test/images/icons/a.png'),env);assert.equal(image.status,200);assert.equal(await image.text(),'png');
  assert.equal((await worker.fetch(new Request('https://example.test/private/upload.zip'),env)).status,404);
  assert.equal((await worker.fetch(new Request('https://example.test/images/a.png',{method:'PUT',body:'x'}),env)).status,405);
  assert.equal((await worker.fetch(new Request('https://example.test/_publish/catalog/current.json',{method:'PUT',body:'x'}),env)).status,405);
});
test('unpromoted snapshots cannot become current',async()=>{
  const current={schema:'ascension-current-1',snapshot:'a'.repeat(64),prefix:`catalog/snapshots/${'a'.repeat(64)}/`};const body=JSON.stringify(current);
  const r=await worker.fetch(new Request('https://example.test/_publish/catalog/current.json',{method:'PUT',headers:{Authorization:'Bearer fixture','Content-Length':String(body.length),'X-Object-Sha256':'a'.repeat(64),'If-None-Match':'*'},body}),{...env,PUBLISH_TOKEN:'fixture',LEGACY_PUBLICATION_ENABLED:'true'});
  assert.equal(r.status,409);
});
test('bounded batch preserves checksum and immutable-write conditions',async()=>{
  const form=new FormData(),key=`catalog/snapshots/${'a'.repeat(64)}/part.json.gz`,data=new TextEncoder().encode('data');
  const sha=Buffer.from(await crypto.subtle.digest('SHA-256',data)).toString('hex');
  form.set('metadata',JSON.stringify([{key,bytes:4,sha256:sha}]));form.set('file0',new Blob([data]),'object');
  const draft=new Request('https://example.test/_publish/batch',{method:'POST',body:form});const bytes=await draft.arrayBuffer();
  let puts=0;
  const result=await worker.fetch(new Request(draft.url,{method:'POST',headers:{'Content-Type':draft.headers.get('Content-Type'),'Content-Length':String(bytes.byteLength),Authorization:'Bearer fixture'},body:bytes}),{PUBLISH_TOKEN:'fixture',LEGACY_PUBLICATION_ENABLED:'true',PUBLIC_DATA:{put:async(k,b,opts)=>{puts++;assert.equal(k,key);assert.equal(opts.sha256,sha);assert.equal(opts.onlyIf.get('If-None-Match'),'*');assert.equal(b.byteLength,4);return {size:4};}}});
  assert.equal(result.status,200);assert.equal(puts,1);assert.equal((await result.json())[0].sha256,sha);
});

function bucket(objects) {
  return {get:async key=>objects[key]===undefined?null:{size:Buffer.byteLength(objects[key]),body:objects[key],json:async()=>JSON.parse(objects[key]),httpEtag:'"fixture"',customMetadata:{},writeHttpMetadata:h=>h.set('Content-Type','application/octet-stream')},head:async key=>objects[key]===undefined?null:{size:Buffer.byteLength(objects[key]),httpEtag:'"fixture"',customMetadata:{},writeHttpMetadata:h=>h.set('Content-Type','application/octet-stream')}};
}
test('live layout resolves shared content and preserves gzip byte semantics',async()=>{
  const snapshot='b'.repeat(64),name='records/one/0.json.gz',base=`catalog/snapshots/${snapshot}/`;
  const shard=Buffer.from(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(name))).toString('hex').slice(0,2);
  const key='catalog/objects/'+'c'.repeat(64);
  const objects={[base+'layout.json']:JSON.stringify({schema:'ascension-live-layout-1',snapshot,shards:{[shard]:'d'.repeat(64)}}),[base+'_index/'+shard+'.json']:JSON.stringify({[name]:{key,bytes:4,sha256:'c'.repeat(64)}}),[key]:'gzip'};
  const env={PUBLIC_DATA:bucket(objects)};
  const result=await worker.fetch(new Request('https://example.test/'+base+name),env);
  assert.equal(result.status,200);assert.equal(await result.text(),'gzip');assert.equal(result.headers.get('Content-Type'),'application/gzip');assert.equal(result.headers.get('Content-Encoding'),null);
  assert.equal((await worker.fetch(new Request('https://example.test/'+base+name,{method:'HEAD'}),env)).status,200);
  assert.equal((await worker.fetch(new Request('https://example.test/'+key),env)).status,404);
});
test('expired catalog returns an actionable gone response',async()=>{
  const result=await worker.fetch(new Request('https://example.test/catalog/snapshots/'+'e'.repeat(64)+'/manifest.json'),{PUBLIC_DATA:bucket({})});
  assert.equal(result.status,410);assert.match(await result.text(),/Refresh/);
});
test('production gateway rejects a valid old publishing token',async()=>{
  const result=await worker.fetch(new Request('https://example.test/_publish/batch',{method:'POST',headers:{Authorization:'Bearer fixture'},body:'x'}),{PUBLISH_TOKEN:'fixture',PUBLIC_DATA:{put:()=>assert.fail('must not write')}});
  assert.equal(result.status,405);
});

test('preserved image filenames can contain spaces and Unicode; SVG keeps its policy',async()=>{
  for(const path of ['images/maps/Floor%201.png','images/portraits/%C3%89toile.PNG'])assert.equal((await worker.fetch(new Request('https://example.test/'+path),env)).status,200);
  const svg=await worker.fetch(new Request('https://example.test/images/Icon.SVG'),env);assert.match(svg.headers.get('Content-Security-Policy'),/sandbox/);
  assert.equal((await worker.fetch(new Request('https://example.test/images/bad%5Cname.png'),env)).status,404);
});
