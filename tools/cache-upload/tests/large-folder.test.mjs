import test from "node:test";
import assert from "node:assert/strict";
import { splitWdbFile } from "../lib/wdb-file.mjs";
import {
  splitWdb,
  inspectWdb,
  FILE_LIMIT,
  TOTAL_LIMIT,
  validateManifest,
  sha256,
} from "../shared/policy.mjs";
function cache(count = 11, payload = 1000000) {
  const data = new Uint8Array(32 + count * (8 + payload)),
    v = new DataView(data.buffer);
  data.set(new TextEncoder().encode("BDIW"));
  v.setUint32(4, 12340, true);
  data.set(new TextEncoder().encode("SUne"), 8);
  for (let n = 0, p = 24; n < count; n++, p += 8 + payload) {
    v.setUint32(p, n + 1, true);
    v.setUint32(p + 4, payload, true);
    data[p + 8] = n;
  }
  return data;
}
test("bounded file reads produce exactly the same complete WDB parts", async () => {
  const data = cache(),
    source = new Blob([data]),
    reads = [];
  const file = {
    size: source.size,
    slice(a, b) {
      reads.push(b - a);
      return source.slice(a, b);
    },
  };
  const actual = [];
  for await (const part of splitWdbFile("itemcache.wdb", file))
    actual.push(part);
  assert.deepEqual(actual, splitWdb("itemcache.wdb", data));
  assert.ok(Math.max(...reads) <= FILE_LIMIT);
  assert.equal(
    actual.reduce((n, p) => n + inspectWdb("itemcache.wdb", p).records, 0),
    11,
  );
});
test("stream validation rejects bad headers, truncated tails, and hidden terminator data", async () => {
  const valid = cache();
  for (const data of [
    valid.slice(0, -9),
    new Uint8Array([...cache(1, 4), 1]),
    new Uint8Array(30),
  ]) {
    await assert.rejects(async () => {
      for await (const part of splitWdbFile("itemcache.wdb", new Blob([data])))
        void part;
    });
  }
});
test("worker discards early parts of a corrupt file and accepts a standalone Desktop WDB", async () => {
  let result;
  globalThis.self = {
    postMessage: (r) => {
      if (!r.progress) result = r;
    },
  };
  await import("../lib/prepare.worker.mjs");
  await self.onmessage({
    data: {
      files: [
        {
          file: new File([cache().slice(0, -9)], "itemcache.wdb"),
          path: "WDB/realm/itemcache.wdb",
        },
        {
          file: new File([cache(1, 4)], "itemcache.wdb"),
          path: "WDB/enUS/itemcache.wdb",
        },
      ],
    },
  });
  assert.equal(result.files.length, 1);
  assert.equal(result.skipped.length, 1);
  const f = result.files[0];
  assert.ok(f.data instanceof Blob);
  assert.equal(
    await sha256(new Uint8Array(await f.data.arrayBuffer())),
    f.sha256,
  );
  assert.equal(
    inspectWdb(f.name, new Uint8Array(await f.data.arrayBuffer())).records,
    1,
  );
});
test("manifests accept a 305 MiB folder and reject totals above 512 MiB", () => {
  const f = {
    name: "itemcache.wdb",
    mode: "unknown",
    size: FILE_LIMIT,
    sha256: "a".repeat(64),
  };
  const m = {
    policy: 1,
    consent: true,
    files: Array.from({ length: 77 }, () => ({ ...f })),
  };
  assert.equal(validateManifest(m).total, 308 * 1048576);
  m.files = Array.from({ length: 128 }, () => ({ ...f }));
  assert.equal(validateManifest(m).total, TOTAL_LIMIT);
  m.files.push({ ...f, size: 1 });
  assert.throws(() => validateManifest(m), /512 MiB/);
});
