"""Structural extractors for the db.exil.es (coa-db) offline mirror.

Standard library only. Every extractor preserves what the page states and
records raw marker codes verbatim; none of them infer a value the page does
not carry. See docs/EXILES-DB.md.
"""
import html
import re

# The mirror is server-rendered BEM markup, so containers are addressed by their
# block/element class rather than by position. A page that changes shape yields
# fewer fields; it never yields a wrong one.
TAG = re.compile(r'(?s)<[^>]+>')
SCRIPTISH = re.compile(r'(?is)<(script|style)\b.*?</\1>')
COMMENT = re.compile(r'(?s)<!--(.*?)-->')
WHITESPACE = re.compile(r'\s+')
ROW = re.compile(r'(?is)<tr\b[^>]*>(.*?)</tr>')
CELL = re.compile(r'(?is)<(t[dh])\b[^>]*>(.*?)</\1>')
LIST_ITEM = re.compile(r'(?is)<li\b[^>]*>(.*?)</li>')
DT = re.compile(r'(?is)<dt\b[^>]*>(.*?)</dt>\s*<dd\b[^>]*>(.*?)</dd>')
ANCHOR = re.compile(r'(?is)<a\b[^>]*href="([^"]*)"[^>]*>(.*?)</a>')
TALENT = re.compile(r'(?is)<a class="talent-cell[^"]*"[^>]*?href="/spell/(-?\d+)"[^>]*?>(.*?)</a>')
ENTITY_HREF = re.compile(r'/(spell|item|npc|quest|achievement|gameobject|area|dungeon|map|skill|currency|pets|tree|class|faction)/([^"?#]+)')
# A `<div>` carrying a class whose content holds no nested block container. The
# site renders several single-value fields this way -- a quest's money, XP and
# talent-point rewards among them -- and they belong to no list or table.
LEAF_DIV = re.compile(r'(?is)<div\b[^>]*class="([^"]+)"[^>]*>((?:(?!<(?:div|ul|ol|table|section|article)\b).)*?)</div>')
ICON = re.compile(r'/icons-clean/([^"/?]+?)\.png')
PORTRAIT = re.compile(r'/creatures/([^"/?]+?)\.webp')
GRID = re.compile(r'grid-row:\s*(\d+);\s*grid-column:\s*(\d+)')
TITLE_ATTR = re.compile(r'(?is)title="([^"]*)"')
RANK = re.compile(r'(?is)<span class="talent-cell__rank">\s*(\d+)\s*/\s*(\d+)\s*</span>')
QUALITY = re.compile(r'\bq(\d)\b')


def unescape(fragment):
    """Collapse a markup fragment to its visible text."""
    return WHITESPACE.sub(' ', html.unescape(TAG.sub(' ', fragment))).strip()


def block(page, cls, tag=r'\w+'):
    """Return the inner markup of the first element carrying `cls`.

    Matching is brace-free and depth-aware only to the extent the mirror needs:
    each targeted container is a leaf-ish section whose closing tag is the next
    one of the same name at the same nesting level.
    """
    opener = re.compile(r'(?is)<(%s)\b[^>]*class="[^"]*\b%s\b[^"]*"[^>]*>' % (tag, re.escape(cls)))
    found = opener.search(page)
    if not found:
        return ''
    name = found.group(1)
    depth, index = 1, found.end()
    step = re.compile(r'(?is)<(/?)%s\b[^>]*?(/?)>' % re.escape(name))
    while depth:
        nxt = step.search(page, index)
        if not nxt:
            return page[found.end():]
        depth += -1 if nxt.group(1) else (0 if nxt.group(2) else 1)
        index = nxt.end()
    return page[found.end():index - len(nxt.group(0))]


def blocks(page, cls, tag=r'\w+'):
    """Every element carrying `cls`, in document order."""
    out, rest = [], page
    opener = re.compile(r'(?is)<(%s)\b[^>]*class="[^"]*\b%s\b[^"]*"[^>]*>' % (tag, re.escape(cls)))
    while True:
        found = opener.search(rest)
        if not found:
            return out
        inner = block(rest, cls, tag)
        out.append(inner)
        rest = rest[found.end() + len(inner):]


