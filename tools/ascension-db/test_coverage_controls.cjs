const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const modulePromise = import(pathToFileURL(path.join(__dirname, 'web/coverage-controls.js')).href);
const file = (path, records, source = 'Client captures', reason = 'Every row searchable') => ({ path, records, source, reason, status: records === null ? 'reference' : 'indexed' });
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

test('coverage sorts the full file index before slicing and exposes files beyond the old cap', async () => {
  const { coveragePage } = await modulePromise;
  const coverage = Array.from({ length: 231 }, (_, i) => file('file-' + String(230 - i).padStart(3, '0'), i));
  const result = coveragePage(coverage, new URLSearchParams({ page: '4' }));
  assert.equal(result.total, 231); assert.equal(result.pages, 5);
  assert.equal(result.rows.length, 31); assert.equal(result.rows[0].path, 'file-200');
  const counts = coveragePage(coverage, new URLSearchParams({ sort: 'records', dir: 'desc', page: '1' }));
  assert.equal(counts.rows[0].records, 180); assert.equal(counts.rows.at(-1).records, 131);
  assert.equal(coverage[0].path, 'file-230', 'sorting does not mutate the loaded catalog');
});
test('record counts sort numerically and references stay last in both directions', async () => {
  const { coveragePage } = await modulePromise;
  const coverage = [file('reference', null), file('ten', 10), file('two', 2), file('zero', 0)];
  assert.deepEqual(coveragePage(coverage, new URLSearchParams({ sort: 'records' })).rows.map(f => f.records), [0, 2, 10, null]);
  assert.deepEqual(coveragePage(coverage, new URLSearchParams({ sort: 'records', dir: 'desc' })).rows.map(f => f.records), [10, 2, 0, null]);
});
test('source, availability, file, note, text and record range filters combine', async () => {
  const { coveragePage } = await modulePromise;
  const coverage = [file('cache/Alpha.tsv', 120, 'Client captures', 'Every row searchable'), file('cache/Beta.tsv', 80), file('cache/Alpha.lua', null), file('cache/Alpha2.tsv', 125, 'Exiles DB')];
  const params = new URLSearchParams({ q: 'CACHE', source: 'Client captures', status: 'indexed', path: 'alpha', note: 'searchable', min: '100', max: '121' });
  assert.deepEqual(coveragePage(coverage, params).rows.map(f => f.path), ['cache/Alpha.tsv']);
  params.set('max', '110'); assert.equal(coveragePage(coverage, params).total, 0);
});
test('status and note use visible column values and invalid pages clamp safely', async () => {
  const { coveragePage } = await modulePromise;
  const coverage = [file('a', 0, 'Source', 'Zulu'), file('b', null, 'Source', 'Alpha')];
  assert.deepEqual(coveragePage(coverage, new URLSearchParams({ sort: 'status' })).rows.map(f => f.path), ['b', 'a']);
  assert.deepEqual(coveragePage(coverage, new URLSearchParams({ sort: 'note', dir: 'desc' })).rows.map(f => f.path), ['a', 'b']);
  assert.equal(coveragePage(coverage, new URLSearchParams({ page: String(Number.MAX_SAFE_INTEGER) })).page, 0);
});
test('coverage rendering escapes all preserved text and filter values', async () => {
  const { renderCoverage } = await modulePromise;
  const hostile = '\"><script>alert(1)</script>&';
  const root = { innerHTML: '', querySelector: () => ({}), querySelectorAll: () => [] };
  renderCoverage({ root, coverage: [file(hostile, 4, hostile, hostile)], manifest: {}, sourceUrl: p => 'https://example.org/' + p, rawUrl: p => 'https://example.org/raw/' + p, esc: escape, params: new URLSearchParams({ q: hostile, path: hostile, note: hostile, source: hostile }) });
  assert.equal(root.innerHTML.includes('<script>'), false);
  assert.equal(root.innerHTML.includes(hostile), false);
  assert.ok(root.innerHTML.includes('&lt;script&gt;'));
});
test('sort and pagination navigation retain filters and use coverage URLs', async () => {
  const { renderCoverage } = await modulePromise;
  const applyButton = {}, clearButton = {}, sortButton = { dataset: { coverageSort: 'records' } }, nextButton = { dataset: { coveragePage: '1' } };
  const query = { value: 'cache', dataset: { coverageFilter: 'q' }, tagName: 'INPUT', reportValidity: () => true };
  const root = { innerHTML: '', querySelector: selector => selector.includes('clear') ? clearButton : applyButton,
    querySelectorAll: selector => selector.includes('filter') ? [query] : selector.includes('sort') ? [sortButton] : [nextButton] };
  global.location = { hash: '' };
  renderCoverage({ root, coverage: [file('cache/a', 1)], manifest: {}, sourceUrl: p => p, rawUrl: p => p, esc: escape, params: new URLSearchParams({ q: 'cache', source: 'Client captures', page: '8', sort: 'records' }) });
  sortButton.onclick();
  let params = new URLSearchParams(location.hash.split('?')[1]);
  assert.equal(params.get('q'), 'cache'); assert.equal(params.get('source'), 'Client captures'); assert.equal(params.get('dir'), 'desc'); assert.equal(params.has('page'), false);
  nextButton.onclick(); params = new URLSearchParams(location.hash.split('?')[1]);
  assert.equal(params.get('page'), '1'); assert.equal(params.get('q'), 'cache');
  assert.ok(location.hash.startsWith('coverage?'));
  clearButton.onclick(); params = new URLSearchParams(location.hash.split('?')[1]);
  assert.equal(params.has('q'), false); assert.equal(params.has('source'), false); assert.equal(params.get('sort'), 'records');
});
