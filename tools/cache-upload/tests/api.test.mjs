import test from "node:test";
import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import { readFileSync } from "node:fs";
import { handle } from "../lib/intake-api.mjs";
import { sha256, bytes } from "../shared/policy.mjs";
const wdb = () => {
  const b = new Uint8Array(44),
    v = new DataView(b.buffer);
  b.set(bytes("BDIW"));
  v.setUint32(4, 12340, true);
  b.set(bytes("SUne"), 8);
  v.setUint32(24, 123, true);
  v.setUint32(28, 4, true);
  return b;
};
import { environment } from "./storage.mjs";
function request(path, method = "GET", body = null, token = "", headers = {}) {
  return new Request("https://upload.example/api/" + path, {
    method,
    headers: {
      Origin: "https://upload.example",
      Authorization: "Bearer " + token,
      ...headers,
    },
    body:
      body === null
        ? undefined
        : body instanceof Uint8Array
          ? body
          : JSON.stringify(body),
  });
}
async function call(
  env,
  path,
  method = "GET",
  body = null,
  token = "",
  headers = {},
) {
  const response = await handle(
    request(path, method, body, token, headers),
    env,
    path,
  );
  return { code: response.status, body: await response.json() };
}
async function create(env) {
  const data = wdb();
  return (
    await call(env, "submissions", "POST", {
      policy: 1,
      consent: true,
      turnstile: "ok",
      files: [
        {
          name: "itemcache.wdb",
          size: data.length,
          sha256: await sha256(data),
          mode: "unknown",
        },
      ],
    })
  ).body;
}
const oldFetch = globalThis.fetch;
globalThis.fetch = async () =>
  Response.json({
    success: true,
    hostname: "upload.example",
    action: "contribute",
  });
test.after(() => {
  globalThis.fetch = oldFetch;
});
test("closed service fails safely without creating submissions", async () => {
  const env = environment();
  env.UPLOADS_ENABLED = "false";
  const r = await call(env, "submissions", "POST", {});
  assert.equal(r.code, 503);
  assert.equal(env.db.prepare("select count(*) n from submissions").get().n, 0);
});
test("full lifecycle: auth, checksum, retry, completion, exclusive claim, remote result", async () => {
  const env = environment(),
    s = await create(env);
  assert.ok(s.id);
  assert.equal(
    (await call(env, `submissions/${s.id}/status`, "GET", null, "wrong")).code,
    404,
  );
  assert.equal(
    (await call(env, `submissions/${s.id}/status`, "GET", null, s.uploadToken))
      .code,
    404,
  );
  assert.equal(
    (await call(env, `submissions/${s.id}/complete`, "POST", {}, s.uploadToken))
      .code,
    409,
  );
  assert.equal(
    (
      await call(
        env,
        `submissions/${s.id}/files/0`,
        "PUT",
        bytes("bad"),
        s.uploadToken,
      )
    ).code,
    400,
  );
  assert.equal(
    (
      await call(
        env,
        `submissions/${s.id}/files/0`,
        "PUT",
        wdb(),
        s.uploadToken,
      )
    ).code,
    200,
  );
  assert.equal(
    (
      await call(
        env,
        `submissions/${s.id}/files/0`,
        "PUT",
        wdb(),
        s.uploadToken,
      )
    ).code,
    200,
  );
  assert.equal(
    (await call(env, `submissions/${s.id}/complete`, "POST", {}, s.uploadToken))
      .body.status,
    "received",
  );
  assert.equal(
    (await call(env, "collector/claim", "POST", {}, "wrong")).code,
    401,
  );
  const claim = (
    await call(env, "collector/claim", "POST", {}, env.COLLECTOR_TOKEN)
  ).body.submission;
  assert.equal(claim.id, s.id);
  assert.equal(
    (await call(env, "collector/claim", "POST", {}, env.COLLECTOR_TOKEN)).body
      .submission,
    null,
  );
  assert.equal(
    (
      await call(
        env,
        `collector/${s.id}/ack`,
        "POST",
        { status: "published" },
        env.COLLECTOR_TOKEN,
        { "X-Lease": claim.lease },
      )
    ).code,
    400,
  );
  assert.equal(
    (
      await call(
        env,
        `collector/${s.id}/ack`,
        "POST",
        { status: "published", commit: "a".repeat(40) },
        env.COLLECTOR_TOKEN,
        { "X-Lease": claim.lease },
      )
    ).code,
    200,
  );
  assert.equal(
    (await call(env, `submissions/${s.id}/status`, "GET", null, s.receiptToken))
      .body.commit,
    "a".repeat(40),
  );
  assert.equal(
    (
      await call(
        env,
        `submissions/${s.id}/files/0`,
        "PUT",
        wdb(),
        s.uploadToken,
      )
    ).code,
    409,
  );
});
test("origin and quota enforce admission, cleanup releases storage", async () => {
  const env = environment();
  assert.equal(
    (
      await call(env, "submissions", "POST", {}, "", {
        Origin: "https://evil.example",
      })
    ).code,
    403,
  );
  for (let i = 0; i < 5; i++) assert.ok((await create(env)).id);
  assert.equal((await create(env)).error.includes("daily limit"), true);
  env.db.exec("UPDATE submissions SET expires=0");
  const cleanup = await call(
    env,
    "collector/cleanup",
    "POST",
    {},
    env.COLLECTOR_TOKEN,
  );
  assert.equal(cleanup.body.removed, 5);
});
test("browser filtering is not trusted and review status is determined by policy", async () => {
  const env = environment(),
    data = bytes("AIO_sv_Addons={}; AIO_sv={Private=1}");
  const s = (
    await call(env, "submissions", "POST", {
      policy: 1,
      consent: true,
      turnstile: "ok",
      files: [
        {
          name: "aio_client.lua",
          size: data.length,
          sha256: await sha256(data),
          mode: "unknown",
          review: false,
        },
      ],
    })
  ).body;
  const r = await call(
    env,
    `submissions/${s.id}/files/0`,
    "PUT",
    data,
    s.uploadToken,
  );
  assert.equal(r.code, 422);
  assert.equal(env.objects.size, 0);
});

