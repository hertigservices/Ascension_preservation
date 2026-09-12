/* Run with: node --test test_search_worker.cjs */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const code = fs.readFileSync(path.join(__dirname, 'web/search-worker.js'), 'utf8');
function fixture(rows, partitionSize = 17) {
  const assets = new Map(), search = {}, buckets = new Map();
  for (const row of rows) {
    const keys = new Set(['browse:' + row[2], 'id:' + row[5].slice(0, 2)]);
    for (const word of row[1].normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase().replaceAll('ß', 'ss').match(/[\p{L}\p{N}]+/gu) || []) if (word.length >= 2) keys.add('name:' + word.slice(0, 2));
    for (const key of keys) { if (!buckets.has(key)) buckets.set(key, []); buckets.get(key).push(row); }
  }
  for (const [key, bucket] of buckets) {
    const parts = [];
    for (let i = 0; i < bucket.length; i += partitionSize) {
      const p = key + '/' + i; parts.push(p); assets.set(p, bucket.slice(i, i + partitionSize));
    }
    search[key] = { parts, count: bucket.length };
  }
  return { assets, manifest: { revision: 'test', built_at: 'test', search, kinds: { item: { label: 'Items' }, spell: { label: 'Spells' }, npc: { label: 'Creatures' } } } };
}
function harness(rows, partitionSize) {
  const { assets, manifest } = fixture(rows, partitionSize);
  let fetches = 0, id = 0;
  const pending = new Map(), messages = [];
  const context = vm.createContext({
    importScripts: name=>{assert.equal(name,'search-aliases.js');vm.runInContext(fs.readFileSync(path.join(__dirname,'web',name),'utf8'),context);},
    AbortController, DOMException, Response, Blob, DecompressionStream, TextDecoder, Uint8Array, setTimeout,
    fetch: async (url) => { fetches++; if (!assets.has(url)) return new Response('', { status: 404 }); return new Response(JSON.stringify(assets.get(url))); },
    postMessage: message => { messages.push(message); if (!message.progress) { const resolve = pending.get(message.id); pending.delete(message.id); resolve?.(JSON.parse(JSON.stringify(message))); } },
  });
  vm.runInContext(code, context);
  return {
    get fetches() { return fetches; }, messages, context, manifest, assets,
    query: (fields = {}) => new Promise(resolve => { const request = ++id; pending.set(request, resolve); context.onmessage({ data: { id: request, manifest, q: '', page: 0, ...fields } }); }),
  };
}
const row = (key, name, id, kind = 'item', source = 'Client captures', mode = 'Unspecified') => [key, name, kind, mode, source, String(id)];
test('Everything includes all collections and globally sorts before pagination', async () => {
  const rows = Array.from({ length: 120 }, (_, i) => row('record/' + i, 'Entry ' + String(119 - i).padStart(3, '0'), i, i % 2 ? 'spell' : 'item'));
  const worker = harness(rows);
  const first = await worker.query();
  assert.equal(first.total, 120);
  assert.equal(first.rows[0][1], 'Entry 000'); assert.equal(first.rows[49][1], 'Entry 049');
  assert.deepEqual([...new Set(first.rows.map(r => r[2]))].sort(), ['item', 'spell']);
  const fetches = worker.fetches;
  const second = await worker.query({ page: 1 });
  assert.equal(second.rows[0][1], 'Entry 050'); assert.equal(second.rows.at(-1)[1], 'Entry 099');
  assert.equal(worker.fetches, fetches, 'next page must reuse the completed result window');
  const descending = await worker.query({ dir: 'desc' });
  assert.equal(descending.rows[0][1], 'Entry 119');
});
test('ID sorting preserves exact integer precision, negatives, and deterministic text IDs', async () => {
  const worker = harness(['9007199254740993', '10', '-9007199254740993', '2', '9007199254740992', '-2', 'row:7'].map((id, i) => row(String(i), 'Same', id)));
  const sorted = await worker.query({ sort: 'id' });
  assert.deepEqual(sorted.rows.map(r => r[5]), ['-9007199254740993', '-2', '2', '10', '9007199254740992', '9007199254740993', 'row:7']);
  assert.deepEqual((await worker.query({ q: '9007199254740993' })).rows.map(r => r[5]), ['9007199254740993']);
  assert.deepEqual((await worker.query({ recordId: 'row:7' })).rows.map(r => r[5]), ['row:7']);
});
test('column filters combine with global search and exact mode membership', async () => {
  const worker = harness([
    row('a', 'Élite Silver Sword', '5', 'item', 'Client captures', 'CoA, Draft'),
    row('b', 'Elite Silver Sword', '6', 'item', 'Exiles DB', 'CoA'),
    row('c', 'Elite Silver Sword', '5', 'spell', 'Client captures', 'CoA'),
    row('d', 'Elite Silver Sword', '7', 'item', 'Client captures', 'CoA2'),
    row('e', 'Elite Gold Sword', '5', 'item', 'Client captures', 'CoA'),
  ]);
  const result = await worker.query({ q: 'eli swo', name: 'sil', recordId: '5', source: 'Client captures', mode: 'CoA', kind: 'item' });
  assert.equal(result.total, 1); assert.equal(result.rows[0][0], 'a');
  assert.equal((await worker.query({ mode: 'CoA' })).total, 4);
  assert.equal((await worker.query({ name: 'zz' })).total, 0);
});
test('collection, source and mode sort by visible labels in both directions', async () => {
  const worker = harness([
    row('a', 'Alpha', '1', 'spell', 'Zulu', 'Zulu'),
    row('b', 'Bravo', '2', 'npc', 'Bravo', 'Bravo'),
    row('c', 'Charlie', '3', 'item', 'Alpha', 'Alpha'),
  ]);
  for (const [sort, expected] of [['kind', ['b', 'c', 'a']], ['source', ['c', 'b', 'a']], ['mode', ['c', 'b', 'a']]]) {
    assert.deepEqual((await worker.query({ sort })).rows.map(r => r[0]), expected);
    assert.deepEqual((await worker.query({ sort, dir: 'desc' })).rows.map(r => r[0]), [...expected].reverse());
  }
});
test('far bookmarked pages use bounded windows without losing records', async () => {
  const worker = harness(Array.from({ length: 6010 }, (_, i) => row(String(i), 'Entry', 6009 - i)), 1000);
  const result = await worker.query({ sort: 'id', page: 110 });
  assert.equal(result.total, 6010);
  assert.equal(result.rows[0][5], '5500'); assert.equal(result.rows.at(-1)[5], '5549');
  const beyond = await worker.query({ sort: 'id', page: 999999 });
  assert.equal(beyond.total, 6010); assert.equal(beyond.rows.length, 0);
});
test('invalid input and missing catalog parts return actionable errors', async () => {
  const worker = harness([row('a', 'Alpha', '1')]);
  assert.match((await worker.query({ q: 'a' })).error, /at least two/);
  worker.manifest.search['browse:item'].parts = ['missing'];
  assert.match((await worker.query()).error, /Catalog part unavailable/);
});
test('cancel prevents a stale completed result and newer search still completes', async () => {
  const worker = harness(Array.from({ length: 8000 }, (_, i) => row(String(i), 'Entry ' + i, i)), 10000);
  const old = worker.query();
  await new Promise(resolve => setTimeout(resolve, 2));
  worker.context.onmessage({ data: { cancel: true } });
  const result = await worker.query({ recordId: '1234' });
  assert.equal(result.rows[0][5], '1234');
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(worker.messages.some(message => message.id === 1 && !message.progress), false);
});

