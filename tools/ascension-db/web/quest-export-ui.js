import {searchColumnState} from './search-controls.js';
let active;
export function cancelQuestExport() {
  if (active) { active.worker.terminate(); active = null; }
}
export function bindQuestExport(container, params, manifest, dataBase) {
  const button = container.querySelector('[data-export-quests]');
  if (!button) return;
  const status = container.querySelector('[data-export-status]');
  button.onclick = () => {
    if (active) { cancelQuestExport(); button.textContent = 'Convert to SQL'; status.textContent = 'Export cancelled.'; return; }
    // Include unapplied text-column edits as well as the currently applied filters.
    const selected = new URLSearchParams(params);
    for (const field of container.querySelectorAll('[data-column-filter]')) {
      const value = field.value.trim();
      if (value) selected.set(field.dataset.columnFilter, value); else selected.delete(field.dataset.columnFilter);
    }
    selected.delete('page');
    const worker = new Worker('quest-export-worker.js');
    const identity = active = {worker};
    button.textContent = 'Cancel SQL export';
    status.textContent = 'Preparing all matching quests…';
    const finish = message => { if (active !== identity) return; worker.terminate(); active = null; button.textContent = 'Convert to SQL'; status.textContent = message; };
    worker.onerror = () => finish('Export failed. Please retry or use the full SQL report.');
    worker.onmessage = ({data}) => {
      if (active !== identity) return;
      if (data.error) { finish(data.error); return; }
      if (data.progress) {
        const p = data.progress;
        status.textContent = `${p.phase}: ${p.done.toLocaleString()} / ${p.total.toLocaleString()} parts · ${p.records.toLocaleString()} source records`;
        return;
      }
      const url = URL.createObjectURL(data.blob), a = document.createElement('a');
      a.href = url; a.download = `ascension-quests-${manifest.revision.slice(0, 12)}.sql.gz`;
      document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 60000);
      finish(`Downloaded ${data.result.records.toLocaleString()} quest source records, including ${data.result.item_rewards.toLocaleString()} item reward slots. SQL is gzip-compressed; details and any field issues are included.`);
    };
    worker.postMessage({id: 1, manifest, dataBase, ...Object.fromEntries(selected),
      ...searchColumnState(selected), filters: Object.fromEntries(selected)});
  };
}