test("exhausted retries become reviewable instead of remaining queued forever", async () => {
  const env = environment(),
    s = await create(env);
  env.db
    .prepare("UPDATE submissions SET status='received',attempts=5 WHERE id=?")
    .run(s.id);
  await call(env, "collector/cleanup", "POST", {}, env.COLLECTOR_TOKEN);
  assert.equal(
    env.db.prepare("SELECT status FROM submissions WHERE id=?").get(s.id)
      .status,
    "needs_review",
  );
});
test("expired but not yet deleted payloads still count toward storage capacity", async () => {
  const env = environment(),
    s = await create(env);
  env.db
    .prepare("UPDATE submissions SET bytes=2147483648,expires=0 WHERE id=?")
    .run(s.id);
  assert.match((await create(env)).error, /queue is full/);
});

async function readyClaim(env) {
  const submission = await create(env);
  await call(env, `submissions/${submission.id}/files/0`, "PUT", wdb(), submission.uploadToken);
  await call(env, `submissions/${submission.id}/complete`, "POST", {}, submission.uploadToken);
  const claim = (await call(env, "collector/claim", "POST", {}, env.COLLECTOR_TOKEN)).body.submission;
  return { submission, claim };
}
test("verified duplicate completion preserves commit and explains already included", async () => {
  const env = environment();
  const { submission, claim } = await readyClaim(env);
  const done = await call(env, `collector/${submission.id}/ack`, "POST", {status:"published", commit:"a".repeat(40), duplicate:true}, env.COLLECTOR_TOKEN, {"X-Lease":claim.lease});
  assert.equal(done.code, 200);
  const receipt = await call(env, `submissions/${submission.id}/status`, "GET", null, submission.receiptToken);
  assert.equal(receipt.body.status, "published");
  assert.match(receipt.body.message, /already included/);
});
test("lease replacement between read and mutation rejects both ack and renewal", async () => {
  for (const action of ["ack", "renew"]) {
    const env = environment();
    const { submission, claim } = await readyClaim(env);
    const original = env.DB.prepare.bind(env.DB);
    env.DB.prepare = (sql) => {
      const statement = original(sql);
      if (!sql.startsWith("UPDATE submissions SET")) return statement;
      return { bind(...args) {
        const bound = statement.bind(...args);
        return {...bound, async run() {
          env.db.prepare("UPDATE submissions SET lease='another-owner' WHERE id=?").run(submission.id);
          return bound.run();
        }};
      }};
    };
    const result = await call(env, `collector/${submission.id}/${action}`, "POST", action === "ack" ? {status:"published",commit:"a".repeat(40)} : {}, env.COLLECTOR_TOKEN, {"X-Lease":claim.lease});
    assert.equal(result.code, 409);
    const row = env.db.prepare("SELECT status,commit_sha FROM submissions WHERE id=?").get(submission.id);
    assert.equal(row.status, "processing");assert.equal(row.commit_sha, null);
  }
});

test("publication reconciliation survives fifth-attempt expiry without taking over a live claim", async () => {
  const env=environment();const {submission,claim}=await readyClaim(env);
  const row=env.db.prepare("SELECT manifest FROM submissions WHERE id=?").get(submission.id);
  const manifest=JSON.parse(row.manifest);const revision="b".repeat(64);
  const parts=manifest.files.map(f=>[f.name,f.mode,f.size,f.sha256]);
  const bundle=await sha256(bytes(JSON.stringify([revision,manifest.policy,parts])));
  const payload={status:"published",commit:"a".repeat(40),bundle,revision};
  const finish=()=>call(env,`collector/${submission.id}/finalize`,"POST",payload,env.COLLECTOR_TOKEN);
  assert.equal((await finish()).code,409);
  env.db.prepare("UPDATE submissions SET attempts=5,lease_until=0 WHERE id=?").run(submission.id);
  await call(env,"collector/cleanup","POST",{},env.COLLECTOR_TOKEN);
  assert.equal(env.db.prepare("SELECT status FROM submissions WHERE id=?").get(submission.id).status,"needs_review");
  assert.equal((await finish()).code,200);assert.equal((await finish()).code,200);
  assert.equal(env.db.prepare("SELECT status FROM submissions WHERE id=?").get(submission.id).status,"published");
  payload.bundle="c".repeat(64);assert.equal((await finish()).code,400);
});


test("collector stream requires admin and exposes only status metadata", async()=>{
  const env=environment();await create(env);
  assert.equal((await call(env,"collector/status")).code,401);
  const response=await call(env,"collector/status","GET",null,"collector-secret");
  assert.equal(response.code,200);assert.equal(response.body.submissions.length,1);
  assert.deepEqual(Object.keys(response.body.submissions[0]).sort(),["bytes","commit_sha","created","id","modes","status"].sort());
  assert.deepEqual(response.body.submissions[0].modes,["unknown"]);
});
