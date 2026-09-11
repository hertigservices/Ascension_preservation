import test from "node:test";
import assert from "node:assert/strict";
import {
  sanitize,
  sanitizeLua,
  splitWdb,
  inspectWdb,
  bytes,
  sha256,
  validateManifest,
  FILE_LIMIT,
  detectMode,
} from "../shared/policy.mjs";
import { readZip } from "../shared/zip.mjs";
import { zipSync } from "fflate";
export function wdb(payloadSize = 4, count = 1) {
  const b = new Uint8Array(24 + count * (8 + payloadSize) + 8),
    v = new DataView(b.buffer);
  b.set(bytes("BDIW"));
  v.setUint32(4, 12340, true);
  b.set(bytes("SUne"), 8);
  for (let i = 0, p = 24; i < count; i++, p += 8 + payloadSize) {
    v.setUint32(p, 100 + i, true);
    v.setUint32(p + 4, payloadSize, true);
  }
  return b;
}
test("WDB validates complete records and rejects mail masquerading as items", () => {
  assert.equal(inspectWdb("itemcache.wdb", wdb()).records, 1);
  assert.throws(() => sanitize("itemtextcache.wdb", wdb()));
  const b = wdb();
  b.set(bytes("XTIW"));
  assert.throws(() => sanitize("itemcache.wdb", b));
  assert.throws(() => sanitize("itemcache.wdb", wdb().slice(0, 29)));
});
test("record-boundary splitting preserves every record exactly once", () => {
  const b = wdb(1000000, 9),
    parts = splitWdb("itemcache.wdb", b);
  assert.equal(parts.length, 3);
  assert.ok(parts.every((p) => p.length <= FILE_LIMIT));
  assert.equal(
    parts.reduce((n, p) => n + inspectWdb("itemcache.wdb", p).records, 0),
    9,
  );
});
test("SavedVariables are parsed as data and private branches are removed", () => {
  const b = bytes(
    'MobSpellsDB={profileKeys={["Secretchar - Realm"]="profile"},global={mobs={[12]={name="Wolf",lastGUID="0x1234567812345678",spells={[45]={amountMin=5,amountMax=8}}}}}}',
  );
  const result = sanitizeLua("mobspells.lua", b, ["Secretchar"]);
  const text = new TextDecoder().decode(result.data);
  assert.ok(!text.includes("profileKeys"));
  assert.ok(!text.includes("lastGUID"));
  assert.ok(text.includes("Wolf"));
  assert.equal(result.review, false);
  assert.deepEqual(sanitizeLua("mobspells.lua", result.data).data, result.data);
  assert.throws(() => sanitizeLua("mobspells.lua", bytes('os.execute("bad")')));
  assert.throws(() =>
    sanitizeLua("mobspells.lua", bytes('MobSpellsDB=loadstring("bad")()')),
  );
});
test("code and harvest data are held for review, identity removed, utf8 preserved", () => {
  const result = sanitizeLua(
    "wildcardharvest.lua",
    bytes(
      'AscensionRebirthHarvestDB={identity={["Secretchar@1"]={}},gossips={[1]={text="Greetings, Secretchar. café"}},enums={A=1},spellsByChar={Secretchar={1}}}',
    ),
  );
  assert.equal(result.review, true);
  const text = new TextDecoder().decode(result.data);
  assert.ok(!text.includes("Secretchar"));
  assert.ok(!text.includes("identity"));
  assert.deepEqual(
    sanitizeLua("wildcardharvest.lua", result.data).data,
    result.data,
  );
  assert.equal(
    sanitizeLua(
      "aio_client.lua",
      bytes("AIO_sv_Addons={};AIO_sv={Secretchar=1}"),
    ).review,
    true,
  );
});
test("arbitrary globals, duplicate globals and deep Lua are rejected", () => {
  assert.throws(() => sanitizeLua("gathermate2.lua", bytes("Other={1}")));
  assert.throws(() =>
    sanitizeLua(
      "gathermate2.lua",
      bytes("GatherMate2MineDB={};GatherMate2MineDB={}"),
    ),
  );
  assert.throws(() =>
    sanitizeLua(
      "gathermate2.lua",
      bytes("GatherMate2MineDB=" + "{x=".repeat(80) + "1" + "}".repeat(80)),
    ),
  );
});
test("ZIP rejects traversal and symlinks before inflation, excludes unsupported files", () => {
  const good = zipSync({
    "WDB/itemcache.wdb": wdb(),
    "Account/secrets.txt": bytes("private"),
  });
  assert.equal(Object.keys(readZip(good).files).length, 1);
  assert.throws(() => readZip(zipSync({ "../itemcache.wdb": wdb() })));
  const link = zipSync({ "itemcache.wdb": wdb() });
  for (let p = 0; p < link.length - 46; p++) {
    const v = new DataView(link.buffer);
    if (v.getUint32(p, true) === 0x02014b50) {
      v.setUint32(p + 38, 0xa0000000, true);
      break;
    }
  }
  assert.throws(() => readZip(link));
});
test("manifest strips arbitrary paths and does not trust claimed review flags", async () => {
  const data = bytes("AIO_sv_Addons={}");
  const manifest = validateManifest({
    policy: 1,
    consent: true,
    files: [
      {
        name: "aio_client.lua",
        size: data.length,
        sha256: await sha256(data),
        mode: "unknown",
        review: false,
        path: "C:/private",
      },
    ],
  });
  assert.equal(manifest.files[0].review, true);
  assert.equal(manifest.files[0].path, undefined);
  assert.throws(() =>
    validateManifest({ policy: 1, consent: false, files: [] }),
  );
  assert.equal(
    detectMode("Cache/WDB/enUS/Vol'jin - Conquest of Azeroth/itemcache.wdb"),
    "conquest-of-azeroth",
  );
  assert.equal(
    detectMode("Account/SOMEONE/SavedVariables/MobSpells.lua"),
    "unknown",
  );
});

