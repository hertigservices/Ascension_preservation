#!/usr/bin/env node
/* Node 22+. Uses exactly the same conversion and filter code as AscensionDB. */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');
const crypto = require('node:crypto');
const {spawnSync} = require('node:child_process');
const {once} = require('node:events');
const web = path.join(__dirname, '../ascension-db/web');
for (const name of ['search-aliases', 'search-query', 'quest-sql', 'quest-server-sql', 'quest-export']) require(path.join(web, name + '.js'));

async function main() {
  const args = process.argv.slice(2), options = {};
  for (let i = 0; i < args.length; i += 2) {
    if (!['--catalog', '--out', '--cache', '--filters'].includes(args[i]) || args[i + 1] === undefined) throw Error('Usage: node tools/ascension-sql/convert.cjs --catalog https://HOST/catalog/snapshots/HASH/ --out NEW_DIRECTORY [--cache DIRECTORY] [--filters "kind=quest&mode=conquest-of-azeroth"]');
    options[args[i].slice(2)] = args[i + 1];
  }
  const base = new URL(options.catalog);
  if (base.protocol !== 'https:' || base.username || base.password || base.search || base.hash || !base.pathname.endsWith('/')) throw Error('Use an immutable HTTPS catalog directory ending in /');
  if (!options.out) throw Error('--out is required');
  const out = path.resolve(options.out), cache = path.resolve(options.cache || path.join(out, 'download-cache'));
  fs.mkdirSync(out, {recursive: false}); fs.mkdirSync(cache, {recursive: true});
  const load = async relative => {
    if (!/^[a-zA-Z0-9_./-]+$/.test(relative) || relative.split('/').includes('..') || relative.startsWith('/')) throw Error('Invalid catalog path');
    const url = new URL(relative, base).href;
    const filename = path.join(cache, crypto.createHash('sha256').update(url).digest('hex'));
    if (!fs.existsSync(filename)) {
      const result = spawnSync('curl', ['--fail', '--silent', '--show-error', '--location', '--retry', '3', '--max-time', '120', '--output', filename + '.part', '--', url], {encoding: 'utf8'});
      if (result.status !== 0) throw Error('Catalog download failed: ' + relative + ' ' + result.stderr);
      fs.renameSync(filename + '.part', filename);
    }
    const bytes = fs.readFileSync(filename);
    return JSON.parse((bytes[0] === 31 && bytes[1] === 139 ? zlib.gunzipSync(bytes) : bytes).toString('utf8'));
  };
  const manifest = await load('manifest.json');
  const params = new URLSearchParams(options.filters || 'kind=quest'); params.delete('page');
  const itemEvidence = JSON.parse(zlib.gunzipSync(fs.readFileSync(path.join(web, 'quest-item-evidence.json.gz'))));
  const request = {itemEvidence, ...Object.fromEntries(params), filters: Object.fromEntries(params), recordId: params.get('id') || '', dataBase: base.href, manifest};
  const partial = path.join(out, 'quests.sql.gz.partial');
  const compressed = zlib.createGzip({level: 6}), destination = fs.createWriteStream(partial, {flags: 'wx'});
  compressed.pipe(destination);
  const finished = once(destination, 'close');
  let streamError;
  destination.on('error', e => { streamError = e; compressed.destroy(e); });
  compressed.on('error', e => { streamError = e; destination.destroy(e); });
  finished.catch(() => {});
  let last = '';
  const result = await AscensionQuestExport.run(request, {load,
    write: async value => { if (streamError) throw streamError; if (!compressed.write(value, 'utf8')) await once(compressed, 'drain'); },
    progress: p => {
      if (p.phase !== last || p.done % 20 === 0 || p.done === p.total) console.error(`${p.phase}: ${p.done}/${p.total}; ${p.records} records`);
      last = p.phase;
    },
  });
  compressed.end(); await finished; if (streamError) throw streamError;
  fs.renameSync(partial, path.join(out, 'quests.sql.gz'));
  const report = {schema: AscensionQuestSQL.VERSION, revision: manifest.revision, catalog: base.href, filters: request.filters, ...result,
    notes: ['Counts are source records, not unique quests. Published overlapping views stay separate.',
      'Item-based currencies retain their exact IDs and quantities; no currency identity is guessed from a name.',
      'NULL means unavailable or unmapped. Original fields and every conversion issue remain in SQL.',
      'The export includes a guarded server reward importer. Importing the file alone does not apply world changes; run its explicit dry-run/apply procedure.']};
  fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify(report, null, 2) + '\n');
  fs.writeFileSync(path.join(out, 'manifest.json'), JSON.stringify({revision: manifest.revision, catalog: base.href,
    converter: AscensionQuestSQL.VERSION, implementation_sha256: Object.fromEntries(['quest-sql.js','quest-server-sql.js','quest-export.js','search-query.js','search-aliases.js','quest-item-evidence.json.gz'].map(name => [name, crypto.createHash('sha256').update(fs.readFileSync(path.join(web,name))).digest('hex')])), files: Object.fromEntries(['quests.sql.gz', 'report.json'].map(name => {
      const b = fs.readFileSync(path.join(out, name)); return [name, {bytes: b.length, sha256: crypto.createHash('sha256').update(b).digest('hex')}];
    }))}, null, 2) + '\n');
  console.log(JSON.stringify(report, null, 2));
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