def text_of(page, cls, tag=r'\w+'):
    return unescape(block(page, cls, tag))


def strip_chrome(page):
    """Drop the shared header/footer/search so page text is page-specific."""
    body = SCRIPTISH.sub(' ', page)
    main = block(body, 'site-main', 'main')
    return main or body


def definition_list(fragment):
    """`<dt>label</dt><dd>value</dd>` pairs as an ordered list of pairs."""
    return [[unescape(k), unescape(v)] for k, v in DT.findall(fragment)]


def table_rows(fragment):
    """Rows of a table as lists of cell text, header row included separately."""
    header, body = [], []
    for row in ROW.findall(fragment):
        cells = CELL.findall(row)
        values = [unescape(v) for _, v in cells]
        if cells and all(kind.lower() == 'th' for kind, _ in cells):
            header = values
        elif values:
            body.append(values)
    return header, body


def links(fragment):
    """Entity references a fragment points at, de-duplicated, in order."""
    seen, out = set(), []
    for href, label in ANCHOR.findall(fragment):
        ref = ENTITY_HREF.search(href)
        if not ref:
            continue
        key = (ref.group(1), ref.group(2))
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(type=ref.group(1), key=ref.group(2), label=unescape(label)))
    return out


def icon_name(fragment):
    found = ICON.search(fragment)
    return found.group(1) if found else None


def quality(fragment):
    found = QUALITY.search(fragment)
    return int(found.group(1)) if found else None


def markers(fragment):
    """Raw `<!--code-->` tooltip markers, verbatim and in order.

    These carry the numeric stat/rating/subclass ids the rendered text spells
    out in words. They are preserved uninterpreted: `stat5` is recorded as
    `stat5`, not as "Intellect".
    """
    return [m.strip() for m in COMMENT.findall(fragment) if m.strip()]


def tooltip_lines(fragment):
    """Tooltip text split into lines, each with the marker codes that precede it."""
    out = []
    for chunk in re.split(r'(?i)<br\s*/?>', fragment):
        line = unescape(chunk)
        codes = markers(chunk)
        if line or codes:
            out.append(dict(text=line, markers=codes))
    return out


def history_rows(fragment):
    """`entity-history` rows: dated field changes with old value, new and source.

    Item, quest and gameobject pages all carry this block, so it is shared
    rather than reimplemented per page type.
    """
    out = []
    for row in blocks(fragment, 'entity-history__row', 'li'):
        when = re.search(r'(?is)<time\b[^>]*>(.*?)</time>', row)
        values = [unescape(v) for v in blocks(row, 'changelog-row__value', 'code')]
        note = [unescape(m) for m in blocks(row, 'muted', 'span')]
        out.append(dict(date=unescape(when.group(1)) if when else None,
                        field=text_of(row, 'entity-history__field'),
                        old=values[0] if values else None,
                        new=values[1] if len(values) > 1 else None,
                        source=note[-1].strip('()') if note else None))
    return out


def generic(page):
    """Structure common to every page: headings, prose, lists, tables and links.

    Used for page types without a hand-written extractor so that no mirrored
    page contributes nothing, and as a completeness check on the tuned ones.
    Prose and list items are captured as well as tables because a quest's
    objectives and an achievement's description live in `<p>` and `<li>`, and a
    catalog that kept only the tables would silently drop both.
    """
    main = strip_chrome(page)
    sections = []
    for found in re.finditer(r'(?is)<h([1-3])\b[^>]*>(.*?)</h\1>', main):
        sections.append(unescape(found.group(2)))
    tables = []
    for found in re.finditer(r'(?is)<table\b[^>]*>(.*?)</table>', main):
        header, body = table_rows(found.group(1))
        if body:
            tables.append(dict(header=header, rows=body))
    tabular = re.sub(r'(?is)<table\b.*?</table>', ' ', main)
    paragraphs = [unescape(p) for p in re.findall(r'(?is)<p\b[^>]*>(.*?)</p>', tabular)]
    items = [unescape(i) for i in LIST_ITEM.findall(tabular)]
    fields = []
    for classes, inner in LEAF_DIV.findall(tabular):
        text = unescape(inner)
        if text:
            fields.append(dict(field=classes.split()[0], text=text))
    return dict(headings=sections,
                paragraphs=[p for p in paragraphs if p],
                list_items=[i for i in items if i],
                fields=fields,
                meta=definition_list(main), tables=tables,
                history=history_rows(main), references=links(main))


