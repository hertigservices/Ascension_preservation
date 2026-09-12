import {collectionDirectory, collectionLabel} from './collections.js';
import {renderCoverage} from "./coverage-controls.js";
import {searchColumnState, searchTable, bindSearchColumns} from './search-controls.js';
import {showAtlas, atlasRecordLinks, closeAtlas} from './atlas.js';
const $ = (s) => document.querySelector(s),
  esc = (s) =>
    String(s ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    ),
  num = (n) => Number(n).toLocaleString();
let manifest,
  coverage,
  recovery,
  request = 0,
  page = 0,
  dataBase = "";
const worker = new Worker("search-worker.js");
const params = () =>
  new URLSearchParams(location.hash.replace(/^#(?:search\?|record=)?/, ""));
const sourceUrl = (path) =>
  "https://github.com/hertigservices/ascension-data/blob/" +
  manifest.revision +
  "/" +
  path.split("/").map(encodeURIComponent).join("/");
const rawUrl = (path) =>
  "https://raw.githubusercontent.com/hertigservices/ascension-data/" +
  manifest.revision +
  "/" +
  path.split("/").map(encodeURIComponent).join("/");
const label = (k) => manifest?.kinds[k]?.label || k;
const safeUrl = (u) => {
  try {
    const v = new URL(u);
    return ["https:", "http:"].includes(v.protocol) ? esc(v.href) : "#";
  } catch {
    return "#";
  }
};
async function gz(path) {
  const r = await fetch(dataBase + path);
  if (!r.ok)
    throw Error(`Unable to read ${path} (${r.status}). Please refresh.`);
  const a = await r.arrayBuffer(),
    b = new Uint8Array(a);
  return b[0] === 31 && b[1] === 139
    ? new Response(
        new Blob([a]).stream().pipeThrough(new DecompressionStream("gzip")),
      ).json()
    : JSON.parse(new TextDecoder().decode(a));
}
function notice(s, error = false) {
  $("#notice").innerHTML = s
    ? `<p class="${error ? "error" : "loading"}">${esc(s)}</p>`
    : "";
}
function heading(s) {
  return `<div class="section-title"><h2>${esc(s)}</h2></div>`;
}
function home() {
  const info = {
    item: ["Equipment, weapons, armor and captured variants", "◇"],
    spell: ["Abilities, effects and preserved spell descriptions", "✧"],
    quest: ["Objectives, rewards and the stories of Azeroth", "⌘"],
    npc: ["Creatures, observed abilities and historical records", "♜"],
    gameobject: ["Objects and community world observations", "⬡"],
    tree: ["Preserved class trees and talent references", "✦"],
  };
  $("#content").innerHTML =
    `${manifest.sample ? '<div class="banner">Development preview: a small real-data sample. The full collection is being built.</div>' : ""}<div class="stats"><div class="stat"><strong>${num(manifest.records)}</strong><small>Searchable source records</small></div><div class="stat"><strong>${num(Object.keys(manifest.kinds).length)}</strong><small>Collections to explore</small></div><div class="stat"><strong>${num(manifest.total_files)}</strong><small>Published files accounted for</small></div><div class="stat"><strong>${num(manifest.recovery.pages)}</strong><small>Recovered historical pages</small></div></div>${heading("Browse the database")}<div class="collections">${Object.entries(
      info,
    )
      .filter(([k]) => manifest.kinds[k])
      .map(
        ([k, [desc, glyph]]) =>
          `<a class="collection" href="#search?kind=${k}"><h3>${esc(label(k))}</h3><p>${desc}</p><span class="count">${num(manifest.kinds[k].records)} source records</span><span class="glyph" aria-hidden="true">${glyph}</span></a>`,
      )
      .join(
        "",
      )}</div>${heading("All collections")}<div class="collection-directory" aria-label="All data collections">${collectionDirectory(manifest.kinds, esc)}</div>${heading("One archive. Distinct sources.")}<div class="source-grid"><article class="source-card"><h3>Captured from the client</h3><p>Published WDB records retain their game modes and capture metadata. Different versions remain separate.</p><a href="#search?source=Client%20captures&kind=item">Explore client captures →</a></article><article class="source-card"><h3>Recovered from the web</h3><p>Exiles DB, BisBeard and archived AscensionDB pages preserve additional descriptions and historical claims.</p><a href="#guides">View recovered pages →</a></article><article class="source-card"><h3>Every file accounted for</h3><p>Structured records are searchable. Binary captures, addon code and supporting files remain available as references.</p><a href="#coverage">Inspect sources & coverage →</a></article></div>`;
}
function table(rows) {
  return `<div class="table-wrap"><table><thead><tr><th>Name / ID</th><th>Collection</th><th>Source</th><th>Game mode</th></tr></thead><tbody>${rows.map((r) => `<tr><td><a href="#record=${esc(r[0])}">${esc(r[1])}</a><small>ID ${esc(r[5])}</small></td><td>${esc(label(r[2]))}</td><td>${esc(r[4])}</td><td><span class="badge">${esc(r[3])}</span></td></tr>`).join("")}</tbody></table></div>`;
}
function startSearch() {
  const q = $("#query").value.trim(),
    kind = $("#kind").value,
    source = $("#source").value,
    mode = $("#mode").value;
  if (q.length === 1 && !/^\d$/.test(q)) {
    notice("Enter at least two letters, or an exact numeric ID.", true);
    return;
  }
  const p = location.hash.startsWith("#search?") ? params() : new URLSearchParams();
  p.delete("page");
  for (const [k, v] of Object.entries({ q, kind, source, mode }))
    if (v) p.set(k, v); else p.delete(k);
  const next = "#search?" + p;
  if (location.hash === next) route();
  else location.hash = next;
}
function runSearch(p) {
  $("#query").value = p.get("q") || "";
  $("#kind").value = p.get("kind") || "";
  $("#source").value = p.get("source") || "";
  $("#mode").value = p.get("mode") || "";
  page = Number(p.get("page") || 0);
  if (!Number.isSafeInteger(page) || page < 0 || page > Math.floor(Number.MAX_SAFE_INTEGER / 50)) page = 0;
  notice("Searching the preserved collection…");
  const id = ++request;
  worker.postMessage({
    id,
    manifest,
    dataBase,
    q: $("#query").value,
    kind: $("#kind").value,
    source: $("#source").value,
    mode: $("#mode").value,
    page,
    ...searchColumnState(p),
  });
}
worker.onmessage = ({ data: d }) => {
  if (d.id !== request) return;
  if (d.error) {
    notice(d.error, true);
    return;
  }
  if (d.progress) {
    notice(`Searching collection parts ${d.progress} / ${d.parts}…`);
    return;
  }
  notice("");
  $("#content").innerHTML =
    `<div class="result-head"><h2>Search results</h2><p class="muted">${num(d.total)} source records · page ${page + 1} of ${Math.max(1, Math.ceil(d.total / 50))}</p></div><p class="muted">A shared ID may have several captures or sources. Open a record to inspect its exact fields.</p>${searchTable(d.rows, params(), manifest)}<div class="pagination"><button id="prev" ${page === 0 ? "disabled" : ""}>Previous</button><button id="next" ${(page + 1) * 50 >= d.total ? "disabled" : ""}>Next</button></div>`;
  bindSearchColumns($("#content"), params());
  for (const [s, delta] of [
    ["#prev", -1],
    ["#next", 1],
  ])
    $(s).onclick = () => {
      const p = new URLSearchParams(location.hash.slice(8));
      p.set("page", page + delta);
      location.hash = "search?" + p;
    };
};
function val(v) {
  if (v === null) return "null";
  if (typeof v === "object")
    return `<pre>${esc(JSON.stringify(v, null, 2))}</pre>`;
  return `<span class="record-text">${esc(v)}</span>`;
}
async function detail(key) {
  const routeId = request;
  if (!/^[a-z0-9]+\/\d+\/\d+$/.test(key)) throw Error("Invalid record link.");
  notice("Reading preserved record…");
  const [fid, part, offset] = key.split("/"),
    f = manifest.files[fid];
  if (!f)
    throw Error(
      "This record belongs to an older snapshot. Search its name or ID in the current catalog.",
    );
  const rows = await gz(`records/${fid}/${part}.json.gz`),
    r = rows[Number(offset)];
  if (routeId !== request) return;
  if (!r) throw Error("Record was not found.");
  notice("");
  const [id, title, kind, mode, source, data] = r;
  let refs = [];
  const collect = (v, depth = 0) => {
    if (depth > 5 || !v || typeof v !== "object") return;
    if (v.type && v.key && v.label) refs.push(v);
    if (Array.isArray(v)) v.forEach((x) => collect(x, depth + 1));
    else Object.values(v).forEach((x) => collect(x, depth + 1));
  };
  collect(data);
  refs = [
    ...new Map(refs.map((x) => [x.type + ":" + x.key, x])).values(),
  ].slice(0, 100);
  $("#content").innerHTML =
    `<p><a href="#" id="back">← Back</a></p><article class="detail"><p class="eyebrow">${esc(label(kind))} · ID ${esc(id)}</p><h1>${esc(title)}</h1><dl><dt>Source</dt><dd>${esc(source)}</dd><dt>Game mode</dt><dd>${esc(mode)}</dd><dt>Published snapshot</dt><dd><a href="https://github.com/hertigservices/ascension-data/commit/${manifest.revision}">${manifest.revision.slice(0, 12)}</a></dd><dt>Original record file</dt><dd>${f.archive_url ? `<a href="${safeUrl(f.archive_url)}">Archived source page ↗</a>` : `<a href="${sourceUrl(f.path)}">${esc(f.path)}</a> · <a href="${rawUrl(f.path)}">Download source ↗</a>`}</dd></dl><div class="banner">${source === "Client captures" ? "This is a preserved capture or published view, not a claim about the latest live game. WDB item definitions do not establish drop locations." : source === "LootCollector" ? "This pin records the player’s position when the loot window opened, not a confirmed source or drop rate." : source === "Addon observations" ? "These are published addon observations or reference data. Unspecified game modes are not inferred." : "This source preserves historical website or planner claims. It remains separate from client-captured evidence."}${kind === "raw-variant" ? " This entry describes an exact retained binary variant; use the corresponding raw pack for its payload." : ""}</div><p><a href="#search?q=${encodeURIComponent(id)}">Find this ID across sources and modes →</a> · <button id="download" type="button">Download this record</button></p>${data.description ? `<p class="record-text">${esc(data.description)}</p>` : ""}${refs.length ? heading("Referenced records") + '<div class="reference-list">' + refs.map((v) => `<a href="#search?q=${encodeURIComponent(v.key)}&kind=${encodeURIComponent(v.type === "creature" ? "npc" : v.type)}">${esc(v.label)}</a>`).join("") + "</div>" : ""}${heading("Preserved fields")}<div class="table-wrap"><table><tbody>${Object.entries(
      data,
    )
      .map(([k, v]) => `<tr><td>${esc(k)}</td><td>${val(v)}</td></tr>`)
      .join("")}</tbody></table></div></article>`;
  const mapLinks = await atlasRecordLinks(key, manifest, gz);
  if (routeId !== request) return;
  if (mapLinks.length) {
    const box = document.createElement("p");
    box.className = "record-map-links";
    box.innerHTML = mapLinks.map(z => `<a class="map-link" href="#atlas?zone=${encodeURIComponent(z.key)}&record=${encodeURIComponent(key)}">Show on map: ${esc(z.label)} →</a>`).join(" ");
    $("#download").parentElement.after(box);
  }
  $("#back").onclick = (e) => {
    e.preventDefault();
    history.length > 1 ? history.back() : (location.hash = "");
  };
  $("#download").onclick = () => {
    const u = URL.createObjectURL(
      new Blob(
        [
          JSON.stringify(
            {
              source,
              mode,
              snapshot: manifest.revision,
              file: f.path,
              record: data,
            },
            null,
            2,
          ),
        ],
        { type: "application/json" },
      ),
    );
    const a = document.createElement("a");
    a.href = u;
    a.download = "ascension-record.json";
    a.click();
    setTimeout(() => URL.revokeObjectURL(u), 1000);
  };
}
async function showCoverage() {
  const routeId = request;
  notice("Reading source coverage…");
  coverage ||= await gz("coverage.json.gz");
  if (routeId !== request) return;
  notice("");
  $("#content").innerHTML =
    `${heading("Sources & coverage")}<p>Every tracked file in snapshot <a href="https://github.com/hertigservices/ascension-data/commit/${manifest.revision}">${manifest.revision.slice(0, 12)}</a> is listed here. ${num(manifest.indexed_files)} files supply searchable records; ${num(manifest.reference_files)} remain downloadable references. Counts are source records, not unique game entities.</p><div class="banner">Coverage means coverage of this published repository, not the entire game or every original upload. Unknown formats remain references; a new structured-file parsing error stops publication. Binary variants and addon source files are preserved without inventing decoded facts.</div><p><a href="${esc(dataBase)}coverage.json.gz">Download full report →</a></p><div id="file-results"></div>`;
  renderCoverage({root:$("#file-results"),coverage,manifest,sourceUrl,rawUrl,esc,params:new URLSearchParams(location.hash.split("?")[1] || "")});
}
async function guides() {
  const routeId = request;
  notice("Reading recovery inventory…");
  recovery ||= await gz("recovery.json.gz");
  if (routeId !== request) return;
  notice("");
  const rows = Object.entries(manifest.browse)
    .filter(([k]) => ["guide", "recovered-page"].includes(k))
    .flatMap(([, r]) => r);
  $("#content").innerHTML =
    `${heading("Recovered AscensionDB pages")}<p>Historical pages recovered from the Internet Archive. Downloaded scripts are not run on this site, and linked pages are not counted as recovered unless their content was retrieved.</p><div class="banner">${num(recovery.results?.length || 0)} retrieval attempts · ${num(recovery.records?.length || 0)} readable pages · ${num(recovery.inventory?.length || 0)} indexed archive URLs. This is a bounded recovery inventory, not a complete mirror.</div><div class="guide-grid">${rows.map((r) => `<a class="collection" href="#record=${r[0]}"><h3>${esc(r[1])}</h3><p>Read preserved text and inspect its source</p></a>`).join("")}</div>${heading("Recovery results")}<div class="table-wrap"><table><thead><tr><th>Original resource</th><th>Result</th><th>Evidence</th></tr></thead><tbody>${(recovery.results || []).map((r) => `<tr><td>${esc(r.source)}</td><td>${esc(r.status)}</td><td>${r.status === "recovered" ? `<a href="${safeUrl(r.snapshot)}">Archive capture ↗</a><small>${num(r.bytes)} bytes · SHA-256 ${esc(r.sha256)}</small>` : esc(r.error)}</td></tr>`).join("")}</tbody></table></div>`;
}
async function route() {
  if (!manifest) return;
  request++;
  closeAtlas();
  worker.postMessage({ cancel: true });
  notice("");
  const h = location.hash;
  $(".intro").hidden=h.startsWith("#atlas");
  $("#search").hidden=h.startsWith("#atlas");
  document
    .querySelectorAll("[data-nav]")
    .forEach((a) =>
      a.classList.toggle(
        "active",
        a.dataset.nav ===
          (h.startsWith("#atlas") ? "atlas" : h.startsWith("#coverage")
            ? "coverage"
            : h === "#guides"
              ? "guides"
              : "home"),
      ),
    );
  try {
    if (h.startsWith("#record=")) await detail(decodeURIComponent(h.slice(8)));
    else if (h.startsWith("#atlas")) await showAtlas({root: $("#content"), manifest, gz, notice, esc, label, params: new URLSearchParams(h.split("?")[1] || "")});
    else if (h.startsWith("#coverage")) await showCoverage();
    else if (h === "#guides") await guides();
    else if (h.startsWith("#search?"))
      runSearch(new URLSearchParams(h.slice(8)));
    else home();
  } catch (e) {
    notice(e.message, true);
  }
}
$("#search").onsubmit = (e) => {
  e.preventDefault();
  startSearch();
};
for (const id of ["kind", "source", "mode"]) $("#" + id).onchange = startSearch;
addEventListener("hashchange", route);
try {
  const config = await fetch("config.json").then((r) => (r.ok ? r.json() : {}));
  dataBase = config.dataBase || "";
  const r = await fetch(dataBase + "manifest.json", { cache: "no-cache" });
  if (!r.ok)
    throw Error("The catalog is unavailable. Please try again shortly.");
  manifest = await r.json();
  for (const [key, entry] of Object.entries(manifest.kinds)) entry.label = collectionLabel(key, entry);
  manifest.modes = [...new Set(manifest.modes.flatMap(m=>m.split(/[,|]/).map(s=>s.trim()).filter(Boolean)))].sort();
  for (const [k, v] of Object.entries(manifest.kinds).sort((a, b) =>
    a[1].label.localeCompare(b[1].label),
  )) {
    const o = new Option(v.label + " (" + num(v.records) + ")", k);
    $("#kind").add(o);
  }
  $("#source").replaceChildren(new Option("All sources", ""));
  for (const k of Object.keys(manifest.sources).sort()) $("#source").add(new Option(k,k));
  for (const k of manifest.modes) $("#mode").add(new Option(k, k));
  $("#snapshot").innerHTML =
    `Data snapshot <a href="https://github.com/hertigservices/ascension-data/commit/${manifest.revision}">${manifest.revision.slice(0, 8)}</a><br>Built ${esc(new Date(manifest.built_at).toLocaleString())}<br><a href="https://github.com/hertigservices/Ascension_preservation/tree/main/tools/ascension-db">Inspect how this archive works ↗</a>`;
  await route();
} catch (e) {
  notice(e.message, true);
}

// Optional browser-agent access uses the same visible search and result renderer.
const modelContext = document.modelContext;
if (manifest && modelContext?.registerTool) {
  const lifecycle = new AbortController();
  addEventListener("pagehide", () => lifecycle.abort(), { once: true });
  try {
    Promise.resolve(
      modelContext.registerTool(
        {
          name: "search_archive",
          title: "Search the Ascension archive",
          description:
            "Search preserved source records by name prefix or exact numeric ID, update the visible results, and return the first page. Results are attributed historical evidence, not verified live-game facts.",
          inputSchema: {
            type: "object",
            properties: {
              query: { type: "string", maxLength: 200 },
              collection: { type: "string" },
              source: { type: "string" },
              mode: { type: "string" },
            },
            required: ["query"],
            additionalProperties: false,
          },
          annotations: { readOnlyHint: false, untrustedContentHint: true },
          execute(input) {
            if (
              !input ||
              typeof input.query !== "string" ||
              input.query.length > 200 ||
              (input.query.trim().length === 1 &&
                !/^\d$/.test(input.query.trim()))
            )
              throw Error(
                "Provide at least two letters or an exact numeric ID.",
              );
            if (input.collection && !manifest.kinds[input.collection])
              throw Error("Unknown collection.");
            if (input.source && !manifest.sources[input.source])
              throw Error("Unknown source.");
            if (input.mode && !manifest.modes.includes(input.mode))
              throw Error("Unknown game mode.");
            const p = new URLSearchParams({ q: input.query.trim() });
            for (const [key, value] of [
              ["kind", input.collection],
              ["source", input.source],
              ["mode", input.mode],
            ])
              if (value) p.set(key, value);
            history.replaceState(null, "", "#search?" + p);
            worker.postMessage({ cancel: true });
            const id = request + 1;
            return new Promise((resolve, reject) => {
              const finish = () => {
                clearTimeout(timeout);
                worker.removeEventListener("message", listener);
              };
              const listener = ({ data }) => {
                if (data.id !== id || data.progress) return;
                finish();
                if (data.error) reject(Error(data.error));
                else
                  resolve({
                    total: data.total,
                    snapshot: manifest.revision,
                    records: data.rows.map((r) => ({
                      name: r[1],
                      collection: r[2],
                      mode: r[3],
                      source: r[4],
                      id: r[5],
                      link: "#record=" + r[0],
                    })),
                  });
              };
              const timeout = setTimeout(() => {
                finish();
                reject(
                  Error(
                    "Search is still loading; inspect the visible progress.",
                  ),
                );
              }, 120000);
              worker.addEventListener("message", listener);
              runSearch(p);
            });
          },
        },
        { signal: lifecycle.signal },
      ),
    ).catch(() => {
      /* Unsupported implementations leave normal browsing available. */
    });
  } catch {
    /* A partial implementation must not break normal browsing. */
  }
}
