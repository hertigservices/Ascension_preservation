const {test} = require('node:test');
const assert = require('node:assert/strict');
const {pathToFileURL} = require('node:url');
const path = require('node:path');
const modulePromise = import(pathToFileURL(path.join(__dirname, 'web/collections.js')).href);
const esc = v => String(v).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
test('every published category including newly discovered ones gets a browse link', async () => {
 const {collectionDirectory} = await modulePromise;
 const kinds = {item:{label:'Items',records:3}, Auctionator:{label:'Auctionator',records:2}, futureKind:{label:'Future category',records:5}};
 const html = collectionDirectory(kinds,esc);
 assert.equal((html.match(/<a /g)||[]).length,3);
 for (const key of Object.keys(kinds)) assert.ok(html.includes('#search?kind='+key));
 assert.ok(html.includes('Auction price observations')); assert.ok(html.includes('Future category'));
 assert.equal(kinds.Auctionator.label,'Auctionator');
});
test('category labels and URL values remain inert', async () => {
 const {collectionDirectory} = await modulePromise;
 const html=collectionDirectory({'new&kind':{label:'<img src=x onerror=alert(1)>',records:1}},esc);
 assert.ok(html.includes('kind=new%26kind')); assert.ok(html.includes('&lt;img')); assert.ok(!html.includes('<img'));
});
