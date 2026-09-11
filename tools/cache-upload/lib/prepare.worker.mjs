import { readZip } from "../shared/zip.mjs";
import { splitWdbFile } from "./wdb-file.mjs";
import {
  canonicalName,
  allowedName,
  detectMode,
  identifiersFromPath,
  splitWdb,
  sanitizeLua,
  sha256,
  TOTAL_LIMIT,
  FILE_COUNT,
  requiresReview,
} from "../shared/policy.mjs";
self.onmessage = async ({ data: { files } }) => {
  try {
    const result = [],
      skipped = [],
      seen = new Set();
    let total = 0;
    async function add(path, source) {
      const name = canonicalName(path);
      if (!allowedName(name)) {
        skipped.push("Unrelated or unsupported file");
        return;
      }
      const staged = [],
        stagedKeys = new Set();
      let stagedTotal = 0,
        overLimit = false;
      try {
        const mode = detectMode(path);
        const chunks = name.endsWith(".wdb")
          ? source instanceof Uint8Array
            ? splitWdb(name, source)
            : splitWdbFile(name, source)
          : [
              sanitizeLua(
                name,
                source instanceof Uint8Array
                  ? source
                  : new Uint8Array(await source.arrayBuffer()),
                identifiersFromPath(path),
              ).data,
            ];
        for await (const data of chunks) {
          const hash = await sha256(data),
            key = hash + mode;
          if (seen.has(key) || stagedKeys.has(key)) continue;
          stagedTotal += data.length;
          if (
            total + stagedTotal > TOTAL_LIMIT ||
            result.length + staged.length >= FILE_COUNT
          ) {
            overLimit = true;
            throw Error(
              "Selection exceeds 512 MiB or 256 file parts. Choose fewer folders or files.",
            );
          }
          stagedKeys.add(key);
          staged.push({
            name,
            mode,
            size: data.length,
            sha256: hash,
            review: requiresReview(name),
            data: new Blob([data]),
          });
          self.postMessage({
            progress: true,
            preparedBytes: total + stagedTotal,
          });
        }
        result.push(...staged);
        total += stagedTotal;
        for (const key of stagedKeys) seen.add(key);
      } catch (e) {
        if (overLimit) throw e;
        skipped.push(name + ": " + e.message);
      }
    }
    for (const entry of files) {
      const file = entry.file || entry;
      if (file.name.toLowerCase().endsWith(".zip")) {
        if (file.size > 128 * 1024 * 1024)
          throw Error(
            "ZIP exceeds 128 MiB. Select the extracted WDB or Account folder instead (up to 512 MiB of prepared data).",
          );
        const zip = readZip(new Uint8Array(await file.arrayBuffer()));
        if (zip.skipped) skipped.push(zip.skipped + " unrelated ZIP entries");
        for (const [path, b] of Object.entries(zip.files)) await add(path, b);
      } else {
        const path = entry.path || file.webkitRelativePath || file.name;
        const name = canonicalName(path);
        if (!allowedName(name)) {
          skipped.push("Unrelated or unsupported file");
          continue;
        }
        const limit = name.endsWith(".wdb") ? TOTAL_LIMIT : 8 * 1024 * 1024;
        if (file.size > limit) {
          skipped.push(
            name + ": exceeds " + limit / 1048576 + " MiB source-file limit",
          );
          continue;
        }
        await add(path, file);
      }
    }
    self.postMessage({ files: result, skipped });
  } catch (e) {
    self.postMessage({ error: e.message || "Could not read these files" });
  }
};
