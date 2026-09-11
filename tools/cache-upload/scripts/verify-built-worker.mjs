import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
const root = fileURLToPath(new URL("../dist/client/", import.meta.url));
const origin = process.argv[2];
async function asset(path) {
  const local = await readFile(join(root, path), "utf8");
  if (!origin) return local;
  const response = await fetch(new URL(path, origin), {
    headers: { "User-Agent": "AscensionArchive-BuildCheck/1.0" },
  });
  assert.equal(response.status, 200, `Missing public asset ${path}`);
  assert.match(response.headers.get("content-type"), /javascript/);
  const remote = await response.text();
  assert.equal(remote, local, "Deployed asset differs from current build");
  return remote;
}
const pages = (await readdir(join(root, "_next/static/chunks"))).filter((n) =>
  /^page-.*\.js$/.test(n),
);
assert.equal(pages.length, 1);
const page = await asset("_next/static/chunks/" + pages[0]);
assert.ok(
  !page.includes("file:///ROOT/app/page.tsx"),
  "Worker URL points at the build machine",
);
const workerPath = page.match(
  /\/_next\/static\/prepare\.worker-[\w-]+\.js/,
)?.[0];
assert.ok(workerPath, "Missing bundled worker URL");
const code = await asset(workerPath.slice(1));
const source = new Uint8Array(44),
  view = new DataView(source.buffer);
source.set(new TextEncoder().encode("BDIW"));
view.setUint32(4, 12340, true);
source.set(new TextEncoder().encode("SUne"), 8);
view.setUint32(24, 1, true);
view.setUint32(28, 4, true);
let result;
const self = {
  postMessage: (r) => {
    if (!r.progress) result = structuredClone(r);
  },
};
const context = vm.createContext({
  self,
  crypto,
  Blob,
  File,
  Uint8Array,
  Uint16Array,
  Uint32Array,
  Int32Array,
  ArrayBuffer,
  DataView,
  TextEncoder,
  TextDecoder,
  console,
});
vm.runInContext(code, context, { timeout: 5000 });
await self.onmessage({
  data: {
    files: [
      {
        file: new File([source], "itemcache.wdb"),
        path: "WDB/enUS/itemcache.wdb",
      },
      {
        file: new File(
          [
            'MobSpellsDB={global={mobs={[12]={name="Wolf",spells={[45]={amountMin=5}}}}}}',
          ],
          "MobSpells.lua",
        ),
        path: "Account/Example/SavedVariables/MobSpells.lua",
      },
    ],
  },
});
assert.equal(result.error, undefined);
assert.equal(result.files.length, 2, JSON.stringify(result.skipped));
assert.ok(result.files.every((f) => f.data instanceof Blob && f.size > 0));
console.log(
  `${origin ? "Deployed" : "Built"} page uses a site-relative worker URL; bundled processor prepares WDB and Account fixtures.`,
);
