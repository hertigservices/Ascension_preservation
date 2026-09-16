// Editable combobox: typing narrows choices; selecting one applies an exact facet.
const normalized = value => String(value).normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase();
export function zoneOptions(facets, kind, query = '') {
  const terms = normalized(query).trim().split(/\s+/).filter(Boolean);
  return (facets?.zones || []).filter(zone => (!kind || zone.kinds[kind]) &&
    terms.every(term => normalized(`${zone.label} ${zone.key}`).includes(term)));
}

export function bindZoneFilter(root, facets, onSelect) {
  const input = root.querySelector('input');
  const toggle = root.querySelector('[data-zone-toggle]');
  const clear = root.querySelector('[data-zone-clear]');
  const list = root.querySelector('[role=listbox]');
  const status = root.querySelector('[data-zone-status]');
  let selected = '', kind = '', active = -1, choices = [];
  const label = () => facets?.zones.find(zone => zone.key === selected)?.label || (selected ? 'Unavailable zone' : '');
  const opened = () => input.getAttribute('aria-expanded') === 'true';
  function close(restore = true) {
    list.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
    active = -1;
    if (restore) input.value = label();
  }
  function highlight(index) {
    active = index;
    for (const [i, option] of [...list.children].entries()) option.classList.toggle('is-active', i === active);
    const option = list.children[active];
    if (option) {
      input.setAttribute('aria-activedescendant', option.id);
      option.scrollIntoView({block: 'nearest'});
    } else input.removeAttribute('aria-activedescendant');
  }
  function choose(key) {
    selected = key;
    close();
    clear.hidden = !selected;
    input.focus();
    onSelect(key);
  }
  function open(query = '') {
    choices = [{key: '', label: 'All zones'}, ...zoneOptions(facets, kind, query)];
    list.replaceChildren();
    choices.forEach((zone, i) => {
      const option = document.createElement('div');
      option.id = `zone-option-${i}`;
      option.setAttribute('role', 'option');
      option.setAttribute('aria-selected', String(zone.key === selected));
      option.textContent = zone.label;
      if (zone.key) {
        const count = document.createElement('small');
        count.textContent = (kind ? zone.kinds[kind] : zone.count).toLocaleString() + ' source records';
        option.append(count);
      }
      option.addEventListener('mousedown', event => event.preventDefault());
      option.addEventListener('click', () => choose(zone.key));
      list.append(option);
    });
    status.textContent = choices.length === 1 ? 'No matching zones.' : `${choices.length - 1} zones available.`;
    list.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    highlight(query ? (choices.length > 1 ? 1 : -1) : Math.max(0, choices.findIndex(zone => zone.key === selected)));
  }
  input.addEventListener('click', () => { if (!opened()) { input.select(); open(); } });
  input.addEventListener('input', () => open(input.value));
  input.addEventListener('keydown', event => {
    if (['ArrowDown', 'ArrowUp', 'Escape', 'Enter'].includes(event.key)) {
      if (event.key === 'Enter' && !opened()) return;
      event.preventDefault();
      if (event.key === 'Escape') { close(); return; }
      if (event.key === 'Enter') { if (active >= 0) choose(choices[active].key); return; }
      if (!opened()) { open(); return; }
      highlight(Math.max(0, Math.min(choices.length - 1, active + (event.key === 'ArrowDown' ? 1 : -1))));
    }
  });
  toggle.addEventListener('click', () => { input.focus(); if (opened()) close(); else open(); });
  clear.addEventListener('click', () => choose(''));
  root.addEventListener('focusout', event => { if (!root.contains(event.relatedTarget)) close(); });
  document.addEventListener('pointerdown', event => { if (!root.contains(event.target)) close(); });
  return {
    get value() { return selected; },
    update(nextKind, value = '') {
      kind = nextKind;
      const supported = Boolean(facets && (!kind || facets.kinds[kind] || value));
      root.hidden = !supported;
      selected = supported ? value : '';
      clear.hidden = !selected;
      close();
    },
  };
}
