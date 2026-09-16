importScripts('search-aliases.js', 'search-query.js', 'quest-sql.js', 'quest-server-sql.js', 'quest-export.js');
let controller;
onmessage = async ({data}) => {
  controller?.abort();
  if (data.cancel) return;
  const own = controller = new AbortController(), {signal} = own;
  try {
    if (typeof CompressionStream === 'undefined') throw Error('This browser cannot create gzip downloads. Use a current browser or the full SQL report.');
    const compression = new CompressionStream('gzip'), writer = compression.writable.getWriter();
    const chunks = [], encoder = new TextEncoder();
    let outputBytes = 0, inputBytes = 0;
    const collecting = (async () => {
      const reader = compression.readable.getReader();
      try {
        while (true) {
          const {done, value} = await reader.read(); if (done) break;
          outputBytes += value.byteLength;
          if (outputBytes > 256 * 1024 * 1024) throw Error('Download exceeds 256 MiB. Narrow the quest filters and try again.');
          chunks.push(value);
        }
      } catch (error) { own.abort(); await reader.cancel(error); throw error; }
    })();
    // Observe errors immediately while writes are in progress.
    collecting.catch(() => {});
    const load = async path => {
      const response = await fetch(data.dataBase + path, {signal});
      if (!response.ok) throw Error(`Catalog part unavailable (${response.status}). Retry or refresh the catalog.`);
      const buffer = await response.arrayBuffer(), bytes = new Uint8Array(buffer);
      return bytes[0] === 31 && bytes[1] === 139
        ? new Response(new Blob([buffer]).stream().pipeThrough(new DecompressionStream('gzip'))).json()
        : JSON.parse(new TextDecoder().decode(buffer));
    };
    const evidenceResponse = await fetch('quest-item-evidence.json.gz', {signal});
    if (!evidenceResponse.ok) throw Error('Reward item evidence unavailable. Please retry.');
    data.itemEvidence = await new Response(evidenceResponse.body.pipeThrough(new DecompressionStream('gzip'))).json();
    let result;
    try {
      result = await AscensionQuestExport.run(data, {load, signal,
        write: async text => {
          const bytes = encoder.encode(text); inputBytes += bytes.length;
          if (inputBytes > 2 * 1024 * 1024 * 1024) throw Error('SQL exceeds 2 GiB. Narrow the quest filters and try again.');
          await writer.write(bytes);
        },
        progress: progress => postMessage({id: data.id, progress}),
      });
      await writer.close(); await collecting;
    } catch (error) { await writer.abort(error).catch(() => {}); throw error; }
    if (!signal.aborted) postMessage({id: data.id, result, blob: new Blob(chunks, {type: 'application/gzip'})});
  } catch (error) {
    if (error.name !== 'AbortError') postMessage({id: data.id, error: error.message});
  }
};
