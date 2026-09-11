import test from "node:test";
import assert from "node:assert/strict";
import {
  groupInstallationFiles,
  findInstallationFolders,
  readGameFolder,
} from "../lib/game-folders.mjs";

const file = (path) => ({
  name: path.split("/").at(-1),
  webkitRelativePath: path,
});
const directory = (name, entries = []) => ({
  name,
  kind: "directory",
  async getDirectoryHandle(name) {
    const match = entries.find(
      (e) => e.kind === "directory" && e.name === name,
    );
    if (!match)
      throw Object.assign(new Error("missing"), { name: "NotFoundError" });
    return match;
  },
  async *values() {
    yield* entries;
  },
});

test("installation selection separates the intended folders and excludes unrelated paths", () => {
  const wdb = file(
    "ascension-live/Cache/WDB/Conquest of Azeroth/itemcache.wdb",
  );
  const account = file(
    "ascension-live/WTF/Account/PrivateAccount/SavedVariables/MobSpells.lua",
  );
  const groups = groupInstallationFiles([
    wdb,
    account,
    file("ascension-live/Interface/AddOns/MobSpells.lua"),
    file("ascension-live/Data/patch.MPQ"),
    file("ascension-live/WTF/Config.wtf"),
    file("ascension-live/WTF/Account/PrivateAccount/macros-cache.txt"),
    file("ascension-live/backup/Cache/WDB/itemcache.wdb"),
  ]);
  assert.deepEqual(groups.wdb, [{ file: wdb, path: wdb.webkitRelativePath }]);
  assert.deepEqual(groups.account, [
    { file: account, path: account.webkitRelativePath },
  ]);
});

test("backup roots containing WDB and Account directly are accepted", () => {
  const groups = groupInstallationFiles([
    file("backup/WDB/itemcache.wdb"),
    file("backup/Account/Name/SavedVariables/AIO_Client.lua"),
  ]);
  assert.equal(groups.wdb.length, 1);
  assert.equal(groups.account.length, 1);
});

test("native installation lookup handles mixed case and missing folder types", async () => {
  const wdb = directory("wdb");
  const root = directory("ascension-live", [
    directory("cache", [wdb]),
    directory("Data"),
  ]);
  const groups = await findInstallationFolders(root);
  assert.equal(groups.wdb.handle, wdb);
  assert.equal(groups.account, null);
});

test("native reader never opens unsupported files and retains relative sanitizer context", async () => {
  let opened = 0;
  const allowed = {
    name: "MobSpells.lua",
    kind: "file",
    async getFile() {
      opened++;
      return { name: this.name };
    },
  };
  const unrelated = {
    name: "macros-cache.txt",
    kind: "file",
    async getFile() {
      throw Error("must not open");
    },
  };
  const handle = directory("Account", [
    directory("PrivateAccount", [
      directory("SavedVariables", [allowed]),
      unrelated,
    ]),
  ]);
  const files = await readGameFolder({
    handle,
    path: "ascension-live/WTF/Account",
  });
  assert.equal(opened, 1);
  assert.equal(
    files[0].path,
    "ascension-live/WTF/Account/PrivateAccount/SavedVariables/MobSpells.lua",
  );
});

test("Ascension-specific capture files and backups are found in the normal Account layout", () => {
  const groups = groupInstallationFiles([
    file(
      "ascension-live/WTF/Account/PrivateAccount/SavedVariables/Ascension_CoAReader.lua",
    ),
    file(
      "ascension-live/WTF/Account/PrivateAccount/SavedVariables/AscensionHarvest.lua.bak",
    ),
    file(
      "ascension-live/WTF/Account/PrivateAccount/SavedVariables/AscensionUI.lua",
    ),
    file(
      "ascension-live/WTF/Account/PrivateAccount/SavedVariables/BindMySoul.lua",
    ),
  ]);
  assert.equal(groups.account.length, 2);
  assert.equal(groups.wdb.length, 0);
});
