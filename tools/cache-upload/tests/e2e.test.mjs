import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdtemp, writeFile, readFile, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve, dirname, basename } from "node:path";
import { environment } from "./storage.mjs";
import { handle } from "../lib/intake-api.mjs";
import { sanitizeLua, bytes, sha256 } from "../shared/policy.mjs";
test("HTTP upload through the real Python collector and Node validator into isolated inbox", async () => {
  const env = environment(),
    nativeFetch = globalThis.fetch;
  const server = createServer(async (req, res) => {
    try {
      if (req.url.startsWith("/api/collector/"))
        assert.equal(
          req.headers["user-agent"],
          "AscensionArchive-Collector/1.0",
        );
      let chunks = [];
      for await (const c of req) chunks.push(c);
      const r = new Request("http://localhost" + req.url, {
        method: req.method,
        headers: req.headers,
        body: chunks.length ? Buffer.concat(chunks) : undefined,
      });
      const result = await handle(r, env, req.url.replace(/^\/api\//, ""));
      res.writeHead(result.status, Object.fromEntries(result.headers));
      res.end(Buffer.from(await result.arrayBuffer()));
    } catch (e) {
      res.writeHead(500);
      res.end(String(e));
    }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const origin = "http://127.0.0.1:" + server.address().port;
  env.ALLOWED_ORIGIN = origin;
  globalThis.fetch = async (url, options) =>
    String(url).startsWith("https://challenges.cloudflare.com/")
      ? Response.json({
          success: true,
          hostname: "127.0.0.1",
          action: "contribute",
        })
      : nativeFetch(url, options);
  const root = await mkdtemp(join(tmpdir(), "ascension-upload-e2e-"));
  try {
    const data = sanitizeLua(
      "gathermate2.lua",
      bytes("GatherMate2MineDB={[1]={[123456]=201}}"),
    ).data;
    const create = await nativeFetch(origin + "/api/submissions", {
      method: "POST",
      headers: { Origin: origin },
      body: JSON.stringify({
        policy: 1,
        consent: true,
        turnstile: "test",
        files: [
          {
            name: "gathermate2.lua",
            size: data.length,
            sha256: await sha256(data),
            mode: "unknown",
          },
        ],
      }),
    });
    assert.equal(create.status, 201);
    const s = await create.json();
    const headers = {
      Origin: origin,
      Authorization: "Bearer " + s.uploadToken,
    };
    assert.equal(
      (
        await nativeFetch(origin + `/api/submissions/${s.id}/files/0`, {
          method: "PUT",
          headers,
          body: data,
        })
      ).status,
      200,
    );
    assert.equal(
      (
        await nativeFetch(origin + `/api/submissions/${s.id}/complete`, {
          method: "POST",
          headers,
        })
      ).status,
      200,
    );
    const config = {
      url: origin,
      state: join(root, "private"),
      work: join(root, "work"),
      inbox: join(root, "work", "_inbox"),
      node: process.execPath,
    };
    const configPath = join(root, "config.json");
    await writeFile(configPath, JSON.stringify(config));
    const child = spawn(
      "python",
      [
        "-B",
        resolve("collector/collector.py"),
        "--config",
        configPath,
        "--once",
        "--stage-only",
      ],
      {
        env: {
          ...process.env,
          ASCENSION_UPLOAD_COLLECTOR_TOKEN: env.COLLECTOR_TOKEN,
        },
        stdio: ["ignore", "pipe", "pipe"],
      },
    );
    let output = "";
    child.stdout.on("data", (c) => (output += c));
    child.stderr.on("data", (c) => (output += c));
    const code = await new Promise((resolve, reject) => {
      child.on("exit", resolve);
      child.on("error", reject);
    });
    assert.equal(code, 0, output);
    assert.match(output, /staged/);
    const files = await readdir(config.inbox, { recursive: true });
    const lua = files.find((f) => f.endsWith("gathermate2.lua"));
    assert.ok(lua);
    assert.deepEqual(
      new Uint8Array(await readFile(join(config.inbox, lua))),
      data,
    );
    const status = await nativeFetch(
      origin + `/api/submissions/${s.id}/status`,
      { headers: { Authorization: "Bearer " + s.receiptToken } },
    );
    assert.equal((await status.json()).status, "processing");
  } finally {
    globalThis.fetch = nativeFetch;
    await new Promise((resolve) => server.close(resolve));
    if (
      dirname(resolve(root)) !== resolve(tmpdir()) ||
      !basename(root).startsWith("ascension-upload-e2e-")
    )
      throw Error("Unsafe test cleanup path");
    await rm(root, { recursive: true, force: true });
  }
});