test('equal visible values retain every distinct source record across cached windows', async () => {
  const rows = Array.from({ length: 1105 }, (_, i) => row('record/' + (1104 - i), 'Same', '9007199254740993'));
  const worker = harness(rows, 200);
  const expected = rows.map(r => r[0]).sort();
  await worker.query({ sort: 'id' });
  const before = await worker.query({ sort: 'id', page: 19 });
  const after = await worker.query({ sort: 'id', page: 20 });
  assert.deepEqual(before.rows.map(r => r[0]), expected.slice(950, 1000));
  assert.deepEqual(after.rows.map(r => r[0]), expected.slice(1000, 1050));
  assert.equal(after.total, 1105);
  assert.equal(new Set([...before.rows, ...after.rows].map(r => r[0])).size, 100);
});
test('name filters require two letters and huge pages remain safe', async () => {
  const worker = harness([row('one', 'Alpha', '1')]);
  assert.match((await worker.query({ name: 'a' })).error, /at least two/);
  assert.match((await worker.query({ name: '!!' })).error, /at least two/);
  const huge = await worker.query({ page: Number.MAX_SAFE_INTEGER });
  assert.equal(huge.total, 1); assert.equal(huge.rows.length, 0);
});
test('every visible column, filter value and option escapes hostile HTML', async () => {
  const { pathToFileURL } = require('node:url');
  const { searchTable } = await import(pathToFileURL(path.join(__dirname, 'web/search-controls.js')).href);
  const hostile = '\"><script>alert(1)</script>&';
  const params = new URLSearchParams({ name: hostile, id: hostile, kind: hostile, source: hostile, mode: hostile });
  const html = searchTable([[hostile, hostile, hostile, hostile, hostile, hostile]], params, {
    kinds: { [hostile]: { label: hostile } }, sources: { [hostile]: 1 }, modes: [hostile],
  });
  assert.equal(html.includes('<script>'), false);
  assert.equal(html.includes(hostile), false);
  assert.ok(html.includes('&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;&amp;'));
  assert.equal((html.match(/data-column-filter=/g) || []).length, 5);
});

