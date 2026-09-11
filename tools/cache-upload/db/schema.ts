import { sqliteTable, text, integer, index } from "drizzle-orm/sqlite-core";
export const submissions = sqliteTable(
  "submissions",
  {
    id: text("id").primaryKey(),
    uploadHash: text("upload_hash").notNull(),
    receiptHash: text("receipt_hash").notNull(),
    ipHash: text("ip_hash").notNull(),
    manifest: text("manifest").notNull(),
    bytes: integer("bytes").notNull(),
    created: integer("created").notNull(),
    expires: integer("expires").notNull(),
    status: text("status").notNull().default("uploading"),
    lease: text("lease"),
    leaseUntil: integer("lease_until").notNull().default(0),
    commit: text("commit_sha"),
    message: text("message"),
    attempts: integer("attempts").notNull().default(0),
  },
  (t) => [
    index("submission_queue").on(t.status, t.leaseUntil, t.created),
    index("submission_quota").on(t.ipHash, t.created),
    index("submission_expiry").on(t.expires),
  ],
);
