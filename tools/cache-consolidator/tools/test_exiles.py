"""Structural extraction, comparison, publication refusal and repeatability tests."""
from contextlib import closing
import gzip
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import import_exiles as importer
import exiles_parse as parser

CHROME = ('<header class="site-header"><a href="/">db.exil.es</a></header>'
          '<nav class="breadcrumb"><ol><li class="breadcrumb__item">'
          '<a class="breadcrumb__link" href="/spells">Spells</a></li></ol></nav>')
FOOTER = '<footer class="site-footer"><p class="site-footer__line">coa-db</p></footer>'


def page(body):
    return '<!doctype html><html><body>' + CHROME + '<main class="site-main">' + body + '</main>' + FOOTER + '</body></html>'


SPELL = page(
    '<article class="spell-page spell-page--frost">'
    '<header class="spell-page__head">'
    '<img class="spell-page__icon" src="https://i.exil.es/coa/static/icons-clean/spell_frost_frostbolt.png">'
    '<div class="spell-page__head-text"><h1 class="spell-page__name">Frostbolt</h1></div></header>'
    '<table class="spell-info"><tbody>'
    '<tr><th>School</th><td>Frost</td></tr>'
    '<tr><th>Cast time</th><td class="numeric">1.3 sec</td></tr>'
    '<tr><th>Spell ID</th><td class="numeric muted">116</td></tr>'
    '</tbody></table>'
    '<p class="spell-page__desc">Launches a bolt of frost.</p>'
    '<section class="spell-page__section"><h2>Effects</h2><ol class="spell-page__effects">'
    '<li class="effect-row"><span class="effect-row__name">School Damage</span>'
    '<span class="muted"> · Frost</span><span class="effect-row__value numeric">11</span></li>'
    '</ol></section>'
    '<section class="spell-page__section spell-page__cast-by-npcs"><h2>Cast by NPCs</h2>'
    '<ul><li><a href="/npc/12369">Lord Kragaru</a></li></ul></section></article>')

ITEM = page(
    '<div class="item-header"><h1 class="q3">Destiny</h1></div>'
    '<div class="tooltip item-tooltip"><table class="tooltip-item"><tr><td>'
    '<b class="q3">Destiny</b><br><!--bo-->Binds when picked up<br>'
    '<span><!--stat7-->+14 Stamina</span><br>Item Level 57'
    '<!--?647:1:57:52--></td></tr></table></div>'
    '<div class="item-sources"><ul class="item-sources">'
    '<li><a href="/npc/12369">Lord Kragaru</a> <span class="muted">(Lv 38)</span> '
    '<span class="muted">(7.7%)</span></li></ul></div>'
    '<div class="entity-history"><ul class="entity-history__list">'
    '<li class="entity-history__row entity-history__row--changed"><time>2026-05-27</time> — '
    '<span class="entity-history__field">disenchant_id</span> '
    '<code class="changelog-row__value">0</code> → <code class="changelog-row__value">52</code> '
    '<span class="muted">(tc-backfill)</span></li></ul></div>')

NPC = page(
    '<article class="npc-page" data-npc-id="12369">'
    '<div class="npc-page__top"><div class="npc-page__intro">'
    '<header class="npc-page__header"><h1 class="npc-page__name">Lord Kragaru</h1>'
    '<div class="npc-page__levels">Level 38</div></header>'
    '<dl class="npc-page__meta"><dt>Side</dt><dd class="side-neutral">Neutral</dd>'
    '<dt>Type</dt><dd>Humanoid</dd></dl></div>'
    '<img class="npc-page__portrait" src="https://i.exil.es/coa/static/creatures/11257.webp"></div>'
    '<section class="npc-drops"><h2>Drops</h2><table class="npc-drops__table">'
    '<thead><tr><th>Item</th><th>Chance</th></tr></thead>'
    '<tbody><tr><td><a class="q1" href="/item/647">Destiny</a></td><td>100%</td></tr>'
    '</tbody></table></section></article>')

