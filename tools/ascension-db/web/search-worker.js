importScripts('search-aliases.js', 'search-query.js');
/* Search runs in a worker: filter and order the entire candidate set before paging. */
const cache = new Map();
let latest = 0;
let activeController;
let resultCache;
const PAGE_SIZE = 50;
const WINDOW_SIZE = 1000;
const MAX_WINDOW = 5000;
const norm = (s) => String(s ?? '').normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase().replaceAll('ß', 'ss');
const words = (s) => norm(s).match(/[\p{L}\p{N}]+/gu) || [];
const compareText = (a, b) => a < b ? -1 : a > b ? 1 : 0;
// Do not coerce IDs to Number: published IDs may exceed JavaScript's safe integer range.
function compareId(a, b) {
  a = String(a); b = String(b);
  if (/^-?\d+$/.test(a) && /^-?\d+$/.test(b)) {
    const negativeA = a[0] === '-' && !/^-?0+$/.test(a);
    const negativeB = b[0] === '-' && !/^-?0+$/.test(b);
    if (negativeA !== negativeB) return negativeA ? -1 : 1;
    const aa = a.replace(/^-?0*/, '') || '0', bb = b.replace(/^-?0*/, '') || '0';
    const magnitude = compareText(aa.length, bb.length) || compareText(aa, bb);
    return (negativeA ? -magnitude : magnitude) || compareText(a, b);
  }
  if (/^-?\d+$/.test(a)) return -1;
  if (/^-?\d+$/.test(b)) return 1;
  return compareText(norm(a), norm(b)) || compareText(a, b);
}
function comparator(sort, direction, manifest) {
  const sign = direction === 'desc' ? -1 : 1;
  const field = { name: 1, kind: 2, mode: 3, source: 4, id: 5 }[sort] ?? 1;
  const prepared = new WeakMap();
  const value = r => {
    if (!prepared.has(r)) prepared.set(r, field === 2 ? norm(manifest.kinds[r[2]]?.label || r[2]) : norm(r[field]));
    return prepared.get(r);
  };
  return (a, b) => sign * (field === 5 ? compareId(a[5], b[5]) : compareText(value(a), value(b))) ||
    compareText(norm(a[1]), norm(b[1])) || compareId(a[5], b[5]) || compareText(a[0], b[0]);
}
// A max heap keeps only the earliest requested window, never all matching rows.
class TopRows {
  constructor(limit, compare) { this.limit = limit; this.compare = compare; this.rows = []; }
  add(row) {
    const a = this.rows, compare = this.compare;
    if (a.length < this.limit) {
      a.push(row);
      let i = a.length - 1;
      while (i) {
        const p = (i - 1) >> 1;
        if (compare(a[p], a[i]) >= 0) break;
        [a[p], a[i]] = [a[i], a[p]]; i = p;
      }
    } else if (compare(row, a[0]) < 0) {
      a[0] = row;
      let i = 0;
      while (true) {
        let c = i * 2 + 1;
        if (c >= a.length) break;
        if (c + 1 < a.length && compare(a[c + 1], a[c]) > 0) c++;
        if (compare(a[i], a[c]) >= 0) break;
        [a[i], a[c]] = [a[c], a[i]]; i = c;
      }
    }
  }
  sorted() { return this.rows.sort(this.compare); }
}
async function load(path, signal) {
  if (cache.has(path)) {
    const value = cache.get(path); cache.delete(path); cache.set(path, value); return value;
  }
  const response = await fetch(path, { signal });
  if (!response.ok) throw Error(`Catalog part unavailable (${response.status}). Refresh to load the latest snapshot.`);
  const buffer = await response.arrayBuffer(), bytes = new Uint8Array(buffer);
  const value = bytes[0] === 31 && bytes[1] === 139
    ? await new Response(new Blob([buffer]).stream().pipeThrough(new DecompressionStream('gzip'))).json()
    : JSON.parse(new TextDecoder().decode(buffer));
  if (signal.aborted) throw new DOMException('Search cancelled', 'AbortError');
  cache.set(path, value);
  while (cache.size > 12) cache.delete(cache.keys().next().value);
  return value;
}
const pause = () => new Promise(resolve => setTimeout(resolve, 0));
onmessage = async ({ data: d }) => {
  const job = ++latest;
  activeController?.abort();
  if (d.cancel) return;
  const controller = activeController = new AbortController();
  try {
    if (d.zone && !Object.hasOwn(d.manifest.search, 'zone:' + d.zone)) throw Error('This zone is unavailable in this snapshot. Clear the zone filter or choose another zone.');
    const q = String(d.q || '').trim();
    if (q && !/^-?\d+$/.test(q) && !AscensionSearchAliases.variants(q).some(ts=>ts.some(t=>t.length>=2))) throw Error('Enter at least two letters, or an exact numeric ID.');
    if (String(d.name || '').trim() && !AscensionSearchAliases.variants(d.name).some(ts=>ts.some(t=>t.length>=2))) throw Error('Enter at least two letters in the Name column filter.');
    const sort = ['name', 'id', 'kind', 'source', 'mode'].includes(d.sort) ? d.sort : 'name';
    const dir = d.dir === 'desc' ? 'desc' : 'asc';
    const key = JSON.stringify([d.dataBase, d.manifest.revision, d.manifest.built_at, q, d.name || '', d.recordId || '', d.kind || '', d.source || '', d.mode || '', d.zone || '', d.locale || '', sort, dir]);
    const page = Math.min(Number.isSafeInteger(d.page) && d.page >= 0 ? d.page : 0, Math.floor(Number.MAX_SAFE_INTEGER / PAGE_SIZE) - 1);
    const start = page * PAGE_SIZE;
    const end = start + PAGE_SIZE;
    const compare = comparator(sort, dir, d.manifest);
    let saved = resultCache?.key === key ? resultCache : null;
    if (saved && (start >= saved.total || (start >= saved.offset && end <= saved.offset + saved.rows.length) || (start >= saved.offset && saved.offset + saved.rows.length === saved.total))) {
      postMessage({ id: d.id, rows: saved.rows.slice(Math.max(0, start - saved.offset), end - saved.offset), total: saved.total, sourceTotal: saved.sourceTotal }); return;
    }
    const { parts, match } = AscensionSearchQuery.candidates(d);
    let offset = 0, anchor = null;
    if (saved && start >= saved.offset + saved.rows.length && saved.rows.length) {
      offset = saved.offset + saved.rows.length; anchor = saved.rows.at(-1);
    }
    // Far bookmarked pages use successive bounded windows; ordinary Next navigation reuses the previous window.
    while (true) {
      const limit = Math.min(MAX_WINDOW, Math.max(WINDOW_SIZE, end - offset));
      const heap = new TopRows(limit, compare);
      let total = 0, sourceTotal = 0;
      for (let batch = 0; batch < parts.length; batch += 4) {
        const loaded = await Promise.all(parts.slice(batch, batch + 4).map(part => load((d.dataBase || '') + part, controller.signal)));
        for (let b = 0; b < loaded.length; b++) {
        const i = batch + b, rows = loaded[b];
        if (job !== latest) return;
        for (let j = 0; j < rows.length; j++) {
          const r = rows[j];
          const matched = match(r,parts[i]);
          if (matched) { total++; sourceTotal += matched[7]?.members.length || 1; if (!anchor || compare(matched, anchor) > 0) heap.add(matched); }
          if (j && j % 4096 === 0) { await pause(); if (job !== latest) return; }
        }
        postMessage({ id: d.id, progress: i + 1, parts: parts.length, skipped: offset });
        // A warm partition cache must still yield so cancel and route changes are handled.
        await pause(); if (job !== latest) return;
        }
      }
      const rows = heap.sorted();
      if (job !== latest) return;
      resultCache = { key, offset, rows, total, sourceTotal };
      if (end <= offset + rows.length || offset + rows.length >= total || start >= total || !rows.length) {
        postMessage({ id: d.id, rows: start >= total ? [] : rows.slice(start - offset, end - offset), total, sourceTotal }); return;
      }
      offset += rows.length; anchor = rows.at(-1);
    }
  } catch (error) {
    if (job === latest && error.name !== 'AbortError') postMessage({ id: d.id, error: error.message });
  }
};
