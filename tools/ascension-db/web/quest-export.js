/* The CLI and browser call this same export pipeline and the search predicate. */
(() => {
  'use strict';
  async function run(request, {load, write, progress = () => {}, signal}) {
    if (request.kind !== 'quest') throw Error('SQL export requires the Quests collection.');
    AscensionSearchQuery.validate(request);
    const check = () => { if (signal?.aborted) throw new DOMException('Export cancelled', 'AbortError'); };
    const {parts, match} = AscensionSearchQuery.candidates(request);
    const groups = new Map(), seen = new Set();
    for (let i = 0; i < parts.length; i++) {
      check();
      const rows = await load(parts[i]);
      if (!Array.isArray(rows)) throw Error('Invalid search partition');
      for (const row of rows) if (match(row, parts[i]) && !seen.has(row[0])) {
        if (!/^[a-z0-9]+\/\d+\/\d+$/.test(row[0])) throw Error('Invalid catalog record key');
        seen.add(row[0]);
        const [fid, part, offset] = row[0].split('/');
        if (!request.manifest.files[fid]) throw Error('Missing source identity');
        const path = `records/${fid}/${part}.json.gz`;
        if (!groups.has(path)) groups.set(path, []);
        groups.get(path).push({row, fid, offset: Number(offset)});
      }
      progress({phase: 'Finding matching quests', done: i + 1, total: parts.length, records: seen.size});
    }
    if (!seen.size) throw Error('No quests match these filters.');
    const sql = AscensionQuestSQL, result = sql.summary();
    await write(sql.header({revision: request.manifest.revision, catalog: request.dataBase,
      filters: request.filters || {q: request.q || '', kind: request.kind, source: request.source || '', mode: request.mode || '', name: request.name || '', id: request.recordId || ''},
      item_evidence_snapshot: request.itemEvidence?.cache_snapshot || null, expected_records: seen.size}));
    let done = 0;
    for (const [path, entries] of groups) {
      check();
      const records = await load(path);
      for (const {row, fid, offset} of entries) {
        check();
        const record = records[offset], file = request.manifest.files[fid];
        if (!record || record[2] !== 'quest' || String(record[0]) !== row[5] || record[1] !== row[1] || record[3] !== row[3] || record[4] !== row[4]) throw Error('Index/detail mismatch; export stopped without downloading partial SQL.');
        const evidence = request.itemEvidence?.sources?.[file.path];
        const converted = sql.convert(record, {key: row[0], path: file.path, identity: file.blob});
        converted.itemEvidence = evidence || null;
        await write(sql.sql(converted));
        sql.count(result, converted);
      }
      progress({phase: 'Converting quest rewards', done: ++done, total: groups.size, records: result.records, expected: seen.size});
    }
    if (result.records !== seen.size) throw Error('Export record count mismatch');
    await write(sql.footer(result));
    await write(AscensionQuestServerSQL.generate());
    return result;
  }
  globalThis.AscensionQuestExport = Object.freeze({run});
})();