def title_of(page):
    for cls in ('spell-page__name', 'npc-page__name', 'item-header', 'npc-page__header'):
        found = text_of(page, cls)
        if found:
            return found
    found = re.search(r'(?is)<h1\b[^>]*>(.*?)</h1>', strip_chrome(page))
    return unescape(found.group(1)) if found else ''


def parse_spell(page, key):
    main = strip_chrome(page)
    info = block(main, 'spell-info', 'table')
    fields = {}
    for row in ROW.findall(info):
        cells = CELL.findall(row)
        if len(cells) == 2:
            fields[unescape(cells[0][1])] = unescape(cells[1][1])
    effects = []
    for item in blocks(main, 'effect-row', 'li'):
        effects.append(dict(name=text_of(item, 'effect-row__name'),
                            detail=unescape(block(item, 'muted', 'span')).lstrip('· ').strip(),
                            value=text_of(item, 'effect-row__value')))
    school = re.search(r'spell-page--([a-z-]+)', main)
    return dict(id=key, name=text_of(main, 'spell-page__name') or title_of(page),
                description=text_of(main, 'spell-page__desc'),
                school_class=school.group(1) if school else None,
                icon=icon_name(main), info=fields, effects=effects,
                cast_by_npcs=links(block(main, 'spell-page__cast-by-npcs', 'section')),
                taught_by=links(block(main, 'spell-page__taught-by', 'section')))


def parse_item(page, key):
    main = strip_chrome(page)
    tooltip = block(main, 'item-tooltip')
    sources = []
    for item in LIST_ITEM.findall(block(main, 'item-sources', 'ul')):
        ref = links(item)
        muted = [unescape(m) for m in blocks(item, 'muted', 'span')]
        sources.append(dict(entity=ref[0] if ref else None, label=unescape(item), notes=muted))
    history = history_rows(main)
    scaler = re.search(r'data-default-level="(\d+)"', main)
    return dict(id=key, name=title_of(page), quality=quality(block(main, 'item-header')),
                icon=icon_name(main), tooltip=tooltip_lines(tooltip),
                tooltip_text=unescape(tooltip), sources=sources, history=history,
                default_level=int(scaler.group(1)) if scaler else None)


def _drop_table(fragment):
    header, rows = table_rows(fragment)
    out = []
    for row in ROW.findall(fragment):
        cells = CELL.findall(row)
        if not cells or all(kind.lower() == 'th' for kind, _ in cells):
            continue
        ref = links(cells[0][1])
        values = [unescape(v) for _, v in cells]
        entry = dict(entity=ref[0] if ref else None, cells=values)
        for name, value in zip(header[1:], values[1:]):
            entry[name.lower()] = value
        out.append(entry)
    return out


def parse_npc(page, key):
    main = strip_chrome(page)
    portrait = PORTRAIT.search(main)
    return dict(id=key, name=text_of(main, 'npc-page__name') or title_of(page),
                levels=text_of(main, 'npc-page__levels'),
                meta=dict(definition_list(block(main, 'npc-page__meta', 'dl'))),
                health=text_of(main, 'npc-page__hp'), damage=text_of(main, 'npc-page__dmg'),
                display_id=portrait.group(1) if portrait else None,
                drops=_drop_table(block(main, 'npc-drops__table', 'table')),
                pickpocket=_drop_table(block(block(main, 'npc-pickpocket', 'section'), 'npc-drops__table', 'table')),
                casts=_drop_table(block(main, 'npc-casts__table', 'table')),
                quests=links(block(main, 'npc-quests', 'section')))


