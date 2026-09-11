const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const columns = [['name', 'Name'], ['id', 'ID'], ['kind', 'Collection'], ['source', 'Source'], ['mode', 'Game mode']];
export function searchColumnState(params) {
  return {
    name: params.get('name') || '', recordId: params.get('id') || '',
    sort: columns.some(([key]) => key === params.get('sort')) ? params.get('sort') : 'name',
    dir: params.get('dir') === 'desc' ? 'desc' : 'asc',
  };
}
function options(items, selected, empty) {
  return `<option value="">${escapeHtml(empty)}</option>` + items.map(([value, label]) =>
    `<option value="${escapeHtml(value)}"${value === selected ? ' selected' : ''}>${escapeHtml(label)}</option>`).join('');
}
export function searchTable(rows, params, manifest) {
  const state = searchColumnState(params);
  const select = (key, items, empty) => `<select data-column-filter="${key}" aria-label="Filter ${key === 'kind' ? 'collection' : key === 'mode' ? 'game mode' : key}">${options(items, params.get(key) || '', empty)}</select>`;
  const collectionOptions = Object.entries(manifest.kinds).map(([key, value]) => [key, value.label]).sort((a, b) => a[1].localeCompare(b[1]));
  return `<div class="search-column-tools"><p class="muted" id="column-help">Select a heading to sort all matching records. Combine column filters to narrow your search.</p><button type="button" class="quiet-button" data-clear-columns>Clear column filters</button></div>
    <div class="table-wrap"><table class="search-results" aria-describedby="column-help"><thead><tr>${columns.map(([key, label]) => {
      const active = state.sort === key;
      const direction = active && state.dir === 'asc' ? 'descending' : 'ascending';
      return `<th scope="col" aria-sort="${active ? state.dir === 'asc' ? 'ascending' : 'descending' : 'none'}"><button type="button" class="column-sort${active ? ' is-sorted' : ''}" data-sort="${key}" aria-label="Sort by ${label}, ${direction}">${label}<span aria-hidden="true">${active ? state.dir === 'asc' ? '↑' : '↓' : '↕'}</span></button></th>`;
    }).join('')}</tr><tr class="column-filters">
      <th><input type="search" data-column-filter="name" value="${escapeHtml(state.name)}" aria-label="Filter name by word prefix" placeholder="Name prefix…" maxlength="200"></th>
      <th><input type="search" data-column-filter="id" value="${escapeHtml(state.recordId)}" aria-label="Filter exact ID" placeholder="Exact ID…" maxlength="200"></th>
      <th>${select('kind', collectionOptions, 'All collections')}</th>
      <th>${select('source', Object.keys(manifest.sources).sort().map(value => [value, value]), 'All sources')}</th>
      <th>${select('mode', manifest.modes.map(value => [value, value]), 'All modes')}</th>
    </tr></thead><tbody>${rows.length ? rows.map(r => `<tr><td><a href="#record=${escapeHtml(r[0])}">${escapeHtml(r[1])}</a></td><td class="record-id">${escapeHtml(r[5])}</td><td>${escapeHtml(manifest.kinds[r[2]]?.label || r[2])}</td><td>${escapeHtml(r[4])}</td><td><span class="badge">${escapeHtml(r[3])}</span></td></tr>`).join('') : '<tr><td colspan="5" class="empty-search">No matching records. Try fewer words or clear a column filter.</td></tr>'}</tbody></table></div><div class="column-filter-actions"><span class="muted">Name uses word prefixes · ID matches exactly</span><button type="button" data-apply-columns>Apply column filters</button></div>`;
}
export function bindSearchColumns(container, params) {
  const navigate = p => { p.delete('page'); location.hash = 'search?' + p; };
  const applied = () => {
    const p = new URLSearchParams(params);
    for (const field of container.querySelectorAll('[data-column-filter]')) {
      const key = field.dataset.columnFilter, value = field.value.trim();
      if (value) p.set(key, value); else p.delete(key);
    }
    return p;
  };
  for (const button of container.querySelectorAll('[data-sort]')) button.onclick = () => {
    const p = applied(), state = searchColumnState(params), sort = button.dataset.sort;
    p.set('sort', sort); p.set('dir', sort === state.sort && state.dir === 'asc' ? 'desc' : 'asc'); navigate(p);
  };
  for (const field of container.querySelectorAll('[data-column-filter]')) {
    if (field.tagName === 'SELECT') field.onchange = () => navigate(applied());
    else field.onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); navigate(applied()); } };
  }
  container.querySelector('[data-apply-columns]').onclick = () => navigate(applied());
  container.querySelector('[data-clear-columns]').onclick = () => {
    const p = new URLSearchParams(params);
    for (const [key] of columns) p.delete(key);
    navigate(p);
  };
}
