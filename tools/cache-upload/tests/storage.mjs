import { DatabaseSync } from "node:sqlite";
import { readFileSync } from "node:fs";
export function environment() {
  const db = new DatabaseSync(":memory:");
  db.exec(
    readFileSync(
      new URL("../drizzle/0000_messy_madelyne_pryor.sql", import.meta.url),
      "utf8",
    ).replaceAll("--> statement-breakpoint", ""),
  );
  const objects = new Map();
  const DB = {
    prepare(sql) {
      return {
        bind(...args) {
          return {
            first: async () => db.prepare(sql).get(...args) || null,
            all: async () => ({ results: db.prepare(sql).all(...args) }),
            run: async () => ({
              meta: { changes: db.prepare(sql).run(...args).changes },
            }),
          };
        },
      };
    },
  };
  const BUCKET = {
    async put(k, b, options) {
      objects.set(k, { b: new Uint8Array(b), size: b.length, ...options });
    },
    async head(k) {
      return objects.get(k) || null;
    },
    async get(k) {
      const o = objects.get(k);
      return o ? { ...o, body: o.b } : null;
    },
    async delete(k) {
      objects.delete(k);
    },
  };
  return {
    DB,
    BUCKET,
    UPLOADS_ENABLED: "true",
    TURNSTILE_SITE_KEY: "key",
    TURNSTILE_SECRET: "secret",
    COLLECTOR_TOKEN: "collector-secret",
    RATE_SECRET: "rate-secret",
    ALLOWED_ORIGIN: "https://upload.example",
    db,
    objects,
  };
}
