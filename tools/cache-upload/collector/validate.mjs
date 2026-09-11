import { readFile, writeFile, mkdir, stat } from "node:fs/promises";
import { resolve } from "node:path";
import {
  validateManifest,
  sanitize,
  sha256,
  MODE_FOLDERS,
} from "../shared/policy.mjs";
const [input, output] = process.argv.slice(2);
if (!input || !output) throw Error("Usage: node validate.mjs INPUT OUTPUT");
const manifest = validateManifest({
  ...JSON.parse(await readFile(resolve(input, "manifest.json"), "utf8")),
  consent: true,
});
const accepted = [],
  review = [];
for (const f of manifest.files) {
  const p = resolve(input, String(f.index));
  if ((await stat(p)).size !== f.size) throw Error("Unexpected file size");
  const data = new Uint8Array(await readFile(p));
  if ((await sha256(data)) !== f.sha256) throw Error("Checksum mismatch");
  const clean = sanitize(f.name, data);
  if ((await sha256(clean.data)) !== f.sha256)
    throw Error("File was not privacy filtered");
  const folder =
    MODE_FOLDERS[f.mode] === "unknown" ? "enUS" : MODE_FOLDERS[f.mode];
  const rel = `${clean.review ? "review" : "accepted"}/part-${String(f.index).padStart(4, "0")}/${folder}/${f.name}`;
  const target = resolve(output, rel);
  await mkdir(resolve(target, ".."), { recursive: true });
  await writeFile(target, clean.data, { flag: "wx" });
  (clean.review ? review : accepted).push({
    index: f.index,
    path: rel,
    sha256: f.sha256,
  });
}
await writeFile(
  resolve(output, "validation.json"),
  JSON.stringify({ accepted, review, policy: manifest.policy }, null, 2),
  { flag: "wx" },
);