test("Ascension capture formats retain references, remove private branches and are eligible for reference snapshot preservation", () => {
  const coa = sanitizeLua(
    "ascension_coareader.lua",
    bytes(
      'CoAReaderDB={edges={[101]={102}},edgeStats={count=1},tree={sample={spellID=42,player="Alice"}},known={55},globals={token="secret"},trace={calls={"chat"}},autorun="report"}',
    ),
    ["Alice"],
  );
  const coaText = new TextDecoder().decode(coa.data);
  assert.equal(coa.review, false);
  assert.match(coaText, /edges/);
  assert.doesNotMatch(coaText, /Alice|known|secret|trace|autorun|player/);
  assert.deepEqual(
    sanitizeLua("ascension_coareader.lua", coa.data).data,
    coa.data,
  );
  const harvest = sanitizeLua(
    "ascensionharvest.lua",
    bytes(
      'AscensionHarvestDB={realms={Realm={items={[1]={name="Sword",player="Alice"}},cursor=900}},sweeps={Realm={quest={data={[7]="Help Alice"},miss={[8]=true},cursor=10}}},watch={Realm={spells={[42]="Fireball",Alice="private"},casters={Alice=1},gossip={secret=1},fx={private=1}}},gear={Alice=1},addon={ASC_GUILD="secret"},dumps={private=1}}',
    ),
    ["Alice"],
  );
  const text = new TextDecoder().decode(harvest.data);
  assert.equal(harvest.review, false);
  assert.match(text, /Sword/);
  assert.match(text, /Fireball/);
  assert.doesNotMatch(
    text,
    /Alice|private|secret|ASC_GUILD|casters|gear|dumps|cursor|miss/,
  );
  assert.deepEqual(
    sanitizeLua("ascensionharvest.lua", harvest.data).data,
    harvest.data,
  );
  for (const [name, data] of [
    ["ascension_coareader.lua", coa.data],
    ["ascensionharvest.lua", harvest.data],
  ]) {
    const manifest = validateManifest({
      policy: 1,
      consent: true,
      files: [
        {
          name,
          mode: "unknown",
          size: data.length,
          sha256: "a".repeat(64),
          review: false,
        },
      ],
    });
    assert.equal(manifest.files[0].review, false);
  }
  assert.throws(
    () =>
      sanitizeLua(
        "ascension_coareader.lua",
        bytes("CoAReaderDB={known={1},trace={calls={}}}"),
      ),
    /No CoA/,
  );
  assert.throws(
    () =>
      sanitizeLua(
        "ascensionharvest.lua",
        bytes('AscensionHarvestDB={gear={1},addon={ASC_GUILD="secret"}}'),
      ),
    /No harvested/,
  );
});

import { readFileSync } from "node:fs";
import * as contractPolicy from "../shared/policy.mjs";
test("Python collector policy contract matches the browser and validator policy", () => {
  const contract=JSON.parse(readFileSync(new URL("../shared/policy-contract.json",import.meta.url),"utf8"));
  assert.deepEqual(contract,{policy:contractPolicy.POLICY_VERSION,names:[...contractPolicy.WDB_NAMES,...contractPolicy.LUA_NAMES],modes:contractPolicy.MODES,folders:contractPolicy.MODE_FOLDERS,review:contractPolicy.REVIEW_LUA_NAMES});
});