def parse_tree(page, key):
    """A talent tree: every cell's spell id, name, icon, grid position and ranks.

    The site renders two layouts from the same cell markup: a positioned
    `talent-grid` for the CoA classes and a `moa-tree-flat` list for the stock
    ones. Cells are read from the enclosing tree section so both are covered;
    only the grid layout carries row/column coordinates.
    """
    main = strip_chrome(page)
    section = block(main, 'talent-tree', 'section') or main
    grid = block(section, 'talent-grid', 'div')
    layout = 'grid' if grid else ('flat' if block(section, 'moa-tree-flat', 'div') else None)
    talents = []
    for found in re.finditer(r'(?is)<a class="talent-cell[^"]*"[^>]*>.*?</a>', section):
        cell = found.group(0)
        spell = re.search(r'href="/spell/(-?\d+)"', cell)
        position = GRID.search(cell)
        ranks = RANK.search(cell)
        name = TITLE_ATTR.search(cell)
        talents.append(dict(spell_id=int(spell.group(1)) if spell else None,
                            name=html.unescape(name.group(1)) if name else None,
                            icon=icon_name(cell),
                            row=int(position.group(1)) if position else None,
                            column=int(position.group(2)) if position else None,
                            max_rank=int(ranks.group(2)) if ranks else None,
                            filled='talent-cell--filled' in cell))
    # The grid dimensions live on the container's own style attribute, which is
    # part of its opening tag and therefore outside the inner markup above.
    opening = re.search(r'(?is)<div[^>]*class="[^"]*\btalent-grid\b[^"]*"[^>]*>', section)
    style = opening.group(0) if opening else ''
    columns = re.search(r'grid-template-columns:\s*repeat\((\d+)', style)
    rows = re.search(r'grid-template-rows:\s*repeat\((\d+)', style)
    declared = re.search(r'([\d,]+)\s+talents', unescape(section))
    return dict(key=key, name=title_of(page), layout=layout,
                declared_talents=int(declared.group(1).replace(',', '')) if declared else None,
                columns=int(columns.group(1)) if columns else None,
                rows=int(rows.group(1)) if rows else None,
                talents=talents)


def parse_changes(page, key):
    """One daily changelog page: field-level diffs with score and source."""
    main = strip_chrome(page)
    events = []
    for row in re.finditer(r'(?is)<tr class="changelog-row[^"]*"[^>]*>(.*?)</tr>', main):
        cells = row.group(1)
        kind = re.search(r'changelog-row--(\w+)', row.group(0))
        removed = [unescape(v) for v in blocks(cells, 'entity-history__diff-line--removed', 'span')]
        added = [unescape(v) for v in blocks(cells, 'entity-history__diff-line--added', 'span')]
        ref = links(block(cells, 'changelog-row__name', 'td'))
        events.append(dict(change=kind.group(1) if kind else None,
                           field=text_of(cells, 'changelog-row__kind', 'td'),
                           entity=ref[0] if ref else None,
                           entity_label=text_of(cells, 'changelog-row__name', 'td'),
                           removed=[v.lstrip('- ').strip() for v in removed],
                           added=[v.lstrip('+ ').strip() for v in added],
                           significance=text_of(cells, 'changelog-row__sig', 'td'),
                           source=text_of(cells, 'changelog-row__source', 'td')))
    return dict(date=key.split('/')[-1], events=events)


EXTRACTORS = {'spell': parse_spell, 'item': parse_item, 'npc': parse_npc, 'tree': parse_tree}


def parse_page(page_type, key, page, name=None):
    """Parse one mirrored page, always returning its generic structure too.

    `name` is the crawler's own index entry for the page and stays canonical;
    `page_title` is whatever the rendered heading says, kept separately because
    some headings fold a badge or point value into the same element.
    """
    record = dict(type=page_type, key=key)
    tuned = EXTRACTORS.get(page_type)
    if tuned:
        record.update(tuned(page, key))
    elif page_type == 'page' and key.startswith('changes/'):
        record.update(parse_changes(page, key))
        record['type'] = 'change-log'
    else:
        record.update(generic(page))
    if tuned or record['type'] == 'change-log':
        # A tuned extractor knows the fields worth naming; it does not know what
        # a page might carry that nobody has looked at yet -- vendor stock on an
        # NPC, a reward block on a quest, a source table on an item. Keeping the
        # generic structure alongside means an unexamined section is preserved
        # rather than silently discarded.
        record['structure'] = generic(page)
    record['key'] = key
    record['page_title'] = title_of(page)
    record['name'] = name if name is not None else record['page_title']
    return record
