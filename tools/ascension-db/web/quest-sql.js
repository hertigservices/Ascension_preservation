/* Shared browser/Node converter. Source records remain separate; no realm writes. */
(() => {
  'use strict';
  const VERSION = 'ascension-quest-sql-1';
  const scalar = {
    RewXPId: 'RewardXPDifficulty', RewOrReqMoney: 'RewardMoney',
    RewMoneyMaxLevel: 'RewardMoneyDifficulty', RewSpell: 'RewardDisplaySpell',
    RewSpellCast: 'RewardSpell', RewHonor: 'RewardHonor',
    RewHonorMultiplier: 'RewardKillHonor', RewTitleId: 'RewardTitle',
    RewTalents: 'RewardTalents', RewArenaPoints: 'RewardArenaPoints',
    RewRepMask: 'RewardReputationMask',
  };
  const fields = [...Object.values(scalar)];
  for (let i = 1; i <= 4; i++) fields.push(`RewardItem${i}`, `RewardAmount${i}`);
  for (let i = 1; i <= 6; i++) fields.push(`RewardChoiceItemID${i}`, `RewardChoiceItemQuantity${i}`);
  for (let i = 1; i <= 5; i++) fields.push(`RewardFactionID${i}`, `RewardFactionValue${i}`, `RewardFactionOverride${i}`);
  const signed = new Set(['RewardMoney', 'RewardSpell', 'RewardHonor', ...fields.filter(f => /RewardFaction(Value|Override)/.test(f))]);
  const canonical = value => JSON.stringify(value);
  // Hex strings preserve apostrophes, backslashes, NUL, control bytes and Unicode
  // independently of the receiving client's SQL mode. Numeric values never use Number.
  const encoder = new TextEncoder();
  const hex = Array.from({length: 256}, (_, i) => i.toString(16).padStart(2, '0'));
  function text(value) {
    if (value === null || value === undefined) return 'NULL';
    return "CONVERT(X'" + Array.from(encoder.encode(String(value)), b => hex[b]).join('') + "' USING utf8mb4)";
  }
  function integer(raw, isSigned = false) {
    if (typeof raw === 'number' && !Number.isSafeInteger(raw)) throw Error('Unsafe or fractional source number');
    const s = String(raw);
    if (!/^(0|-?[1-9][0-9]*)$/.test(s)) throw Error('Noncanonical integer');
    const n = BigInt(s), lo = isSigned ? -2147483648n : 0n, hi = isSigned ? 2147483647n : 4294967295n;
    if (n < lo || n > hi) throw Error('Outside captured 32-bit range');
    return s;
  }
  function decimal(raw) {
    const s = String(raw);
    if (!/^(0|[1-9][0-9]{0,20})(\.[0-9]{1,9})?$/.test(s)) throw Error('Unsupported finite decimal; raw value retained');
    return s;
  }
  function convert(record, metadata = {}) {
    if (!Array.isArray(record) || record[2] !== 'quest' || !record[5] || typeof record[5] !== 'object') throw Error('Expected a complete quest source record');
    const [id, title, , mode, source, raw, attribution = null] = record;
    const data = raw.record && typeof raw.record === 'object' ? raw.record : raw;
    const rewards = Object.fromEntries(fields.map(f => [f, null]));
    const issues = [], items = [];
    let adapter = 'evidence-only';
    const put = (field, value) => {
      if (value === null || value === undefined || value === '') return;
      try { rewards[field] = field === 'RewardKillHonor' ? decimal(value) : integer(value, signed.has(field)); }
      catch (e) { issues.push({field, value, reason: e.message}); }
    };
    if (Object.hasOwn(data, 'RewOrReqMoney') || Object.hasOwn(data, 'RewardItem1')) {
      adapter = 'client-quest-cache';
      for (const [from, to] of Object.entries(scalar)) put(to, data[from]);
      for (let i = 1; i <= 4; i++) { put(`RewardItem${i}`, data[`RewardItem${i}`]); put(`RewardAmount${i}`, data[`RewardAmount${i}`]); }
      for (let i = 1; i <= 6; i++) { put(`RewardChoiceItemID${i}`, data[`RewardChoiceItemId${i}`]); put(`RewardChoiceItemQuantity${i}`, data[`RewardChoiceItemCount${i}`]); }
      for (let i = 1; i <= 5; i++) {
        put(`RewardFactionID${i}`, data[`RewardFactionId${i}`]);
        put(`RewardFactionValue${i}`, data[`RewardFactionValueId${i}`]);
        put(`RewardFactionOverride${i}`, data[`RewardFactionValueIdOverride${i}`]);
      }
    } else if (Object.hasOwn(data, 'reward_items') && Object.hasOwn(data, 'reward_money')) {
      adapter = 'structured-exiles-quest';
      const mapping = {reward_xp_difficulty: 'RewardXPDifficulty', reward_money: 'RewardMoney',
        reward_money_max_level: 'RewardMoneyDifficulty', reward_honor: 'RewardHonor',
        reward_arena_points: 'RewardArenaPoints', reward_title_id: 'RewardTitle', reward_talents: 'RewardTalents'};
      for (const [from, to] of Object.entries(mapping)) put(to, data[from]);
      for (const [from, kind, limit] of [['reward_items', 'fixed', 4], ['reward_choice_items', 'choice', 6], ['reward_factions', 'faction', 5]]) {
        try {
          const entries = typeof data[from] === 'string' ? JSON.parse(data[from]) : data[from];
          if (!Array.isArray(entries)) throw Error('Expected reward array');
          if (entries.length > limit) issues.push({field: from, reason: 'Exceeds target slot count; every original entry retained in raw JSON'});
          for (let n = 0; n < Math.min(entries.length, limit); n++) {
            const e = entries[n], i = n + 1;
            if (!e || typeof e !== 'object') throw Error('Expected reward object');
            if (kind === 'faction') {
              put(`RewardFactionID${i}`, e.faction); put(`RewardFactionValue${i}`, e.value_idx); put(`RewardFactionOverride${i}`, e.value_override);
            } else {
              put(kind === 'fixed' ? `RewardItem${i}` : `RewardChoiceItemID${i}`, e.id);
              put(kind === 'fixed' ? `RewardAmount${i}` : `RewardChoiceItemQuantity${i}`, e.count);
            }
          }
          // Empty/unused slots are explicitly empty in the source array.
          for (let i = entries.length + 1; i <= limit; i++) {
            for (const field of kind === 'faction' ? [`RewardFactionID${i}`, `RewardFactionValue${i}`, `RewardFactionOverride${i}`] : kind === 'fixed' ? [`RewardItem${i}`, `RewardAmount${i}`] : [`RewardChoiceItemID${i}`, `RewardChoiceItemQuantity${i}`]) put(field, '0');
          }
        } catch (e) { issues.push({field: from, reason: e.message}); }
      }
      // The export's two spell labels disagree with the cache convention in observed
      // records. Preserve the originals; do not silently reverse their semantics.
      if (data.reward_spell_id || data.reward_spell_cast_id) issues.push({field: 'reward_spell_id/reward_spell_cast_id', reason: 'Spell display/cast semantics unresolved; preserved in raw JSON'});
    } else {
      issues.push({field: '*', reason: 'No supported structured reward fields; full source record retained'});
    }
    for (const [kind, count] of [['fixed', 4], ['choice', 6]]) for (let slot = 1; slot <= count; slot++) {
      const idField = kind === 'fixed' ? `RewardItem${slot}` : `RewardChoiceItemID${slot}`;
      const amountField = kind === 'fixed' ? `RewardAmount${slot}` : `RewardChoiceItemQuantity${slot}`;
      const item = rewards[idField], quantity = rewards[amountField];
      if ((item === '0' && quantity !== null && quantity !== '0') || (item !== null && item !== '0' && (quantity === '0' || quantity === null))) {
        issues.push({field: idField, reason: 'Incomplete or inconsistent item/quantity pair; retained without repair'});
      }
      if (item !== null && item !== '0') items.push({kind, slot, id: item, quantity});
    }
    return {id: String(id), title: String(title), mode: String(mode), source: String(source),
      locale: data._locales || metadata.path?.match(/\/by-locale\/([^/]+)\//)?.[1] || 'unknown',
      raw, attribution, rewards, issues, items, adapter, ...metadata};
  }
  function header(metadata) {
    return `-- Ascension quest and reward SQL export (${VERSION})\n-- MySQL 8.4 (validated dialect). Import into a NEW EMPTY database or a disposable world COPY.\n-- Source records are not unique quests. Modes, locales and disagreements stay separate.\n-- Item currencies retain item IDs and quantities; custom server behavior is not inferred.\n-- Missing/unmapped values are NULL, not zero. Raw fields and issues remain available.\n-- Loading only stages data. The included server importer requires an explicit CALL.\nSET NAMES utf8mb4;\nSET SESSION sql_mode='STRICT_ALL_TABLES,NO_ENGINE_SUBSTITUTION';\nCREATE TABLE ascension_quest_export_metadata (id INT PRIMARY KEY, metadata_json LONGTEXT NOT NULL) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\nCREATE TABLE ascension_quest_export (record_key VARCHAR(160) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY, quest_id VARCHAR(64) NOT NULL, title TEXT NOT NULL, source TEXT NOT NULL, game_mode TEXT NOT NULL, locale TEXT NOT NULL, source_path TEXT NOT NULL, source_identity TEXT NOT NULL, adapter VARCHAR(40) NOT NULL, raw_json LONGTEXT NOT NULL, attribution_json LONGTEXT NULL, INDEX quest_id (quest_id), INDEX source_scope (source_path(190), game_mode(80), quest_id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\nCREATE TABLE ascension_quest_rewards (record_key VARCHAR(160) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,\n${fields.map(f => '`' + f + '` ' + (f === 'RewardKillHonor' ? 'DECIMAL(30,9)' : 'BIGINT' + (signed.has(f) ? '' : ' UNSIGNED')) + ' NULL').join(',\n')}) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\nCREATE TABLE ascension_quest_reward_items (record_key VARCHAR(160) CHARACTER SET ascii COLLATE ascii_bin NOT NULL, reward_kind VARCHAR(8) NOT NULL, slot INT NOT NULL, item_id BIGINT UNSIGNED NOT NULL, quantity BIGINT UNSIGNED NULL, PRIMARY KEY (record_key,reward_kind,slot), INDEX item_id (item_id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\nCREATE TABLE ascension_quest_item_evidence (record_key VARCHAR(160) CHARACTER SET ascii COLLATE ascii_bin NOT NULL, item_id BIGINT UNSIGNED NOT NULL, item_name TEXT NOT NULL, item_class INT UNSIGNED NOT NULL, source_path TEXT NOT NULL, source_sha256 CHAR(64) NOT NULL, PRIMARY KEY(record_key,item_id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\nCREATE TABLE ascension_quest_export_issues (record_key VARCHAR(160) CHARACTER SET ascii COLLATE ascii_bin NOT NULL, issue_json LONGTEXT NOT NULL, INDEX record_key (record_key)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\nSTART TRANSACTION;\nINSERT INTO ascension_quest_export_metadata VALUES (1,${text(canonical({converter: VERSION, ...metadata}))});\n`;
  }
  function sql(row) {
    if (!/^[A-Za-z0-9_:/.-]{1,160}$/.test(row.key || '')) throw Error('Invalid export record key');
    const key = text(row.key);
    let out = `INSERT INTO ascension_quest_export VALUES (${[row.key, row.id, row.title, row.source, row.mode, row.locale, row.path || '', row.identity || '', row.adapter, canonical(row.raw), row.attribution === null ? null : canonical(row.attribution)].map(text).join(',')});\n`;
    out += `INSERT INTO ascension_quest_rewards VALUES (${key},${fields.map(f => row.rewards[f] ?? 'NULL').join(',')});\n`;
    for (const item of row.items) out += `INSERT INTO ascension_quest_reward_items VALUES (${key},${text(item.kind)},${item.slot},${item.id},${item.quantity ?? 'NULL'});\n`;
    const uniqueItems = new Set(row.items.map(i => i.id));
    for (const item of uniqueItems) {
      const evidence = row.itemEvidence?.items?.[item];
      if (evidence) out += `INSERT INTO ascension_quest_item_evidence VALUES (${key},${integer(item)},${text(evidence[0])},${integer(evidence[1])},${text(row.itemEvidence.path)},${text(row.itemEvidence.sha256)});\n`;
    }
    for (const issue of row.issues) out += `INSERT INTO ascension_quest_export_issues VALUES (${key},${text(canonical(issue))});\n`;
    return out;
  }
  function summary() { return {records: 0, reward_records: 0, evidence_only_records: 0, item_rewards: 0, issues: 0, sources: Object.create(null), modes: Object.create(null), adapters: Object.create(null), max_item_quantity: '0'}; }
  function count(s, r) {
    s.records++; s[r.adapter === 'evidence-only' ? 'evidence_only_records' : 'reward_records']++;
    s.item_rewards += r.items.length; s.issues += r.issues.length;
    for (const [name, key] of [['sources', r.source], ['modes', r.mode], ['adapters', r.adapter]]) s[name][key] = (s[name][key] || 0) + 1;
    for (const item of r.items) if (item.quantity && BigInt(item.quantity) > BigInt(s.max_item_quantity)) s.max_item_quantity = item.quantity;
  }
  function footer(s) { return `INSERT INTO ascension_quest_export_metadata VALUES (2,${text(canonical(s))});\nCOMMIT;\n-- Export complete: ${s.records} source records; ${s.item_rewards} item reward slots; ${s.issues} retained issues.\n`; }
  globalThis.AscensionQuestSQL = Object.freeze({VERSION, fields, scalar, integer, decimal, text, convert, header, sql, summary, count, footer});
  if (typeof module !== 'undefined' && module.exports) module.exports = globalThis.AscensionQuestSQL;
})();