GRID_TREE = page(
    '<section class="talent-tree moa-tree"><header class="talent-tree__head">'
    '<h2>Tinker — Mechanics</h2><span class="muted">2 talents</span></header>'
    '<div class="talent-grid talent-grid--moa" style="grid-template-columns: repeat(10, 56px); '
    'grid-template-rows: repeat(7, 56px);">'
    '<a class="talent-cell talent-cell--filled" href="/spell/504527" style="grid-row: 1; grid-column: 5;" '
    'title="Makeshift Dynamite"><img src="https://i.exil.es/coa/static/icons-clean/custom_engineerskill_49.png">'
    '<span class="talent-cell__rank">0/1</span></a>'
    '<a class="talent-cell talent-cell--filled" href="/spell/520453" style="grid-row: 2; grid-column: 7;" '
    'title="Slam the Cogs!"><img src="https://i.exil.es/coa/static/icons-clean/inv_engineering_autohammer.png">'
    '<span class="talent-cell__rank">0/2</span></a></div></section>')

FLAT_TREE = page(
    '<section class="talent-tree moa-tree"><header class="talent-tree__head">'
    '<h2>Death Knight — Blood</h2><span class="muted">2 talents</span></header>'
    '<div class="moa-tree-flat">'
    '<a class="talent-cell talent-cell--filled" href="/spell/50365" title="Improved Blood Presence">'
    '<img src="https://i.exil.es/coa/static/icons-clean/spell_deathknight_bloodpresence.png">'
    '<span class="talent-cell__rank">0/2</span></a>'
    '<a class="talent-cell talent-cell--filled" href="/spell/48978" title="Bladed Armor">'
    '<img src="https://i.exil.es/coa/static/icons-clean/inv_shoulder_36.png">'
    '<span class="talent-cell__rank">0/5</span></a></div></section>')

CHANGES = page(
    '<table><tbody><tr class="changelog-row changelog-row--changed">'
    '<td class="changelog-row__kind">reward_items</td>'
    '<td class="changelog-row__name"><a href="/quest/656">Summoning the Princess '
    '<span class="muted">#656</span></a></td>'
    '<td class="changelog-row__diff"><pre><code>'
    '<span class="entity-history__diff-line entity-history__diff-line--removed">'
    '<span class="entity-history__diff-prefix">-</span>    &quot;count&quot;: 300,</span>'
    '<span class="entity-history__diff-line entity-history__diff-line--added">'
    '<span class="entity-history__diff-prefix">+</span>    &quot;count&quot;: 65,</span>'
    '</code></pre></td>'
    '<td class="changelog-row__sig num muted">7</td>'
    '<td class="changelog-row__source muted">wdb</td></tr></tbody></table>')

HISTORY = page('<div class="entity-history"><ul class="entity-history__list">'
               '<li class="entity-history__row">Older change</li></ul></div>')

PLACEHOLDER_ITEM = page('<div class="item-header"><h1 class="q1">Item #999</h1></div>'
                        '<div class="tooltip item-tooltip"><table class="tooltip-item">'
                        '<tr><td>Item #999</td></tr></table></div>')

CONFLICT_ITEM = page('<div class="item-header"><h1 class="q1">Renamed Blade</h1></div>'
                     '<div class="tooltip item-tooltip"><table class="tooltip-item">'
                     '<tr><td>Renamed Blade</td></tr></table></div>')

MISSING_ITEM = page('<div class="item-header"><h1 class="q1">Uncaptured Relic</h1></div>'
                    '<div class="tooltip item-tooltip"><table class="tooltip-item">'
                    '<tr><td>Uncaptured Relic</td></tr></table></div>')

LISTING = page('<section class="listing"><h1>Spells</h1><table class="listing__table">'
               '<thead><tr><th>Name</th><th>School</th></tr></thead>'
               '<tbody><tr><td><a href="/spell/116">Frostbolt</a></td><td>Frost</td></tr>'
               '</tbody></table></section>')

QUEST = page(
    '<article class="quest-page" data-quest-id="656">'
    '<h1>Summoning the Princess</h1>'
    '<dl class="quest-page__meta"><dt>Level</dt><dd>50</dd></dl>'
    '<div class="quest-text"><p>My Shredder is out of fuel!</p></div>'
    '<div class="quest-text quest-text--objectives"><h2>Objectives</h2>'
    '<ul class="quest-objectives"><li>Collect eight barrels</li></ul></div>'
    '<section class="quest-rewards"><h2>Rewards</h2>'
    '<div class="quest-rewards__money">Money: <span class="money-gold">29g</span></div>'
    '<div class="quest-rewards__xp">Experience: 33,100 XP</div>'
    '<div class="quest-rewards__talents">Talent points: +100</div>'
    '<div class="quest-rewards__rep"><h3>Reputation</h3><ul class="quest-rewards__list">'
    '<li><a href="/faction/529">Argent Dawn</a> +100</li></ul></div>'
    '</section></article>')

