import {
  FILE_LIMIT,
  TOTAL_LIMIT,
  inspectWdbHeader,
} from "../shared/policy.mjs";

// Only a bounded source window and one output part need byte arrays in memory.
// Consumers must discard every yielded part if validation later fails.
export async function* splitWdbFile(name, file) {
  if (file.size > TOTAL_LIMIT) throw Error("Source WDB exceeds 512 MiB");
  const header = new Uint8Array(await file.slice(0, 24).arrayBuffer());
  inspectWdbHeader(name, header);
  let p = 24,
    records = 0;
  let part = new Uint8Array(FILE_LIMIT),
    used = 24;
  part.set(header);
  while (p < file.size) {
    const window = new Uint8Array(
      await file.slice(p, p + FILE_LIMIT).arrayBuffer(),
    );
    const view = new DataView(window.buffer);
    let offset = 0;
    while (offset < window.length) {
      if (offset + 8 > window.length) {
        if (p + window.length === file.size)
          throw Error("Truncated WDB record");
        break;
      }
      const id = view.getUint32(offset, true),
        size = view.getUint32(offset + 4, true);
      if (id === 0 && size === 0) {
        if (window.subarray(offset).some((v) => v !== 0))
          throw Error("Unexpected bytes after terminator");
        for (
          let tail = p + window.length;
          tail < file.size;
          tail += FILE_LIMIT
        ) {
          const padding = new Uint8Array(
            await file.slice(tail, tail + FILE_LIMIT).arrayBuffer(),
          );
          if (padding.some((v) => v !== 0))
            throw Error("Unexpected bytes after terminator");
        }
        p = file.size;
        break;
      }
      if (!id || size > FILE_LIMIT - 40 || p + offset + 8 + size > file.size)
        throw Error("Invalid WDB record size");
      if (offset + 8 + size > window.length) break;
      if (++records > 1000000) throw Error("Too many WDB records");
      if (used + 8 + size + 8 > FILE_LIMIT) {
        yield part.slice(0, used + 8);
        part = new Uint8Array(FILE_LIMIT);
        part.set(header);
        used = 24;
      }
      part.set(window.subarray(offset, offset + 8 + size), used);
      used += 8 + size;
      offset += 8 + size;
    }
    if (p !== file.size) p += offset;
  }
  if (!records) throw Error("Empty cache");
  if (used > 24) yield part.slice(0, used + 8);
}
