var __defProp = Object.defineProperty;
var __name = (target, value) => __defProp(target, "name", { value, configurable: true });

// worker.mjs
var safe = /* @__PURE__ */ __name((s) => /^[a-zA-Z0-9_./-]+$/.test(s) && !s.split("/").some((p) => p === ".." || p === "."), "safe");
var imageKey = /* @__PURE__ */ __name((s) => typeof s === "string" && !s.includes("\\") && !/[\u0000-\u001f\u007f]/.test(s) && !s.split("/").some((p) => !p || p === "." || p === "..") && /^images\/.+\.(png|jpe?g|webp|gif|avif|svg)$/i.test(s), "imageKey");
var valid = /* @__PURE__ */ __name((s) => safe(s) && /^(catalog|media)\/(current\.json|snapshots\/[0-9a-f]{64}\/.+)$/.test(s), "valid");
var publicHeaders = { "Access-Control-Allow-Origin": "*", "X-Content-Type-Options": "nosniff" };
var readCache = /* @__PURE__ */ new WeakMap();
async function cachedJson(bucket, key) {
  let cache = readCache.get(bucket);
  if (!cache) {
    cache = /* @__PURE__ */ new Map();
    readCache.set(bucket, cache);
  }
  const hit = cache.get(key);
  if (hit && hit.until > Date.now()) return hit.value;
  const object = await bucket.get(key);
  if (object && object.size > 1024 * 1024) throw Error("Catalog index exceeds the read limit");
  const value = object ? await object.json() : null;
  if (cache.size >= 128) cache.delete(cache.keys().next().value);
  cache.set(key, { value, until: Date.now() + (value ? 6e4 : 1e3) });
  return value;
}
__name(cachedJson, "cachedJson");
var hex = /* @__PURE__ */ __name((bytes) => Array.from(new Uint8Array(bytes), (v) => v.toString(16).padStart(2, "0")).join(""), "hex");
var physical = /* @__PURE__ */ __name((s) => typeof s === "string" && safe(s) && (/^catalog\/objects\/[a-f0-9]{64}$/.test(s) || /^catalog\/snapshots\/[a-f0-9]{64}\/.+/.test(s)), "physical");
var logicalType = /* @__PURE__ */ __name((key) => key.endsWith(".gz") ? "application/gzip" : key.endsWith(".json") ? "application/json" : key.endsWith(".js") ? "text/javascript" : key.endsWith(".css") ? "text/css" : key.endsWith(".html") ? "text/html" : "application/octet-stream", "logicalType");
async function publicObject(bucket, key, head) {
  const match = /^catalog\/snapshots\/([a-f0-9]{64})\/(.+)$/.exec(key);
  if (!match) return { object: await (head ? bucket.head(key) : bucket.get(key)) };
  const base = `catalog/snapshots/${match[1]}/`, name = match[2];
  const layout = await cachedJson(bucket, base + "layout.json");
  if (!layout) return { object: await (head ? bucket.head(key) : bucket.get(key)) };
  if (layout.schema !== "ascension-live-layout-1" || layout.snapshot !== match[1]) throw Error("Invalid catalog layout");
  if (name === "storage-manifest.json") return { object: await (head ? bucket.head(key) : bucket.get(key)) };
  const shard = hex(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(name))).slice(0, 2);
  if (!layout.shards?.[shard]) return { object: null };
  const index = await cachedJson(bucket, base + "_index/" + shard + ".json"), entry = index?.[name];
  if (!entry || !physical(entry.key)) return { object: null };
  const object = await (head ? bucket.head(entry.key) : bucket.get(entry.key));
  if (object && (object.size !== entry.bytes || object.customMetadata?.sha256 && object.customMetadata.sha256 !== entry.sha256)) throw Error("Catalog object differs");
  return { object, type: logicalType(name), sha: entry.sha256 };
}
__name(publicObject, "publicObject");
async function authorized(request, env) {
  if (!env.PUBLISH_TOKEN) return false;
  const hash = /* @__PURE__ */ __name(async (s) => new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s))), "hash");
  const a = await hash(request.headers.get("Authorization") || ""), b = await hash("Bearer " + env.PUBLISH_TOKEN);
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
  return diff === 0;
}
__name(authorized, "authorized");
var worker_default = {
  async fetch(request, env) {
    const url = new URL(request.url);
    let key;
    try {
      key = decodeURIComponent(url.pathname.slice(1));
    } catch {
      return new Response("Invalid path", { status: 400 });
    }
    const admin = key.startsWith("_publish/");
    if (admin && env.LEGACY_PUBLICATION_ENABLED !== "true") return new Response("Publication is managed by the local backup controller", { status: 405 });
    if (admin) {
      if (!await authorized(request, env)) return new Response("Unauthorized", { status: 401 });
      key = key.slice(9);
    }
    if (admin && key === "batch" && request.method === "POST") {
      const size = Number(request.headers.get("Content-Length"));
      if (!Number.isSafeInteger(size) || size <= 0 || size > 5 * 1024 * 1024) return new Response("Invalid batch size", { status: 400 });
      const form = await request.formData();
      let entries;
      try {
        entries = JSON.parse(String(form.get("metadata")));
      } catch {
        return new Response("Invalid batch", { status: 400 });
      }
      if (!Array.isArray(entries) || !entries.length || entries.length > 8) return new Response("Invalid batch", { status: 400 });
      for (let i = 0; i < entries.length; i++) {
        const e = entries[i], file = form.get("file" + i);
        if (!valid(e.key) || e.key.endsWith("/current.json") || !Number.isSafeInteger(e.bytes) || e.bytes < 0 || !file || file.size !== e.bytes || !/^[0-9a-f]{64}$/.test(e.sha256)) return new Response("Invalid member", { status: 400 });
      }
      const result = [];
      for (let i = 0; i < entries.length; i++) {
        const e = entries[i];
        let object2 = await env.PUBLIC_DATA.put(e.key, await form.get("file" + i).arrayBuffer(), { sha256: e.sha256, onlyIf: new Headers({ "If-None-Match": "*" }), httpMetadata: { contentType: e.contentType || "application/octet-stream", cacheControl: "public,max-age=31536000,immutable" }, customMetadata: { sha256: e.sha256 } });
        if (!object2) {
          object2 = await env.PUBLIC_DATA.head(e.key);
          if (!object2 || object2.size !== e.bytes || object2.customMetadata?.sha256 !== e.sha256) return new Response("Immutable object differs", { status: 409 });
        }
        result.push({ key: e.key, sha256: e.sha256, bytes: e.bytes });
      }
      return Response.json(result);
    }
    const headers = admin ? { "X-Content-Type-Options": "nosniff" } : publicHeaders;
    if (!(valid(key) || !admin && imageKey(key))) return new Response("Not found", { status: 404, headers });
    if (admin && request.method === "PUT") {
      const size = Number(request.headers.get("Content-Length")), sha = request.headers.get("X-Object-Sha256") || "";
      if (!Number.isSafeInteger(size) || size < 0 || size > 64 * 1024 * 1024 || !/^[0-9a-f]{64}$/.test(sha)) return new Response("Invalid upload", { status: 400 });
      let body = request.body;
      if (key.endsWith("/current.json")) {
        if (size > 4096) return new Response("Invalid pointer", { status: 400 });
        const bytes = await request.arrayBuffer();
        let current;
        try {
          current = JSON.parse(new TextDecoder().decode(bytes));
        } catch {
          return new Response("Invalid pointer", { status: 400 });
        }
        const channel = key.split("/")[0];
        if (current.schema !== "ascension-current-1" || !/^[0-9a-f]{64}$/.test(current.snapshot) || current.prefix !== `${channel}/snapshots/${current.snapshot}/`) return new Response("Invalid pointer", { status: 400 });
        if (!await env.PUBLIC_DATA.head(current.prefix + "storage-manifest.json")) return new Response("Incomplete snapshot", { status: 409 });
        if (!request.headers.has("If-Match") && !request.headers.has("If-None-Match")) return new Response("Conditional update required", { status: 428 });
        body = bytes;
      } else if (request.headers.get("If-None-Match") !== "*") return new Response("Immutable writes required", { status: 428 });
      const object2 = await env.PUBLIC_DATA.put(key, body, { sha256: sha, onlyIf: request.headers, httpMetadata: { contentType: request.headers.get("Content-Type") || "application/octet-stream", cacheControl: key.endsWith("/current.json") ? "no-store" : "public,max-age=31536000,immutable" }, customMetadata: { sha256: sha } });
      if (!object2) return new Response("Publication changed; retry from current state", { status: 412 });
      return Response.json({ ETag: object2.httpEtag });
    }
    if (!["GET", "HEAD"].includes(request.method)) return new Response("Read only", { status: 405, headers: { ...headers, Allow: "GET, HEAD" } });
    let resolved;
    try {
      resolved = await publicObject(env.PUBLIC_DATA, key, request.method === "HEAD");
    } catch {
      return new Response("Catalog unavailable; please refresh", { status: 503, headers });
    }
    const object = resolved.object;
    if (!object) return new Response("This catalog has been replaced. Refresh to load the current catalog.", { status: key.startsWith("catalog/snapshots/") ? 410 : 404, headers });
    const h = new Headers(headers);
    object.writeHttpMetadata(h);
    h.set("ETag", object.httpEtag);
    h.set("X-Object-Sha256", object.customMetadata?.sha256 || "");
    if (resolved.type) h.set("Content-Type", resolved.type);
    if (resolved.sha) h.set("X-Object-Sha256", resolved.sha);
    if (key.toLowerCase().endsWith(".svg")) h.set("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; sandbox");
    h.delete("Content-Encoding");
    h.set("Cache-Control", admin || key.endsWith("/current.json") ? "no-store" : "public, max-age=31536000, immutable");
    if (request.headers.get("If-None-Match") === object.httpEtag) return new Response(null, { status: 304, headers: h });
    h.set("Content-Length", String(object.size));
    return new Response(request.method === "HEAD" ? null : object.body, { headers: h });
  }
};
export {
  worker_default as default
};
//# sourceMappingURL=worker.js.map
