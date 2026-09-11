import { allowedName, canonicalName } from "../shared/policy.mjs";

const routes = {
  wdb: [["Cache", "WDB"], ["WDB"]],
  account: [["WTF", "Account"], ["Account"]],
};
const MAX_ENTRIES = 50000;

// Keep relative paths locally: the sanitizer needs realm and personal-name context.
export function groupInstallationFiles(selection) {
  const groups = { wdb: [], account: [] };
  for (const file of selection) {
    const path = file.webkitRelativePath || "";
    const parts = path.replaceAll("\\", "/").split("/").slice(1);
    for (const [kind, candidates] of Object.entries(routes)) {
      if (
        candidates.some((route) =>
          route.every(
            (part, i) => parts[i]?.toLowerCase() === part.toLowerCase(),
          ),
        ) &&
        allowedName(canonicalName(path))
      ) {
        groups[kind].push({ file, path });
      }
    }
  }
  return groups;
}

async function child(parent, name) {
  try {
    return await parent.getDirectoryHandle(name);
  } catch (error) {
    if (error.name !== "NotFoundError") throw error;
    for await (const entry of parent.values()) {
      if (
        entry.kind === "directory" &&
        entry.name.toLowerCase() === name.toLowerCase()
      )
        return entry;
    }
    return null;
  }
}

export async function findInstallationFolders(root) {
  const groups = { wdb: null, account: null };
  for (const [kind, candidates] of Object.entries(routes)) {
    for (const route of candidates) {
      let handle = root;
      for (const part of route) {
        handle = await child(handle, part);
        if (!handle) break;
      }
      if (handle) {
        groups[kind] = { handle, path: [root.name, ...route].join("/") };
        break;
      }
    }
  }
  return groups;
}

export async function readGameFolder(folder) {
  const files = [];
  let count = 0;
  async function walk(handle, path, depth) {
    if (depth > 24)
      throw Error(
        "This folder is nested too deeply. Choose WDB or Account directly.",
      );
    for await (const entry of handle.values()) {
      if (++count > MAX_ENTRIES)
        throw Error(
          "This folder contains too many entries. Choose a smaller folder directly.",
        );
      const relativePath = path + "/" + entry.name;
      if (entry.kind === "directory")
        await walk(entry, relativePath, depth + 1);
      else if (allowedName(canonicalName(entry.name)))
        files.push({ file: await entry.getFile(), path: relativePath });
    }
  }
  await walk(folder.handle, folder.path, 0);
  return files;
}
