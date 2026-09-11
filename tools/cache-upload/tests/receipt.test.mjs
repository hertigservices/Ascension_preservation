import test from "node:test";
import assert from "node:assert/strict";
import { parseReceiptHash } from "../lib/receipt.mjs";
const id = "00000000-0000-4000-8000-000000000001",
  token = "a".repeat(64);
test("receipt links restore status credentials, including chat-escaped separators", () => {
  for (const separator of ["&", "\\&"])
    assert.deepEqual(
      parseReceiptHash(`#receipt=${id}${separator}token=${token}`),
      { id, receiptToken: token },
    );
  assert.equal(parseReceiptHash(""), null);
  assert.equal(parseReceiptHash("#help"), null);
});
test("incomplete receipt links produce an actionable error instead of silently disappearing", () => {
  for (const hash of [
    `#receipt=${id}`,
    `#receipt=bad&token=${token}`,
    `#receipt=${id}&token=bad`,
  ])
    assert.throws(() => parseReceiptHash(hash), /incomplete or invalid/);
});
