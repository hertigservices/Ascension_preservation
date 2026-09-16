const {test} = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const vm = require('node:vm');
const fs = require('node:fs');
const web = path.join(__dirname, '../ascension-db/web');
for (const name of ['search-aliases', 'search-query', 'quest-sql', 'quest-server-sql', 'quest-export']) require(path.join(web, name + '.js'));
const sql = AscensionQuestSQL;
const record = (raw, mode = 'conquest-of-azeroth') => ['1', "A 'quest' \\ 雪", 'quest', mode, 'Client captures', raw];
test('reward quantities do not clamp, wrap, round or become scientific notation', () => {
  const r = sql.convert(record({RewOrReqMoney: '-200', RewardItem1: '375250', RewardAmount1: '100000', RewardChoiceItemId1: '1', RewardChoiceItemCount1: '4294967295', RewTalents: '100', RewHonorMultiplier: '0.5'}), {key: 'a/0/0'});
  assert.equal(r.rewards.RewardAmount1, '100000'); assert.equal(r.rewards.RewardMoney, '-200');
  assert.equal(r.items[1].quantity, '4294967295'); assert.equal(r.rewards.RewardKillHonor, '0.5');
  assert.equal(r.rewards.RewardTalents, '100'); assert.equal(r.rewards.RewardArenaPoints, null);
  assert.match(sql.sql(r), /100000/); assert.equal(r.issues.length, 0);
  for (const value of ['-1', '4294967296', '1.5', '1e2', '+1', '01', ' 1', 'NaN', Infinity, 9007199254740992]) {
    const bad = sql.convert(record({RewardItem1: '1', RewardAmount1: value}));
    assert.equal(bad.rewards.RewardAmount1, null); assert.ok(bad.issues.length);
    assert.equal(bad.raw.RewardAmount1, value);
  }
});
test('SQL strings preserve arbitrary text without executable source fragments', () => {
  const raw = {RewardItem1: '1', RewardAmount1: '1', Details: "'; DROP TABLE x;--\n\\\0雪😀$B"};
  const r = sql.convert(record(raw), {key: 'a/0/0'}), output = sql.sql(r);
  assert.equal(output.includes('DROP TABLE'), false);
  assert.equal(Buffer.from(sql.text(raw.Details).match(/X'([a-f0-9]*)'/)[1], 'hex').toString(), raw.Details);
  assert.throws(() => sql.sql({...r, key: "bad'"}), /key/);
});
test('factions, spells, honor, arena and fixed/choice slots stay distinct', () => {
  const r = sql.convert(record({RewOrReqMoney: '0', RewSpell: '11', RewSpellCast: '22', RewHonor: '33', RewArenaPoints: '44', RewRepMask: '15', RewardFactionId1: '529', RewardFactionValueId1: '-5', RewardFactionValueIdOverride1: '-10000', RewardItem1: '123', RewardAmount1: '4', RewardChoiceItemId1: '123', RewardChoiceItemCount1: '8'}));
  assert.equal(r.rewards.RewardDisplaySpell, '11'); assert.equal(r.rewards.RewardSpell, '22');
  assert.equal(r.rewards.RewardFactionValue1, '-5'); assert.equal(r.rewards.RewardFactionOverride1, '-10000');
  assert.deepEqual(r.items.map(i => [i.kind, i.quantity]), [['fixed', '4'], ['choice', '8']]);
});
test('unknown records are preserved without manufacturing zero rewards', () => {
  const raw = {record: {title: 'Website quest', fields: {'Reward': 'Mystery currency'}}};
  const r = sql.convert(record(raw)); assert.equal(r.adapter, 'evidence-only');
  assert.ok(Object.values(r.rewards).every(v => v === null)); assert.equal(r.raw, raw); assert.equal(r.issues.length, 1);
});
test('structured Exiles arrays convert but ambiguous spell labels do not', () => {
  const r = sql.convert(record({reward_money: '-50', reward_items: '[{"id":375250,"count":100000}]', reward_choice_items: '[]', reward_factions: '[{"faction":529,"value_idx":-5,"value_override":10000}]', reward_spell_id: '52382', reward_spell_cast_id: '48778'}));
  assert.equal(r.adapter, 'structured-exiles-quest'); assert.equal(r.items[0].quantity, '100000');
  assert.equal(r.rewards.RewardAmount2, '0'); assert.equal(r.rewards.RewardSpell, null);
  assert.equal(r.rewards.RewardFactionOverride1, '10000'); assert.ok(r.issues.some(i => i.reason.includes('semantics')));
});
function fixture() {
  const index = [], details = [], assets = new Map();
  for (let i = 0; i < 135; i++) {
    const r = [String(i), 'Quest ' + i, 'quest', i % 2 ? 'free-pick' : 'conquest-of-azeroth', i % 3 ? 'Client captures' : 'Exiles DB', {RewOrReqMoney: '0', RewardItem1: '375250', RewardAmount1: String(i + 1)}];
    details.push(r); index.push([`a/0/${i}`, r[1], r[2], r[3], r[4], r[0]]);
  }
  assets.set('browse', index); assets.set('records/a/0.json.gz', details);
  return {assets, request: {kind: 'quest', dataBase: 'https://example.test/', manifest: {revision: 'abc', files: {a: {path: 'public/quests', blob: 'sha256:test'}}, search: {'browse:quest': {parts: ['browse'], count: 135}, 'id:10': {parts: ['browse'], count: 135}}}}};
}
test('export respects mode/source filters across every page and records its exact scope', async () => {
  const {assets, request} = fixture(), chunks = [];
  request.mode = 'conquest-of-azeroth'; request.source = 'Client captures'; request.page = 99;
  const result = await AscensionQuestExport.run(request, {load: async p => assets.get(p), write: async s => chunks.push(s)});
  assert.equal(result.records, 45); assert.equal(result.item_rewards, 45);
  assert.equal(chunks.filter(c => c.startsWith('INSERT INTO ascension_quest_export VALUES')).length, 45);
  const all = await AscensionQuestExport.run({...request, mode: '', source: ''}, {load: async p => assets.get(p), write: async () => {}});
  assert.equal(all.records, 135); // Not the visible 50-record page.
  const exact = await AscensionQuestExport.run({...request, recordId: '100'}, {load: async p => assets.get(p), write: async () => {}});
  assert.equal(exact.records, 1);
});
test('unsupported Zone constraints, empty matches, cancellation and corrupt details never finish an export', async () => {
  const {assets, request} = fixture(), run = d => AscensionQuestExport.run(d, {load: async p => assets.get(p), write: async () => {}});
  await assert.rejects(run({...request, zone: '12'}), /Zone/);
  await assert.rejects(run({...request, mode: 'absent'}), /No quests/);
  const controller = new AbortController(); controller.abort();
  await assert.rejects(AscensionQuestExport.run(request, {load: async p => assets.get(p), write: async () => {}, signal: controller.signal}), /cancelled/);
  assets.get('records/a/0.json.gz')[0][0] = 'wrong'; await assert.rejects(run(request), /mismatch/);
});
test('the quest button precedes clear filters and is absent in other collections', async () => {
  const {searchTable} = await import(path.join(web, 'search-controls.js'));
  const m = {kinds: {quest: {label: 'Quests'}}, sources: {}, modes: []};
  const h = searchTable([], new URLSearchParams('kind=quest'), m);
  assert.ok(h.indexOf('data-export-quests') < h.indexOf('data-clear-columns'));
  assert.equal(searchTable([], new URLSearchParams('kind=item'), m).includes('data-export-quests'), false);
});
