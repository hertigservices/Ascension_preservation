import { unzipSync } from "fflate";
import { canonicalName, allowedName } from "./policy.mjs";
// Inspect the central directory BEFORE inflation. ZIP64, encryption, links,
// paths escaping their root and nested archives are deliberately unsupported.
export function readZip(b) {
  if (b.length > 128 * 1024 * 1024) throw Error("ZIP exceeds 128 MiB");
  const v = new DataView(b.buffer, b.byteOffset, b.length);
  let end = -1;
  for (let p = b.length - 22; p >= Math.max(0, b.length - 65557); p--)
    if (v.getUint32(p, true) === 0x06054b50) {
      end = p;
      break;
    }
  if (end < 0) throw Error("Invalid ZIP");
  const count = v.getUint16(end + 10, true),
    offset = v.getUint32(end + 16, true);
  if (
    v.getUint16(end + 4, true) ||
    v.getUint16(end + 6, true) ||
    count > 5000 ||
    count === 65535
  )
    throw Error("Unsupported or oversized ZIP");
  let p = offset,
    total = 0;
  const names = new Set();
  for (let i = 0; i < count; i++) {
    if (p + 46 > end || v.getUint32(p, true) !== 0x02014b50)
      throw Error("Invalid ZIP directory");
    const flags = v.getUint16(p + 8, true),
      size = v.getUint32(p + 24, true),
      nl = v.getUint16(p + 28, true),
      el = v.getUint16(p + 30, true),
      cl = v.getUint16(p + 32, true);
    const n = new TextDecoder().decode(b.subarray(p + 46, p + 46 + nl));
    const mode = v.getUint32(p + 38, true) >>> 16;
    if (
      flags & 1 ||
      size === 0xffffffff ||
      (mode & 0xf000) === 0xa000 ||
      n.includes("\\") ||
      n.startsWith("/") ||
      n.includes(":") ||
      n.split("/").includes("..") ||
      names.has(n)
    )
      throw Error("Unsafe or unsupported ZIP member");
    names.add(n);
    if (allowedName(canonicalName(n))) {
      total += size;
      if (size > 256 * 1024 * 1024 || total > 512 * 1024 * 1024)
        throw Error("ZIP expands beyond the safety limit");
    }
    p += 46 + nl + el + cl;
    if (p > end) throw Error("Invalid ZIP directory length");
  }
  const files = unzipSync(b, {
    filter: (f) => {
      if (!names.has(f.name)) throw Error("ZIP directory mismatch");
      return allowedName(canonicalName(f.name));
    },
  });
  for (const [n, data] of Object.entries(files))
    if (data.length > 256 * 1024 * 1024)
      throw Error("Expanded file is too large");
  return { files, skipped: count - Object.keys(files).length };
}