PAGES = [
    ('/spell/116', 'spell', '116', 'spell/116.html', SPELL, 'Frostbolt'),
    ('/item/647', 'item', '647', 'item/647.html', ITEM, 'Destiny'),
    ('/item/999', 'item', '999', 'item/999.html', PLACEHOLDER_ITEM, 'Item #999'),
    ('/item/700', 'item', '700', 'item/700.html', CONFLICT_ITEM, 'Renamed Blade'),
    ('/item/800', 'item', '800', 'item/800.html', MISSING_ITEM, 'Uncaptured Relic'),
    ('/item/647/history', 'item', '647/history', 'item/647/history.html', HISTORY, 'Destiny #647 — history'),
    ('/npc/12369', 'npc', '12369', 'npc/12369.html', NPC, 'Lord Kragaru'),
    ('/quest/656', 'quest', '656', 'quest/656.html', QUEST, 'Summoning the Princess'),
    ('/tree/tinker-mechanics', 'tree', 'tinker-mechanics', 'tree/tinker-mechanics.html', GRID_TREE, 'Tinker — Mechanics'),
    ('/tree/death-knight-blood', 'tree', 'death-knight-blood', 'tree/death-knight-blood.html', FLAT_TREE, 'Death Knight — Blood'),
    ('/changes/2026-08-29', 'page', 'changes/2026-08-29', 'changes/2026-08-29.html', CHANGES, 'Changes on 2026-08-29'),
    # Paginated listings: distinct routes sharing one page_key. The real mirror
    # has 7,849 listing routes across a couple of dozen keys.
    ('/spells', 'listing', 'spells', 'spells.html', LISTING, 'Spells'),
    ('/spells?page=2', 'listing', 'spells', 'spells__page_2.html', LISTING, 'Spells'),
    ('/spells?page=3', 'listing', 'spells', 'spells__page_3.html', LISTING, 'Spells'),
]


class ExilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.mirror = self.root / 'ExilesOfflineDB'
        self.out = self.root / 'snapshot'
        self.cache = self.root / 'union'
        self.cache.mkdir()
        # 647 agrees, 700 disagrees, 999 is captured but unnamed upstream, 800 is absent.
        self.cache.joinpath('itemcache.tsv.gz').write_bytes(gzip.compress(
            b'entry\tname\n647\tDestiny\n700\tAncient Blade\n999\tCaptured Name\n', mtime=0))
        self.cache.joinpath('creaturecache.tsv.gz').write_bytes(gzip.compress(
            b'entry\tname\n12369\tLord Kragaru\n', mtime=0))
        # questcache holds the name in `Title`, not `name`, and not in the second
        # column. A positional guess would compare against `Method` and call every
        # quest a conflict, so the layout is reproduced here deliberately.
        self.write_quest_baseline(b'entry\tMethod\tQuestLevel\tTitle\n'
                                  b'656\t2\t50\tSummoning the Princess\n')
        self.archive = self.root / 'archive.7z.001'
        self.archive.write_bytes(b'archive bytes')
        self.digest = importer.sha(self.archive.read_bytes())
        self.write_mirror()

    def tearDown(self):
        self.tmp.cleanup()

    def write_quest_baseline(self, content):
        self.cache.joinpath('questcache.tsv.gz').write_bytes(gzip.compress(content, mtime=0))

    def write_mirror(self, taint='', taint_name=None):
        base = self.mirror / 'mirror' / 'db.exil.es'
        for _, _, _, rel, body, _ in PAGES:
            target = base / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            # Inject into rendered copy that is actually published, not into
            # markup the extractors discard -- screening only guards what ships.
            target.write_text(body.replace('Launches a bolt of frost.',
                                           'Launches a bolt of frost. ' + taint),
                              encoding='utf-8')
        self.taint_name = taint_name
        (base / 'api').mkdir(parents=True, exist_ok=True)
        # The real specification carries a contact address and an AGPL declaration.
        (base / 'api' / 'openapi.json').write_text(
            '{"openapi":"3.1.0","info":{"title":"coa-db API","contact":'
            '{"name":"Sub-Net e.U.","email":"someone@example.at"},'
            '"license":{"name":"AGPL-3.0-or-later"},"version":"0.4.0"}}', encoding='utf-8')
        data = self.mirror / 'data'
        data.mkdir(parents=True, exist_ok=True)
        self.site = data / 'offline_site.sqlite'
        if self.site.exists():
            self.site.unlink()
        with closing(sqlite3.connect(self.site)) as db, db:
            db.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            db.execute('CREATE TABLE route(route_key TEXT PRIMARY KEY,url TEXT,local_path TEXT,'
                       'content_type TEXT,bytes INTEGER,page_type TEXT,page_key TEXT)')
            db.execute('CREATE TABLE search_index(id INTEGER PRIMARY KEY,name TEXT,name_lower TEXT,'
                       'page_type TEXT,page_key TEXT,url TEXT,route_key TEXT)')
            db.execute("INSERT INTO meta VALUES ('built_at','1788061868.0')")
            for index, (route, kind, key, rel, _, name) in enumerate(PAGES, 1):
                local = 'mirror\\db.exil.es\\' + rel.replace('/', '\\')
                if getattr(self, 'taint_name', None) and kind == 'npc':
                    name = self.taint_name
                db.execute('INSERT INTO route VALUES (?,?,?,?,?,?,?)',
                           (route, 'https://db.exil.es' + route, local, 'text/html', 0, kind, key))
                db.execute('INSERT INTO search_index VALUES (?,?,?,?,?,?,?)',
                           (index, name, name.lower(), kind, key, 'https://db.exil.es' + route, route))

    def build(self, **changes):
        options = dict(mirror=self.mirror, site_db=self.site, output=self.out,
                       expected_sha256=self.digest, archive=self.archive,
                       cache_dir=self.cache, baseline_revision='0' * 40)
        options.update(changes)
        return importer.ingest(**options)

    def records(self, name):
        return [json.loads(line) for line in
                gzip.decompress((self.out / name).read_bytes()).splitlines()]

    # -- extraction ------------------------------------------------------
    def test_spell_fields_and_references(self):
        record = parser.parse_page('spell', '116', SPELL, name='Frostbolt')
        self.assertEqual(record['info']['School'], 'Frost')
        self.assertEqual(record['info']['Cast time'], '1.3 sec')
        self.assertEqual(record['school_class'], 'frost')
        self.assertEqual(record['icon'], 'spell_frost_frostbolt')
        self.assertEqual(record['effects'], [dict(name='School Damage', detail='Frost', value='11')])
        self.assertEqual(record['cast_by_npcs'],
                         [dict(type='npc', key='12369', label='Lord Kragaru')])

    def test_tooltip_markers_are_preserved_uninterpreted(self):
        record = parser.parse_page('item', '647', ITEM, name='Destiny')
        markers = [code for line in record['tooltip'] for code in line['markers']]
        self.assertIn('stat7', markers)
        self.assertIn('?647:1:57:52', markers)
        self.assertNotIn('Stamina', markers)

    def test_item_sources_and_history(self):
        record = parser.parse_page('item', '647', ITEM, name='Destiny')
        self.assertEqual(record['sources'][0]['entity']['key'], '12369')
        self.assertEqual(record['sources'][0]['notes'], ['(Lv 38)', '(7.7%)'])
        self.assertEqual(record['history'][0],
                         dict(date='2026-05-27', field='disenchant_id', old='0', new='52',
                              source='tc-backfill'))

    def test_npc_drops_carry_entity_and_chance(self):
        record = parser.parse_page('npc', '12369', NPC, name='Lord Kragaru')
        self.assertEqual(record['display_id'], '11257')
        self.assertEqual(record['meta']['Side'], 'Neutral')
        self.assertEqual(record['drops'][0]['entity']['key'], '647')
        self.assertEqual(record['drops'][0]['chance'], '100%')

    def test_both_tree_layouts_parse(self):
        grid = parser.parse_page('tree', 'tinker-mechanics', GRID_TREE)
        flat = parser.parse_page('tree', 'death-knight-blood', FLAT_TREE)
        self.assertEqual(grid['layout'], 'grid')
        self.assertEqual((grid['columns'], grid['rows']), (10, 7))
        self.assertEqual(grid['talents'][0]['row'], 1)
        self.assertEqual(grid['talents'][0]['column'], 5)
        self.assertEqual(grid['talents'][1]['max_rank'], 2)
        self.assertEqual(flat['layout'], 'flat')
        self.assertIsNone(flat['talents'][0]['row'])
        self.assertEqual(flat['talents'][1]['spell_id'], 48978)
        for tree in (grid, flat):
            self.assertEqual(len(tree['talents']), tree['declared_talents'])

    def test_changelog_keeps_old_and_new_values(self):
        record = parser.parse_page('page', 'changes/2026-08-29', CHANGES)
        self.assertEqual(record['type'], 'change-log')
        event = record['events'][0]
        self.assertEqual(event['field'], 'reward_items')
        self.assertEqual(event['entity'], dict(type='quest', key='656',
                                               label='Summoning the Princess #656'))
        self.assertEqual(event['removed'], ['"count": 300,'])
        self.assertEqual(event['added'], ['"count": 65,'])
        self.assertEqual((event['significance'], event['source']), ('7', 'wdb'))

    def test_quest_rewards_survive_as_leaf_fields(self):
        record = parser.parse_page('quest', '656', QUEST, name='Summoning the Princess')
        fields = {f['field']: f['text'] for f in record['fields']}
        self.assertEqual(fields['quest-rewards__xp'], 'Experience: 33,100 XP')
        self.assertEqual(fields['quest-rewards__talents'], 'Talent points: +100')
        self.assertEqual(fields['quest-rewards__money'], 'Money: 29g')
        self.assertIn('My Shredder is out of fuel!', record['paragraphs'])
        self.assertIn('Collect eight barrels', record['list_items'])
        self.assertIn(dict(type='faction', key='529', label='Argent Dawn'), record['references'])

    def test_tuned_records_keep_the_generic_structure_too(self):
        for kind, key, body in (('spell', '116', SPELL), ('item', '647', ITEM),
                                ('npc', '12369', NPC), ('tree', 'x', GRID_TREE)):
            record = parser.parse_page(kind, key, body)
            self.assertIn('structure', record, kind)
            self.assertIn('headings', record['structure'], kind)

    def test_page_title_and_indexed_name_are_kept_apart(self):
        record = parser.parse_page('achievement', '484',
                                   page('<h1>Gundrak <span>(10)</span></h1>'), name='Gundrak')
        self.assertEqual(record['name'], 'Gundrak')
        self.assertEqual(record['page_title'], 'Gundrak (10)')

    # -- catalog ---------------------------------------------------------
    def test_import_then_verify(self):
        manifest = self.build()
        self.assertEqual(manifest['counts']['parsed_pages'], len(PAGES))
        self.assertEqual(manifest['counts']['trees'], 2)
        self.assertEqual(manifest['counts']['talents'], 4)
        self.assertEqual(manifest['counts']['missing_files'], 0)
        self.assertEqual(importer.verify(self.out)['source']['archive_sha256'], self.digest)

    def test_history_pages_are_streamed_separately(self):
        self.build()
        self.assertEqual([r['key'] for r in self.records('histories.jsonl.gz')], ['647/history'])
        self.assertNotIn('647/history', [r['key'] for r in self.records('items.jsonl.gz')])

    def test_records_name_the_mirrored_file_they_came_from(self):
        self.build()
        index = dict(line.split('\t')[:2] for line in
                     gzip.decompress((self.out / 'mirror.index.tsv.gz').read_bytes())
                     .decode().splitlines()[1:])
        for record in self.records('spells.jsonl.gz'):
            self.assertIn(record['source_path'], index)

    def test_comparison_separates_conflicts_missing_and_unnamed(self):
        self.build()
        report = json.loads(gzip.decompress((self.out / 'comparison.json.gz').read_bytes()))
        self.assertEqual([r['numeric_id'] for r in report['name_conflicts']], [700])
        self.assertEqual([r['numeric_id'] for r in report['candidate_missing']], [800])
        self.assertEqual([r['numeric_id'] for r in report['unnamed_in_mirror']], [999])
        self.assertEqual(report['baseline_revision'], '0' * 40)

    def test_routes_sharing_a_page_key_all_survive(self):
        manifest = self.build()
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / 'catalog.sqlite'
            db_path.write_bytes(gzip.decompress((self.out / 'catalog.sqlite.gz').read_bytes()))
            with closing(sqlite3.connect(db_path)) as db:
                rows = db.execute(
                    "SELECT route_key FROM entity WHERE page_type='listing' AND page_key='spells'"
                ).fetchall()
        self.assertEqual(sorted(r[0] for r in rows),
                         ['/spells', '/spells?page=2', '/spells?page=3'])
        self.assertEqual(manifest['counts']['entities'], len(PAGES) - 1)  # the history page is excluded

    def test_quest_baseline_is_read_from_its_declared_name_column(self):
        self.build()
        report = json.loads(gzip.decompress((self.out / 'comparison.json.gz').read_bytes()))
        self.assertEqual([r for r in report['name_conflicts'] if r['page_type'] == 'quest'], [])
        self.assertEqual(report['compared']['quest'], 1)

    def test_baseline_without_its_declared_column_is_refused(self):
        self.write_quest_baseline(b'entry\tMethod\tQuestLevel\n656\t2\t50\n')
        with self.assertRaises(ValueError):
            self.build()

    def test_trailing_id_disambiguator_is_not_a_conflict(self):
        self.assertEqual(importer.display_name('Ice Chest #188192'), 'Ice Chest')
        self.assertTrue(importer.is_placeholder('Item #999'))
        self.assertFalse(importer.is_placeholder('Ice Chest #188192'))

    def test_rebuild_is_byte_identical(self):
        first = self.build()
        second = self.build(output=self.root / 'again')
        self.assertEqual(first['artifacts'], second['artifacts'])

    # -- refusals --------------------------------------------------------
    def test_wrong_archive_hash_is_refused(self):
        with self.assertRaises(ValueError):
            self.build(expected_sha256='0' * 64)

    def test_tampered_artifact_is_refused(self):
        self.build()
        target = self.out / 'talents.jsonl.gz'
        target.write_bytes(target.read_bytes() + b'\x00')
        with self.assertRaises(ValueError):
            importer.verify(self.out)

    def test_added_file_is_refused(self):
        self.build()
        (self.out / 'stray.txt').write_text('stray', encoding='utf-8')
        with self.assertRaises(ValueError):
            importer.verify(self.out)

    def test_in_game_fictional_address_is_allowed_but_only_that_one(self):
        importer.screen('appeal to techbot@gnome.mail for review')
        with self.assertRaises(ValueError):
            importer.screen('appeal to someone.else@gnome.mail for review')
        with self.assertRaises(ValueError):
            importer.screen('appeal to techbot@gnome.email for review')

    def test_personal_data_in_a_published_field_is_refused(self):
        self.write_mirror(taint='Contact player@example.com')
        self.build()
        with self.assertRaises(ValueError):
            importer.verify(self.out)

    def test_personal_data_in_an_indexed_name_is_refused(self):
        self.write_mirror(taint_name='Lord Kragaru C:\\Users\\someone\\WoW')
        self.build()
        with self.assertRaises(ValueError):
            importer.verify(self.out)

    def test_api_specification_is_published_with_its_address_redacted(self):
        manifest = self.build()
        published = gzip.decompress((self.out / 'openapi.json.gz').read_bytes()).decode()
        self.assertNotIn('someone@example.at', published)
        self.assertIn('<redacted: contact address>', published)
        self.assertIn('Sub-Net e.U.', published)
        spec = manifest['source']['api_specification']
        self.assertEqual(spec['redacted_addresses'], 1)
        self.assertEqual(spec['declared_license'], 'AGPL-3.0-or-later')
        original = (self.mirror / 'mirror' / 'db.exil.es' / 'api' / 'openapi.json').read_bytes()
        self.assertEqual(spec['sha256'], importer.sha(original))
        importer.verify(self.out)

    def test_every_artifact_is_screened_not_a_chosen_few(self):
        self.build()
        target = self.out / 'openapi.json.gz'
        leaked = gzip.decompress(target.read_bytes()).replace(
            b'<redacted: contact address>', b'someone@example.at')
        target.write_bytes(gzip.compress(leaked, 9, mtime=0))
        manifest = json.loads((self.out / 'manifest.json').read_text(encoding='utf-8'))
        manifest['artifacts']['openapi.json.gz'] = dict(
            bytes=target.stat().st_size, sha256=importer.sha(target.read_bytes()))
        (self.out / 'manifest.json').write_bytes(importer.encode(manifest) + b'\n')
        with self.assertRaises(ValueError):
            importer.verify(self.out)

    def test_missing_api_specification_is_refused(self):
        (self.mirror / 'mirror' / 'db.exil.es' / 'api' / 'openapi.json').unlink()
        with self.assertRaises(ValueError):
            self.build()


if __name__ == '__main__':
    unittest.main()
