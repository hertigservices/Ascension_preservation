"""Capture provenance for browser uploads; receiving a file is not capturing it."""
import re
import time
import os

WEB_BUNDLE = re.compile(r'(?:^|/)web-[a-f0-9]{32}(?:/|$)')

def is_web_source(path):
    return bool(WEB_BUNDLE.search(path.replace('\\', '/')))

def source_date(path):
    return '' if is_web_source(path) else time.strftime('%Y-%m-%d', time.localtime(os.path.getmtime(path)))

def repair_dates(sources, index):
    """Repair previously imported web dates from source provenance, not guesses.

    All record payloads, mode associations and source IDs remain unchanged.
    A record also seen in a dated original retains that original's dates.
    """
    by_id = {r['id']: r for r in sources.values()}
    repaired = 0
    for row in by_id.values():
        if is_web_source(row['path']) and row['captured']:
            row['captured'] = ''
            repaired += 1
    for records in index.values():
        for row in records.values():
            dates = [by_id[sid]['captured'] for sid in row['srcs'] if by_id[sid]['captured']]
            row['first_captured'] = min(dates, default='')
            row['last_captured'] = max(dates, default='')
    return repaired

def attach_context(row, sources, cache=None):
    key = row['srcs']
    if cache is not None and key in cache:
        row['_dated_modes'], row['_first_observed'], row['_mode_dates'] = cache[key]
        return
    refs = [sources[sid] for sid in row['srcs'].split(',') if sid]
    row['_dated_modes'] = {r['slug'] for r in refs if r['captured']}
    first = {}
    for source in refs:
        sid = int(source['id'])
        for mode in ('*', source['slug']):
            first[mode] = min(sid, first.get(mode, sid))
    row['_first_observed'] = first
    row['_mode_dates'] = {mode: max((s['captured'] for s in refs if s['slug'] == mode), default='') for mode in first if mode != '*'}
    if cache is not None: cache[key] = (row['_dated_modes'], first, row['_mode_dates'])

def winner_date(row, mode=None):
    # Keep the established date ordering for dated observations. A variant seen
    # only in an undated upload for THIS mode must not borrow another mode's date.
    if mode and '_mode_dates' in row:
        return row['_mode_dates'].get(mode, '')
    if mode and mode not in row.get('_dated_modes', row['modes'].split(',')):
        return ''
    return row['last_captured']

def winner_rank(row, digest, mode=None):
    date = winner_date(row, mode)
    # Undated uploads add records/variants, but later arrivals do not replace an
    # already selected undated observation just because their hash sorts later.
    first = row.get('_first_observed', {}).get(mode or '*', 0)
    return date, -first if not date else 0, digest
