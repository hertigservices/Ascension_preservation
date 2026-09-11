import {
  FILE_LIMIT,
  TOTAL_LIMIT,
  POLICY_VERSION,
  validateManifest,
  sanitize,
  sha256,
  requiresReview,
} from "../shared/policy.mjs";
const HEADERS = {
  "Cache-Control": "no-store",
  "Referrer-Policy": "no-referrer",
  "X-Content-Type-Options": "nosniff",
};
export const json = (body, status = 200) =>
  Response.json(body, { status, headers: HEADERS });
export class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}
const fail = (s, m) => {
  throw new HttpError(s, m);
};
const now = () => Math.floor(Date.now() / 1000);
const random = () =>
  [...crypto.getRandomValues(new Uint8Array(32))]
    .map((v) => v.toString(16).padStart(2, "0"))
    .join("");
const bearer = (r) =>
  (r.headers.get("authorization") || "").replace(/^Bearer /, "");
const query = (env, sql, ...args) => env.DB.prepare(sql).bind(...args);
async function smallBody(request, limit) {
  const reader = request.body?.getReader();
  if (!reader) fail(400, "Missing request body");
  let length = 0,
    chunks = [];
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    length += value.length;
    if (length > limit) {
      await reader.cancel();
      fail(413, "Upload is larger than allowed");
    }
    chunks.push(value);
  }
  const result = new Uint8Array(length);
  let p = 0;
  for (const c of chunks) {
    result.set(c, p);
    p += c.length;
  }
  return result;
}
async function bodyJson(r) {
  try {
    return JSON.parse(new TextDecoder().decode(await smallBody(r, 100000)));
  } catch (e) {
    if (e instanceof HttpError) throw e;
    fail(400, "Invalid request");
  }
}
function enabled(env) {
  return (
    env.UPLOADS_ENABLED === "true" &&
    !!env.TURNSTILE_SITE_KEY &&
    !!env.TURNSTILE_SECRET &&
    !!env.COLLECTOR_TOKEN &&
    !!env.RATE_SECRET &&
    !!env.ALLOWED_ORIGIN
  );
}
function origin(r, env) {
  const expected = env.ALLOWED_ORIGIN;
  if (!expected || r.headers.get("origin") !== expected)
    fail(403, "Upload origin is not allowed");
}
async function admin(r, env) {
  if (
    !env.COLLECTOR_TOKEN ||
    (await sha256(new TextEncoder().encode(bearer(r)))) !==
      (await sha256(new TextEncoder().encode(env.COLLECTOR_TOKEN)))
  )
    fail(401, "Collector authentication required");
}
async function rowFor(r, env, id, kind) {
  if (!/^[a-f0-9-]{36}$/.test(id)) fail(404, "Receipt not found");
  const row = await query(
    env,
    "SELECT * FROM submissions WHERE id = ?",
    id,
  ).first();
  if (
    !row ||
    (await sha256(new TextEncoder().encode(bearer(r)))) !== row[kind + "_hash"]
  )
    fail(404, "Receipt not found");
  if (row.expires < now()) fail(410, "This submission has expired");
  return row;
}
export async function handle(r, env, path) {
  try {
    if (path === "config" && r.method === "GET")
      return json({
        enabled: enabled(env),
        siteKey: env.TURNSTILE_SITE_KEY || null,
        policy: POLICY_VERSION,
        fileLimit: FILE_LIMIT,
        totalLimit: TOTAL_LIMIT,
        retentionDays: 7,
      });
    if (!env.DB || !env.BUCKET)
      fail(
        503,
        "The contribution service is not configured yet. Your files remain on your device.",
      );
    if (path === "submissions" && r.method === "POST") {
      origin(r, env);
      if (!enabled(env))
        fail(
          503,
          "Contributions are temporarily paused. Your files remain on your device.",
        );
      const b = await bodyJson(r);
      let manifest;
      try {
        manifest = validateManifest(b);
      } catch (e) {
        fail(400, e.message);
      }
      if (typeof b.turnstile !== "string" || b.turnstile.length > 2048)
        fail(400, "Please complete verification");
      const verification = await fetch(
        "https://challenges.cloudflare.com/turnstile/v0/siteverify",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            secret: env.TURNSTILE_SECRET,
            response: b.turnstile,
            remoteip: r.headers.get("cf-connecting-ip") || undefined,
          }),
          signal: AbortSignal.timeout(15000),
        },
      );
      if (!verification.ok)
        fail(503, "Verification is temporarily unavailable");
      const verified = await verification.json();
      if (
        !verified.success ||
        verified.hostname !== new URL(env.ALLOWED_ORIGIN).hostname ||
        verified.action !== "contribute"
      )
        fail(403, "Verification expired. Please try again.");
      const key = await crypto.subtle.importKey(
        "raw",
        new TextEncoder().encode(env.RATE_SECRET),
        { name: "HMAC", hash: "SHA-256" },
        false,
        ["sign"],
      );
      const ipHash = [
        ...new Uint8Array(
          await crypto.subtle.sign(
            "HMAC",
            key,
            new TextEncoder().encode(
              (r.headers.get("cf-connecting-ip") || "unknown") +
                ":" +
                Math.floor(now() / 86400),
            ),
          ),
        ),
      ]
        .map((v) => v.toString(16).padStart(2, "0"))
        .join("");
      const id = crypto.randomUUID(),
        uploadToken = random(),
        receiptToken = random(),
        t = now();
      // One conditional INSERT serializes admission and capacity reservation in D1.
      const result = await query(
        env,
        `INSERT INTO submissions (id,upload_hash,receipt_hash,ip_hash,manifest,bytes,created,expires,status,lease_until,attempts)
   SELECT ?,?,?,?,?,?,?,?,'uploading',0,0 WHERE
   (SELECT count(*) FROM submissions WHERE ip_hash=? AND created>?)<5 AND
   (SELECT count(*) FROM submissions WHERE created>?)<100 AND
   (SELECT coalesce(sum(bytes),0) FROM submissions) + ? <= 2147483648`,
        id,
        await sha256(new TextEncoder().encode(uploadToken)),
        await sha256(new TextEncoder().encode(receiptToken)),
        ipHash,
        JSON.stringify(manifest),
        manifest.total,
        t,
        t + 7 * 86400,
        ipHash,
        t - 86400,
        t - 86400,
        manifest.total,
      ).run();
      if (!result.meta.changes)
        fail(
          429,
          "The contribution queue is full or your daily limit is reached. Please try later.",
        );
      return json(
        { id, uploadToken, receiptToken, expires: t + 7 * 86400 },
        201,
      );
    }
    const m = path.match(
      /^submissions\/([a-f0-9-]+)(?:\/(files\/([0-9]+)|complete|status))?$/,
    );
    if (m) {
      const id = m[1],
        action = m[2] || "",
        row = await rowFor(
          r,
          env,
          id,
          action === "status" ? "receipt" : "upload",
        );
      const manifest = JSON.parse(row.manifest);
      if (action === "status" && r.method === "GET")
        return json({
          id,
          status: row.status,
          files: manifest.files.length,
          reviewFiles: manifest.files.filter((f) => requiresReview(f.name))
            .length,
          commit: row.commit_sha || null,
          message: row.message || null,
          expires: row.expires,
        });
      if (action.startsWith("files/") && r.method === "PUT") {
        origin(r, env);
        if (row.status !== "uploading")
          fail(409, "Submission is already complete");
        const f = manifest.files[Number(m[3])];
        if (!f) fail(404, "File not found");
        const data = await smallBody(r, FILE_LIMIT);
        if (data.length !== f.size || (await sha256(data)) !== f.sha256)
          fail(400, "File checksum does not match");
        let clean;
        try {
          clean = sanitize(f.name, data);
        } catch (e) {
          fail(422, e.message);
        }
        if ((await sha256(clean.data)) !== f.sha256)
          fail(422, "Privacy filtering must be applied before upload");
        await env.BUCKET.put(`${id}/${f.index}`, data, {
          httpMetadata: { contentType: "application/octet-stream" },
          customMetadata: { sha256: f.sha256 },
        });
        return json({ received: true });
      }
      if (action === "complete" && r.method === "POST") {
        origin(r, env);
        if (row.status !== "uploading") return json({ status: row.status });
        for (const f of manifest.files) {
          const object = await env.BUCKET.head(`${id}/${f.index}`);
          if (
            !object ||
            object.size !== f.size ||
            object.customMetadata?.sha256 !== f.sha256
          )
            fail(409, "Some files have not finished uploading");
        }
        await query(
          env,
          "UPDATE submissions SET status='received' WHERE id=? AND status='uploading'",
          id,
        ).run();
        return json({ status: "received" });
      }
    }
    if (path.startsWith("collector/")) {
      await admin(r, env);
      if (path === "collector/status" && r.method === "GET") {
        const data=await query(env,"SELECT id,status,bytes,created,commit_sha,manifest FROM submissions ORDER BY created DESC LIMIT 200").all();
        return json({submissions:data.results.map(({manifest,...row})=>({...row,modes:[...new Set(JSON.parse(manifest).files.map(file=>file.mode))]}))});
      }
      if (path === "collector/claim" && r.method === "POST") {
        const lease = random(),
          t = now();
        const row = await query(
          env,
          `UPDATE submissions SET status='processing',lease=?,lease_until=?,attempts=attempts+1 WHERE id=(SELECT id FROM submissions WHERE (status='received' OR (status='processing' AND lease_until<?)) AND lease_until<? AND expires>? AND attempts<5 ORDER BY created LIMIT 1) RETURNING id,manifest,lease,expires`,
          lease,
          t + 7200,
          t,
          t,
          t,
        ).first();
        return json({
          submission: row
            ? { ...row, manifest: JSON.parse(row.manifest) }
            : null,
        });
      }
      const cm = path.match(
        /^collector\/([a-f0-9-]+)\/(files\/([0-9]+)|ack|renew|finalize)$/,
      );
      if (cm) {
        const row = await query(
          env,
          "SELECT * FROM submissions WHERE id=?",
          cm[1],
        ).first();
        // An authenticated collector may reconcile a proven publication after
        // its final lease expired. It cannot take over a live claim or release
        // a deliberate review hold. Bind proof to this exact stored manifest.
        if (cm[2] === "finalize" && r.method === "POST") {
          if (!row) fail(404, "Submission expired");
          const b = await bodyJson(r);
          if (!["published", "needs_review"].includes(b.status) || !/^[a-f0-9]{40}$/.test(b.commit || "") || !/^[a-f0-9]{64}$/.test(b.bundle || "") || !/^[a-f0-9]{64}$/.test(b.revision || "")) fail(400, "Invalid publication proof");
          const manifest = JSON.parse(row.manifest);
          const parts = manifest.files.map(f => [f.name,f.mode,f.size,f.sha256]).sort((a,b) => {
            for (let i=0;i<a.length;i++) {if(a[i]<b[i])return -1;if(a[i]>b[i])return 1;}return 0;
          });
          const key = await sha256(new TextEncoder().encode(JSON.stringify([b.revision,manifest.policy,parts])));
          if (key !== b.bundle) fail(400, "Publication proof does not match submission");
          if (row.status === b.status && row.commit_sha === b.commit) return json({saved:true});
          const saved = await query(env,
            "UPDATE submissions SET status=?,commit_sha=?,message=?,lease=NULL,lease_until=0 WHERE id=? AND commit_sha IS NULL AND (status='received' OR (status='processing' AND lease_until<?) OR (status='needs_review' AND attempts>=5))",
            b.status,b.commit,b.status === "published" ? "Validated game data has been published." : "Accepted data was published; some files require maintainer review.",row.id,now()).run();
          if (saved.meta.changes !== 1) fail(409, "Submission is owned or held for review");
          return json({saved:true});
        }
        if (
          !row ||
          row.status !== "processing" ||
          row.lease !== r.headers.get("x-lease") ||
          row.lease_until < now()
        )
          fail(409, "Lease expired");
        if (cm[2].startsWith("files/") && r.method === "GET") {
          const f = JSON.parse(row.manifest).files[Number(cm[3])];
          if (!f) fail(404, "File not found");
          const obj = await env.BUCKET.get(`${row.id}/${f.index}`);
          if (!obj) fail(404, "File not found");
          return new Response(obj.body, {
            headers: {
              ...HEADERS,
              "Content-Type": "application/octet-stream",
              "Content-Length": String(obj.size),
            },
          });
        }
        if (cm[2] === "renew" && r.method === "POST") {
          const renewed = await query(
            env,
            "UPDATE submissions SET lease_until=? WHERE id=? AND lease=? AND status='processing' AND lease_until>=?",
            now() + 7200,
            row.id,
            row.lease,
            now(),
          ).run();
          if (renewed.meta.changes !== 1) fail(409, "Lease expired");
          return json({ renewed: true });
        }
        if (cm[2] === "ack" && r.method === "POST") {
          const b = await bodyJson(r);
          if (!["published", "needs_review", "received"].includes(b.status))
            fail(400, "Invalid result");
          if (
            (b.status === "published" || b.commit) &&
            !/^[a-f0-9]{40}$/.test(b.commit || "")
          )
            fail(400, "Publication commit required");
          const message =
            b.status === "published"
              ? (b.duplicate === true ? "This exact contribution is already included in the published archive." : "Validated game data has been published.")
              : b.status === "needs_review"
                ? "Some files require maintainer review."
                : "Processing will retry automatically.";
          const saved = await query(
            env,
            "UPDATE submissions SET status=?,commit_sha=?,message=?,lease=NULL,lease_until=? WHERE id=? AND lease=? AND status='processing' AND lease_until>=?",
            b.status,
            b.commit || null,
            message,
            b.status === "received" ? now() + 300 : 0,
            row.id,
            row.lease,
            now(),
          ).run();
          if (saved.meta.changes !== 1) fail(409, "Lease expired");
          return json({ saved: true });
        }
      }
      if (path === "collector/cleanup" && r.method === "POST") {
        // Keep the DB reservation until every object is deleted. A retry is safe.
        const rows = await query(
          env,
          "SELECT id,manifest FROM submissions WHERE expires < ? LIMIT 20",
          now(),
        ).all();
        for (const row of rows.results) {
          for (const f of JSON.parse(row.manifest).files)
            await env.BUCKET.delete(`${row.id}/${f.index}`);
          await query(env, "DELETE FROM submissions WHERE id=?", row.id).run();
        }
        await query(
          env,
          "UPDATE submissions SET status='needs_review',message='Processing requires maintainer attention.' WHERE attempts>=5 AND (status='received' OR (status='processing' AND lease_until<?))",
          now(),
        ).run();
        return json({ removed: rows.results.length });
      }
    }
    fail(404, "Not found");
  } catch (e) {
    if (e instanceof HttpError) return json({ error: e.message }, e.status);
    console.error(
      "Contribution service failure",
      e instanceof Error ? e.name : "unknown",
    );
    return json(
      {
        error:
          "The contribution service is temporarily unavailable. Please try again.",
      },
      503,
    );
  }
}
