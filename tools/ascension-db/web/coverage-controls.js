/* Sources & coverage uses the same full-result column ordering as database search. */
const PAGE_SIZE = 50;
const clean = value => String(value ?? '').normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase();
const compareText = (a, b) => a < b ? -1 : a > b ? 1 : 0;
const statusLabel = file => file.status === 'indexed' ? 'Searchable' : 'Reference';
const columns = [['path', 'Published file'], ['status', 'Availability'], ['records', 'Records'], ['note', 'Coverage note']];
export function coveragePage(coverage, params) {
  const term = key => clean(params.get(key) || '').trim();
  const q = term('q'), path = term('path'), note = term('note');
  const source = params.get('source') || '', status = params.get('status') || '';
  const boundary = key => /^\d+$/.test(params.get(key) || '') ? Number(params.get(key)) : null;
  const minimum = boundary('min'), maximum = boundary('max');
  const rows = coverage.filter(file => {
    if (source && file.source !== source || status && file.status !== status) return false;
    if (path && !clean(file.path).includes(path) || note && !clean(file.reason).includes(note)) return false;
    if (q && !clean(`${file.path} ${file.source} ${file.reason}`).includes(q)) return false;
    if (minimum !== null || maximum !== null) {
      if (file.status !== 'indexed' || !Number.isFinite(file.records)) return false;
      if (minimum !== null && file.records < minimum || maximum !== null && file.records > maximum) return false;
    }
    return true;
  });
  const sort = columns.some(([key]) => key === params.get('sort')) ? params.get('sort') : 'path';
  const dir = params.get('dir') === 'desc' ? 'desc' : 'asc', sign = dir === 'desc' ? -1 : 1;
  rows.sort((a, b) => {
    let order;
    if (sort === 'records') {
      const aa = a.status === 'indexed' && Number.isFinite(a.records) ? a.records : null;
      const bb = b.status === 'indexed' && Number.isFinite(b.records) ? b.records : null;
      // Missing record counts belong at the end in either direction.
      if ((aa === null) !== (bb === null)) return aa === null ? 1 : -1;
      order = aa === null ? 0 : aa - bb;
    } else {
      const value = file => sort === 'status' ? statusLabel(file) : sort === 'note' ? file.reason : file.path;
      order = compareText(clean(value(a)), clean(value(b)));
    }
    return sign * order || compareText(a.path, b.path);
  });
  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const requested = Number(params.get('page') || 0);
  const page = Number.isSafeInteger(requested) && requested >= 0 ? Math.min(requested, pages - 1) : 0;
  return { rows: rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE), total: rows.length, page, pages, sort, dir };
}
export function renderCoverage({ root, coverage, manifest, sourceUrl, rawUrl, esc, params = new URLSearchParams() }) {
  const state = coveragePage(coverage, params);
  const count = number => Number(number).toLocaleString();
  const option = (value, label, key) => `<option value="${esc(value)}"${params.get(key) === value ? ' selected' : ''}>${esc(label)}</option>`;
  const sources = [...new Set(coverage.map(file => file.source).filter(Boolean))].sort();
  const input = (key, label, placeholder, type = 'search') => `<input type="${type}" data-coverage-filter="${key}" aria-label="${label}" placeholder="${placeholder}" value="${esc(params.get(key) || '')}"${type === 'number' ? ' min="0" step="1"' : ' maxlength="200"'}>`;
  root.innerHTML = `<div class="coverage-tools">${input('q', 'Search coverage files, sources and notes', 'Search files, sources or notes…')}<select data-coverage-filter="source" aria-label="Filter coverage source"><option value="">All sources</option>${sources.map(source => option(source, source, 'source')).join('')}</select><button type="button" data-coverage-apply>Apply filters</button><button type="button" class="quiet-button" data-coverage-clear>Clear filters</button></div>
    <div class="result-head"><p class="muted">${count(state.total)} matching files · page ${state.page + 1} of ${state.pages}</p></div>
    <p class="muted" id="coverage-column-help">Select a heading to sort all matching files. Reference files have no searchable record count.</p>
    <div class="table-wrap"><table class="search-results coverage-results" aria-describedby="coverage-column-help"><thead><tr>${columns.map(([key, title]) => {
      const active = state.sort === key, direction = active && state.dir === 'asc' ? 'descending' : 'ascending';
      return `<th scope="col" aria-sort="${active ? state.dir === 'asc' ? 'ascending' : 'descending' : 'none'}"><button type="button" class="column-sort${active ? ' is-sorted' : ''}" data-coverage-sort="${key}" aria-label="Sort by ${title}, ${direction}">${title}<span aria-hidden="true">${active ? state.dir === 'asc' ? '↑' : '↓' : '↕'}</span></button></th>`;
    }).join('')}</tr><tr class="column-filters"><th>${input('path', 'Filter published file path', 'File path…')}</th><th><select data-coverage-filter="status" aria-label="Filter availability"><option value="">All files</option>${option('indexed', 'Searchable', 'status')}${option('reference', 'Reference', 'status')}</select></th><th>${input('min', 'Minimum searchable record count', 'Minimum', 'number')}${input('max', 'Maximum searchable record count', 'Maximum', 'number')}</th><th>${input('note', 'Filter coverage note', 'Coverage note…')}</th></tr></thead><tbody>${state.rows.length ? state.rows.map(file => `<tr><td><a href="${esc(sourceUrl(file.path))}">${esc(file.path)}</a><small>${esc(file.source)} · <a href="${esc(rawUrl(file.path))}">Download</a></small></td><td>${statusLabel(file)}</td><td class="record-id">${file.status === 'indexed' && Number.isFinite(file.records) ? count(file.records) : '—'}</td><td>${esc(file.reason)}</td></tr>`).join('') : '<tr><td colspan="4" class="empty-search">No matching files. Try fewer filters.</td></tr>'}</tbody></table></div>
    <div class="pagination"><button type="button" data-coverage-page="${state.page - 1}"${state.page === 0 ? ' disabled' : ''}>Previous</button><button type="button" data-coverage-page="${state.page + 1}"${state.page + 1 >= state.pages ? ' disabled' : ''}>Next</button></div>`;
  const navigate = (next, resetPage = true) => { if (resetPage) next.delete('page'); location.hash = 'coverage?' + next; };
  const applied = () => {
    const next = new URLSearchParams(params);
    for (const field of root.querySelectorAll('[data-coverage-filter]')) {
      if (!field.reportValidity()) return null;
      const value = field.value.trim(), key = field.dataset.coverageFilter;
      if (value) next.set(key, value); else next.delete(key);
    }
    return next;
  };
  const apply = () => { const next = applied(); if (next) navigate(next); };
  root.querySelector('[data-coverage-apply]').onclick = apply;
  root.querySelector('[data-coverage-clear]').onclick = () => {
    const next = new URLSearchParams(params);
    for (const key of ['q', 'path', 'source', 'status', 'min', 'max', 'note']) next.delete(key);
    navigate(next);
  };
  for (const field of root.querySelectorAll('[data-coverage-filter]')) {
    if (field.tagName === 'SELECT') field.onchange = apply;
    else field.onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); apply(); } };
  }
  for (const button of root.querySelectorAll('[data-coverage-sort]')) button.onclick = () => {
    const next = applied(); if (!next) return;
    next.set('sort', button.dataset.coverageSort);
    next.set('dir', button.dataset.coverageSort === state.sort && state.dir === 'asc' ? 'desc' : 'asc');
    navigate(next);
  };
  for (const button of root.querySelectorAll('[data-coverage-page]')) button.onclick = () => {
    const next = new URLSearchParams(params); next.set('page', button.dataset.coveragePage); navigate(next, false);
  };
}
