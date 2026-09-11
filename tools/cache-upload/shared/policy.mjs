import luaparse from "luaparse";
export const POLICY_VERSION = 1;
export const FILE_LIMIT = 4 * 1024 * 1024;
export const TOTAL_LIMIT = 512 * 1024 * 1024;
export const FILE_COUNT = 256;
export const MODES = [
  "unknown",
  "conquest-of-azeroth",
  "coa-alpha",
  "coa-beta",
  "coa-ptr",
  "free-pick",
  "season-10-freepick",
  "season-10-wildcard",
  "season-9",
  "warcraft-reborn",
  "live-qa",
  "stress-test",
];
export const MODE_FOLDERS = {
  unknown: "unknown",
  "conquest-of-azeroth": "Unknown realm - Conquest of Azeroth",
  "coa-alpha": "Unknown realm - CoA Alpha - Development",
  "coa-beta": "Unknown realm - CoA Beta",
  "coa-ptr": "Unknown realm - CoA PTR",
  "free-pick": "Unknown realm - Free-Pick",
  "season-10-freepick": "Unknown realm - Season 10 Freepick",
  "season-10-wildcard": "Unknown realm - Season 10 Wildcard",
  "season-9": "Unknown realm - Season 9",
  "warcraft-reborn": "Unknown realm - Warcraft Reborn",
  "live-qa": "Unknown realm - Live QA",
  "stress-test": "Unknown realm - Stress Test",
};
const MAGIC = {
  WIDB: "itemcache.wdb",
  WGOB: "gameobjectcache.wdb",
  WMOB: "creaturecache.wdb",
  WNPC: "npccache.wdb",
  WQST: "questcache.wdb",
  WPTX: "pagetextcache.wdb",
  WNDB: "itemnamecache.wdb",
};
export const WDB_NAMES = Object.values(MAGIC);
export const LUA_NAMES = [
  "mobspells.lua",
  "aio_client.lua",
  "auctionator_price_database.lua",
  "gathermate2.lua",
  "coasniff.lua",
  "wildcardharvest.lua",
  "ascension_coareader.lua",
  "ascensionharvest.lua",
];
export const REVIEW_LUA_NAMES = [
  "aio_client.lua",
  "coasniff.lua",
  "wildcardharvest.lua",
];
export const requiresReview = (name) => REVIEW_LUA_NAMES.includes(name);
const enc = new TextEncoder();
const dec = new TextDecoder("utf-8", { fatal: true });
export const bytes = (s) => enc.encode(s);
export function canonicalName(path) {
  const n = path
    .replaceAll("\\", "/")
    .split("/")
    .pop()
    .toLowerCase()
    .replace(/\.bak$/, "");
  return /^wildcardharvest[\w.\-()' ]*\.lua$/.test(n)
    ? "wildcardharvest.lua"
    : n;
}
export function allowedName(n) {
  return Object.values(MAGIC).includes(n) || LUA_NAMES.includes(n);
}
export function detectMode(path) {
  const s = path.toLowerCase();
  if (/ptr/.test(s)) return "coa-ptr";
  if (/coa alpha/.test(s)) return "coa-alpha";
  if (/coa beta/.test(s)) return "coa-beta";
  if (/conquest of azeroth/.test(s)) return "conquest-of-azeroth";
  if (/season.?10.*wildcard/.test(s)) return "season-10-wildcard";
  if (/season.?10.*free/.test(s)) return "season-10-freepick";
  if (/season.?9/.test(s)) return "season-9";
  if (/warcraft reborn/.test(s)) return "warcraft-reborn";
  if (/live qa/.test(s)) return "live-qa";
  if (/stress test/.test(s)) return "stress-test";
  if (/free-pick/.test(s)) return "free-pick";
  return "unknown";
}
export function identifiersFromPath(path) {
  const a = path.replaceAll("\\", "/").split("/");
  const i = a.findIndex((x) => x.toLowerCase() === "account");
  return i < 0
    ? []
    : [a[i + 1], a[i + 3]].filter(
        (x) => x && x.toLowerCase() !== "savedvariables" && x.length >= 3,
      );
}
export function inspectWdbHeader(name, b) {
  if (b.length < 24) throw Error("Incomplete WDB header");
  const view = new DataView(b.buffer, b.byteOffset, b.byteLength);
  const magic = String.fromCharCode(...b.subarray(0, 4))
    .split("")
    .reverse()
    .join("");
  if (MAGIC[magic] !== name)
    throw Error("WDB type does not match its filename");
  if (view.getUint32(4, true) !== 12340)
    throw Error("Unsupported client build");
  const locale = String.fromCharCode(...b.subarray(8, 12))
    .split("")
    .reverse()
    .join("");
  if (
    !/^(enUS|enGB|deDE|frFR|esES|esMX|ruRU|koKR|zhCN|zhTW|ptBR|itIT)$/.test(
      locale,
    )
  )
    throw Error("Unsupported locale");
  return locale;
}
export function inspectWdb(name, b) {
  const locale = inspectWdbHeader(name, b);
  const view = new DataView(b.buffer, b.byteOffset, b.byteLength);
  let p = 24,
    records = 0;
  const boundaries = [];
  while (p < b.length) {
    if (p + 8 > b.length) throw Error("Truncated WDB record");
    const id = view.getUint32(p, true),
      size = view.getUint32(p + 4, true);
    if (id === 0 && size === 0) {
      if (b.subarray(p).some((v) => v !== 0))
        throw Error("Unexpected bytes after terminator");
      break;
    }
    if (!id || size > FILE_LIMIT - 40 || p + 8 + size > b.length)
      throw Error("Invalid WDB record size");
    boundaries.push([p, p + 8 + size]);
    p += 8 + size;
    if (++records > 1000000) throw Error("Too many WDB records");
  }
  if (!records) throw Error("Empty cache");
  return { locale, records, boundaries };
}
export function splitWdb(name, b) {
  const info = inspectWdb(name, b);
  let parts = [],
    chunks = [],
    length = 32;
  function flush() {
    if (!chunks.length) return;
    const out = new Uint8Array(length);
    out.set(b.subarray(0, 24));
    let p = 24;
    for (const [a, z] of chunks) {
      out.set(b.subarray(a, z), p);
      p += z - a;
    }
    parts.push(out);
    chunks = [];
    length = 32;
  }
  for (const [a, z] of info.boundaries) {
    if (length + z - a > FILE_LIMIT) flush();
    chunks.push([a, z]);
    length += z - a;
  }
  flush();
  return parts;
}
function table() {
  return new Map();
}
function get(t, k) {
  return t instanceof Map ? t.get(k) : undefined;
}
function pick(t, keys) {
  const out = table();
  for (const k of keys) if (t instanceof Map && t.has(k)) out.set(k, t.get(k));
  return out;
}
function wrap(path, value) {
  for (const key of [...path].reverse()) value = new Map([[key, value]]);
  return value;
}
function parseLua(b) {
  if (b.length > 8 * 1024 * 1024) throw Error("Addon file is too large");
  let binary = "";
  for (let i = 0; i < b.length; i += 8192)
    binary += String.fromCharCode(...b.subarray(i, i + 8192));
  let nodes = 0;
  const ast = luaparse.parse(binary, {
    luaVersion: "5.1",
    encodingMode: "pseudo-latin1",
    comments: false,
    scope: false,
    locations: false,
    onCreateNode() {
      if (++nodes > 250000) throw Error("Addon structure is too large");
    },
  });
  let visits = 0;
  function value(n, depth = 0) {
    if (++visits > 250000 || depth > 64) throw Error("Addon nesting limit");
    switch (n.type) {
      case "NumericLiteral":
        if (!Number.isFinite(n.value)) throw Error("Invalid number");
        return n.value;
      case "StringLiteral":
        return dec.decode(Uint8Array.from(n.value, (c) => c.charCodeAt(0)));
      case "BooleanLiteral":
        return n.value;
      case "NilLiteral":
        return null;
      case "UnaryExpression":
        if (n.operator === "-" && n.argument.type === "NumericLiteral")
          return -n.argument.value;
        break;
      case "TableConstructorExpression": {
        const t = table();
        let index = 1;
        for (const f of n.fields) {
          const key =
            f.type === "TableValue"
              ? index++
              : f.type === "TableKeyString"
                ? f.key.name
                : value(f.key, depth + 1);
          if (!["string", "number"].includes(typeof key) || t.has(key))
            throw Error("Invalid or duplicate Lua key");
          t.set(key, value(f.value, depth + 1));
        }
        return t;
      }
    }
    throw Error(
      "Only literal SavedVariables are accepted; Lua is never executed",
    );
  }
  const globals = table();
  for (const statement of ast.body) {
    if (
      statement.type !== "AssignmentStatement" ||
      statement.variables.length !== statement.init.length
    )
      throw Error("Only SavedVariables assignments are accepted");
    statement.variables.forEach((v, i) => {
      if (v.type !== "Identifier" || globals.has(v.name))
        throw Error("Invalid global assignment");
      globals.set(v.name, value(statement.init[i]));
    });
  }
  return globals;
}
const DROP = new Set([
  "profilekeys",
  "profiles",
  "identity",
  "spellsbychar",
  "lastguid",
  "lasttime",
  "player",
  "playername",
  "character",
  "charactername",
  "account",
  "accountname",
  "chat",
  "whispers",
  "macros",
  "hotbars",
  "toons",
  "calls",
  "tokens",
  "rapidrollingstate",
  "eligibility",
  "eligibilityreasons",
  "startingchoice",
]);
function scrub(v, identifiers, depth = 0) {
  if (depth > 64) throw Error("Nesting limit");
  if (v instanceof Map) {
    const t = table();
    for (const [k, x] of v) {
      if (
        typeof k === "string" &&
        (DROP.has(k.toLowerCase()) ||
          identifiers.some((id) => id.toLowerCase() === k.toLowerCase()))
      )
        continue;
      const nk = typeof k === "string" ? scrub(k, identifiers, depth + 1) : k;
      if (t.has(nk))
        throw Error("Redacted key collision; needs private review");
      t.set(nk, scrub(x, identifiers, depth + 1));
    }
    return t;
  }
  if (typeof v === "string") {
    let s = v
      .replace(/[\w.+-]+@[\w.-]+\.[\w.-]+/g, "<redacted-email>")
      .replace(/0x[\da-f]{16}/gi, "<redacted-guid>");
    for (const id of identifiers) {
      if (id.length >= 3)
        s = s.replace(
          new RegExp(
            "(?<![\\p{L}\\p{N}_])" +
              id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") +
              "(?![\\p{L}\\p{N}_])",
            "giu",
          ),
          "$n",
        );
    }
    return s;
  }
  return v;
}
function encodeLua(v) {
  if (v instanceof Map)
    return (
      "{" +
      [...v]
        .map(([k, x]) => "[" + encodeLua(k) + "]=" + encodeLua(x))
        .join(",") +
      "}"
    );
  if (v === null) return "nil";
  if (typeof v === "string") {
    let s = '"';
    for (const b of enc.encode(v)) {
      s +=
        b < 32 || b > 126 || b === 34 || b === 92
          ? "\\" + String(b).padStart(3, "0")
          : String.fromCharCode(b);
    }
    return s + '"';
  }
  if (typeof v === "number" && !Number.isFinite(v))
    throw Error("Invalid number");
  return String(v);
}
function identityNames(v, out) {
  if (v instanceof Map) {
    for (const [k, x] of v) {
      if (typeof k === "string" && /@\d+$/.test(k))
        out.push(k.replace(/@\d+$/, ""));
      identityNames(x, out);
    }
  }
}
// Capture only explicitly understood reference branches. New capture formats
// are deduplicated as filtered reference snapshots by the consolidator.
function idRecords(t, stringsOnly = false) {
  const out = table();
  if (!(t instanceof Map)) return out;
  for (const [id, value] of t) {
    const validId =
      (typeof id === "number" && Number.isSafeInteger(id) && id > 0) ||
      (typeof id === "string" && /^[1-9][0-9]*$/.test(id));
    if (validId && (!stringsOnly || typeof value === "string"))
      out.set(id, value);
  }
  return out;
}
function harvestReferences(h) {
  const out = table();
  for (const branch of ["realms", "sweeps", "watch"]) {
    const source = get(h, branch),
      realms = table();
    if (!(source instanceof Map)) continue;
    for (const [realm, record] of source) {
      if (typeof realm !== "string" || !(record instanceof Map)) continue;
      const clean = table();
      if (branch === "realms") {
        for (const kind of ["items", "tips"]) {
          const ids = idRecords(get(record, kind));
          if (ids.size) clean.set(kind, ids);
        }
      } else if (branch === "sweeps") {
        for (const kind of ["quest", "questtitle"]) {
          const ids = idRecords(get(get(record, kind), "data"));
          if (ids.size) clean.set(kind, new Map([["data", ids]]));
        }
      } else {
        const spells = idRecords(get(record, "spells"), true);
        if (spells.size) clean.set("spells", spells);
      }
      if (clean.size) realms.set(realm, clean);
    }
    if (realms.size) out.set(branch, realms);
  }
  return out;
}
export function sanitizeLua(name, b, identifiers = []) {
  const g = parseLua(b);
  let out = table(),
    review = false;
  switch (name) {
    case "ascension_coareader.lua": {
      const h = get(g, "CoAReaderDB");
      const refs = pick(h, ["edges", "edgeStats", "tree"]);
      if (
        !["edges", "tree"].some(
          (k) => get(refs, k) instanceof Map && get(refs, k).size,
        )
      )
        throw Error("No CoA class-tree reference data found");
      out.set("CoAReaderDB", refs);
      break;
    }
    case "ascensionharvest.lua": {
      const refs = harvestReferences(get(g, "AscensionHarvestDB"));
      if (!refs.size)
        throw Error("No harvested item, quest or spell reference data found");
      out.set("AscensionHarvestDB", refs);
      break;
    }
    case "mobspells.lua": {
      const mobs = get(get(get(g, "MobSpellsDB"), "global"), "mobs");
      if (!(mobs instanceof Map)) throw Error("No mob observations found");
      out.set("MobSpellsDB", wrap(["global", "mobs"], mobs));
      break;
    }
    case "gathermate2.lua":
      out = pick(g, [
        "GatherMate2HerbDB",
        "GatherMate2MineDB",
        "GatherMate2FishDB",
        "GatherMate2GasDB",
        "GatherMate2TreeDB",
        "GatherMate2TreasureDB",
      ]);
      break;
    case "auctionator_price_database.lua":
      out = pick(g, ["AUCTIONATOR_PRICE_DATABASE"]);
      break;
    case "aio_client.lua":
      out = pick(g, ["AIO_sv_Addons"]);
      review = true;
      break;
    case "coasniff.lua":
      out = pick(g, ["CoASniffDB"]);
      review = true;
      break;
    case "wildcardharvest.lua": {
      const h = get(g, "AscensionRebirthHarvestDB");
      identityNames(get(h, "identity"), identifiers);
      out.set(
        "AscensionRebirthHarvestDB",
        pick(h, [
          "version",
          "captured",
          "vendors",
          "gossips",
          "advancement",
          "wildcard",
          "byRealm",
          "enums",
          "apiSurfaces",
          "dungeons",
          "battlegrounds",
          "skillCards",
          "roleRequirements",
        ]),
      );
      review = true;
      break;
    }
    default:
      throw Error("Unsupported addon");
  }
  if (!out.size) throw Error("No supported game data found");
  out = scrub(out, identifiers);
  const result = enc.encode(
    [...out].map(([k, v]) => k + "=" + encodeLua(v) + "\n").join(""),
  );
  if (result.length > FILE_LIMIT) throw Error("Filtered addon exceeds 4 MiB");
  return { data: result, review };
}
export function sanitize(name, b, identifiers = []) {
  if (!allowedName(name))
    throw Error("File type is excluded by the privacy policy");
  if (b.length > FILE_LIMIT) throw Error("File exceeds 4 MiB");
  if (name.endsWith(".wdb")) {
    inspectWdb(name, b);
    return { data: b, review: false };
  }
  return sanitizeLua(name, b, identifiers);
}
export async function sha256(data) {
  return [...new Uint8Array(await crypto.subtle.digest("SHA-256", data))]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
export function validateManifest(m) {
  if (
    !m ||
    m.policy !== POLICY_VERSION ||
    m.consent !== true ||
    !Array.isArray(m.files) ||
    !m.files.length ||
    m.files.length > FILE_COUNT
  )
    throw Error("Invalid submission manifest");
  let total = 0;
  const files = m.files.map((f, i) => {
    if (
      !f ||
      !allowedName(f.name) ||
      !MODES.includes(f.mode) ||
      !Number.isSafeInteger(f.size) ||
      f.size < 1 ||
      f.size > FILE_LIMIT ||
      !/^([a-f0-9]{64})$/.test(f.sha256)
    )
      throw Error("Invalid file metadata");
    total += f.size;
    return {
      index: i,
      name: f.name,
      mode: f.mode,
      size: f.size,
      sha256: f.sha256,
      review: requiresReview(f.name),
    };
  });
  if (total > TOTAL_LIMIT) throw Error("Submission exceeds 512 MiB");
  return { policy: POLICY_VERSION, files, total };
}