test('recognized aliases include literal names and both meanings without duplicate records',async()=>{
 const w=harness([row('a','Blackwing Lair',469,'map'),row('b','BWL key',1),row('c','Blackwing Lair BWL notes',2),row('d','Blackwing Forest',3),row('e','The Deadmines',36,'map'),row('f','Dire Maul East',429,'map'),row('g','DM field notes',4)]);
 for(const q of ['bwl','BWL','B.W.L.','b w l']){const r=await w.query({q});assert.deepEqual(r.rows.map(r=>r[0]).sort(),['a','b','c']);assert.equal(r.total,3);}
 assert.deepEqual((await w.query({q:'DM'})).rows.map(r=>r[0]).sort(),['e','f','g']);
 assert.deepEqual((await w.query({q:'Blackwing Lair'})).rows.map(r=>r[0]).sort(),['a','c']);
});
test('acronym qualifiers, column filters and exact numeric IDs compose',async()=>{
 const w=harness([row('a','Blackwing Lair Floor 4',469,'map','Exiles DB'),row('b','Blackwing Lair Floor 1',469,'map','Exiles DB'),row('c','Blackwing Lair Floor 4',469,'map','Client captures'),row('d','Scarlet Monastery Library',1,'map'),row('e','Scarlet Monastery Armory',2,'map')]);
 for(const q of ['BWL floor 4','B.W.L. floor 4','b w l floor 4'])assert.deepEqual((await w.query({q,source:'Exiles DB',kind:'map'})).rows.map(r=>r[0]),['a']);
 assert.deepEqual((await w.query({q:'469',name:'BWL',source:'Exiles DB'})).rows.map(r=>r[0]),['b','a']);
 assert.deepEqual((await w.query({q:'SM lib'})).rows.map(r=>r[0]),['d']);
 assert.equal((await w.query({q:'bwlxyz'})).total,0);
});
test('alias unions retain all source records across sorted pages and direction changes',async()=>{
 const rows=Array.from({length:130},(_,i)=>row('r'+i,(i%2?'BWL Blackwing Lair ':'Blackwing Lair ')+String(i).padStart(3,'0'),i));
 const w=harness(rows,11),first=await w.query({q:'bwl',sort:'id'}),second=await w.query({q:'bwl',sort:'id',page:1}),last=await w.query({q:'bwl',sort:'id',page:2});
 assert.equal(first.total,130);assert.equal(new Set([...first.rows,...second.rows,...last.rows].map(r=>r[0])).size,130);
 assert.equal((await w.query({q:'bwl',sort:'id',dir:'desc'})).rows[0][5],'129');
});

test('dictionary property names remain ordinary literal searches',async()=>{
 const w=harness([row('a','Constructor',1),row('b','Prototype',2)]);
 assert.deepEqual((await w.query({q:'constructor'})).rows.map(r=>r[0]),['a']);
 assert.deepEqual((await w.query({q:'__proto__'})).rows.map(r=>r[0]),['b']);
});

test('two alias buckets sharing one physical part still emit each record once',async()=>{
 const rows=[row('a','Deadmines Dire Maul comparison',1)],w=harness(rows);w.assets.set('shared',rows);
 w.manifest.search['name:de'].parts=['shared'];w.manifest.search['name:di'].parts=['shared'];
 const result=await w.query({q:'DM'});assert.equal(result.total,1);assert.equal(result.rows[0][0],'a');assert.equal(w.fetches,1);
});
