"""Package a contribution from your own Ascension install, ready to send.

WHAT THIS IS FOR

    Your client writes down every answer the server gives it -- what an item is,
    what a quest asks for, what an NPC says. Ascension is a live custom server,
    so those answers exist nowhere else. When a realm changes or closes, the only
    surviving copy is the one sitting in players' cache folders.

    This tool reads your install, keeps the parts that are game data, throws away
    the parts that are about YOU, and writes one small file you can send in.

    Point it at your Ascension folder:

        python contribute.py --install "C:/Games/Ascension"

    It prints exactly what it found and what it would send, and stops. Nothing
    is written and nothing leaves your machine until you look at that list and
    run it again with --write.

WHAT IT SENDS, AND WHAT IT WILL NOT

    Allowed, because a server sends the identical bytes to every player:
        the seven query caches -- item, creature, gameobject, quest, npc,
        pagetext, itemname
        five addon saved-variable files that hold observed game data

    Never, under any circumstances:
        itemtextcache.wdb -- the text of MAIL and letters people wrote to you
        anything else under WTF -- chat, macros, keybinds, combat logs, and a
        folder tree literally named after your login and your characters
        any file whose kind this tool does not recognise

    The rule is an ALLOW-list: a file travels only if its kind is known to be
    game content. Anything unfamiliar is left behind and listed as skipped. That
    is deliberately the opposite of a block-list, where forgetting one entry
    leaks it. scrub.py holds the policy and this tool defers to it entirely.

WHY IT DOES NOT MERGE ANYTHING

    It would be easy to make this tool merge your data into the public set and
    hand you the finished thing. It must not, for one reason: the archive's only
    real defence against bad data is counting how many INDEPENDENT people saw
    the same bytes. An item nine players' clients agree on is a fact. An item one
    submission asserts alone is a claim. If contributors sent merged results, a
    single sender would look exactly like a consensus, and that distinction --
    the one thing that makes poisoning hard -- would be gone.

    So this tool only ever describes what YOUR client saw. Merging happens once,
    centrally, where the count can be kept honest.

WHAT COMES OUT

    A compressed ZIP containing complete WDB observations. --index reports how
    many records are new; it does not remove known records. Central intake reads
    the actual WDB records rather than trusting fingerprint-only claims.

        manifest.json                  modes, counts and skipped files
        new/<locale>/<group>/*.wdb      complete observed WDB records
        lua/capture-N/<addon>.lua       distinct structurally filtered captures

    Lua filtering requires Node and the shared cache-upload policy module. If
    either is unavailable, Lua stays on your computer; use the browser uploader.
    Open it. It is meant to be inspectable before you send it.
"""
import os, re, sys, csv, gzip, json, glob, zipfile, hashlib, struct, argparse, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wdblib, scrub, modes, config

FORMAT = 1                      # bundle format version, so a reader can tell
LOCALES = ("enUS", "enGB", "deDE", "frFR", "esES", "esMX", "ruRU", "koKR",
           "zhCN", "zhTW", "ptBR", "itIT")

# Where an Ascension install usually lives, tried in order when --install is
# not given. Being wrong here is harmless: the check below rejects a folder
# that does not look like a client.
GUESSES = [r"C:/Ascension", r"C:/Games/Ascension", r"C:/Program Files/Ascension",
           r"C:/Program Files (x86)/Ascension",
           os.path.expanduser("~/Ascension"),
           os.path.expanduser("~/Documents/Ascension")]


def looks_like_client(root):
    """A WoW install has a Data folder and an executable. Checking before the
    walk turns 'pointed at the wrong folder' into a sentence instead of an
    empty result the contributor has to interpret."""
    if not os.path.isdir(root):
        return False, "no such folder"
    has_data = os.path.isdir(os.path.join(root, "Data"))
    has_exe = any(os.path.exists(os.path.join(root, n)) for n in
                  ("Wow.exe", "Ascension.exe", "WoW.exe", "run.exe"))
    has_cache = os.path.isdir(os.path.join(root, "Cache"))
    if has_cache and (has_data or has_exe):
        return True, ""
    if has_cache:
        return True, "no Wow.exe beside it, but it has a Cache folder -- continuing"
    return False, ("does not look like a WoW install: expected a Cache folder "
                   "and a Data folder or Wow.exe beside each other")


def find_install(explicit):
    if explicit:
        ok, note = looks_like_client(explicit)
        if not ok:
            sys.exit(f"!! {explicit}\n   {note}")
        return os.path.abspath(explicit), note
    for g in GUESSES:
        ok, note = looks_like_client(g)
        if ok:
            return os.path.abspath(g), note
    sys.exit("!! could not find an Ascension install. Pass --install "
             '"C:/path/to/Ascension"')


def sha1(b):
    return hashlib.sha1(b).hexdigest()


LOCALE_ROOT = "(locale root)"


def find_caches(root):
    """Yield (path, group_folder, locale) for every .wdb under Cache/WDB.

    Two layouts exist in the wild and both are normal:

        Cache/WDB/enUS/<Realm> - <Mode>/itemcache.wdb     <- mode is in the path
        Cache/WDB/enUS/itemcache.wdb                      <- mode is not

    The realm folder, where there is one, is the ONLY place the game mode is
    written down; a cache file does not say which ruleset produced it. That is
    why archived zips made from too high up in the tree can never be identified
    afterwards. When the folder is missing, this tool falls back to the WTF tree
    (see realm_candidates) -- which is the whole reason for reading a live
    install instead of a zip: the answer is still on disk here.
    """
    wdb = os.path.join(root, "Cache", "WDB")
    if not os.path.isdir(wdb):
        return
    for locale in sorted(os.listdir(wdb)):
        lp = os.path.join(wdb, locale)
        if not os.path.isdir(lp):
            continue
        for fn in sorted(os.listdir(lp)):
            p = os.path.join(lp, fn)
            if os.path.isfile(p) and fn.lower().endswith(".wdb"):
                yield p, LOCALE_ROOT, locale        # caches at the locale root
            elif os.path.isdir(p):
                for fn2 in sorted(os.listdir(p)):
                    if fn2.lower().endswith(".wdb"):
                        yield os.path.join(p, fn2), fn, locale


def realm_candidates(root):
    """Realm/mode names this install knows about, from the WTF tree.

    Two independent places record it:
      WTF/Config.wtf            SET realmName "<Realm>"
      WTF/Account/<login>/<Realm>/   one folder per realm the account played

    On Ascension the second is '<Realm> - <Mode>', so it names the game mode --
    the exact fact a cache sitting at the locale root has lost.

    Only the REALM segment is ever read or returned. The <login> segment is the
    account identifier and never leaves this function.
    """
    out = set()
    cfg = os.path.join(root, "WTF", "Config.wtf")
    if os.path.exists(cfg):
        with open(cfg, encoding="utf-8", errors="replace") as f:
            m = re.search(r'SET\s+realmName\s+"([^"]+)"', f.read(), re.I)
            if m:
                out.add(m.group(1).strip())
    acct = os.path.join(root, "WTF", "Account")
    if os.path.isdir(acct):
        for login in os.listdir(acct):
            lp = os.path.join(acct, login)
            if not os.path.isdir(lp):
                continue
            for realm in os.listdir(lp):
                if realm == "SavedVariables":
                    continue
                if os.path.isdir(os.path.join(lp, realm)):
                    out.add(realm.strip())
    return sorted(out)


def resolve_mode(group, candidates):
    """Decide the mode for one cache folder, saying how it was decided.

    A folder that already names the mode is believed. Otherwise the WTF realm
    names are tried, and a mode is adopted only if exactly ONE of them resolves
    to a real mode. Two candidates means this install played two rulesets and
    nothing on disk says which one wrote the cache -- so it stays unknown and
    both are reported. Guessing there would silently file one mode's data under
    another, which is worse than an honest gap.
    """
    m = modes.classify(group)
    if m["source"] in ("path", "override", "modes.json") and m["mode"] != modes.UNKNOWN:
        return m, ""
    named = [modes.classify(c) for c in candidates]
    real = [x for x in named if x["mode"] != modes.UNKNOWN]
    if len(real) == 1:
        r = dict(real[0]); r["source"] = "WTF realm folder"
        return r, ""
    if len(real) > 1:
        return m, ("this install played " +
                   ", ".join(sorted(x["mode"] for x in real)) +
                   " and the cache folder does not say which wrote these files")
    if candidates:
        return m, ("WTF names realm(s) " + ", ".join(candidates) +
                   ", which do not identify a game mode")
    return m, "nothing on disk records the game mode"


def find_lua(root):
    """Yield (path, label) for allow-listed addon saved variables.

    SavedVariables live at two depths -- per account and per character -- and the
    per-character ones sit under a folder named after the character. The file
    NAME decides whether it travels; the path never does. The path is not even
    reported upward: scrub.scrub_path would redact it anyway, and the account
    folder is exactly the thing that must not leave.
    """
    acct = os.path.join(root, "WTF", "Account")
    if not os.path.isdir(acct):
        return
    for path in glob.glob(os.path.join(acct, "**", "SavedVariables", "*.lua"),
                          recursive=True):
        name = os.path.basename(path)
        policy, why = scrub.classify(name)
        # Non-matching addon files are yielded too, as refusals. A contributor
        # who cannot see that forty files were left behind cannot check that the
        # right ones were, and "it silently sent nothing" and "it silently sent
        # everything" look identical from outside.
        yield path, name, why, policy == scrub.SCRUB


def read_index(index_dir, cache):
    """sha1s the archive already has for one cache, from a published index."""
    if not index_dir:
        return None
    for p in (os.path.join(index_dir, f"{cache}.index.tsv.gz"),
              os.path.join(index_dir, "raw", f"{cache}.index.tsv.gz")):
        if os.path.exists(p):
            known = set()
            with gzip.open(p, "rt", encoding="utf-8", newline="") as f:
                r = csv.reader(f, delimiter="\t")
                cols = next(r)
                i = cols.index("sha1")
                for row in r:
                    if row:
                        known.add(row[i])
            return known
    return None


def plan_cache(path, group, locale, index_dir):
    """Describe one .wdb: what it is, and which of its records are new."""
    with open(path, "rb") as f:
        b = f.read()
    info = wdblib.inspect(path, data=b)
    name = os.path.basename(path)
    policy, why = scrub.classify(name)
    stem = name.lower()[:-4]
    out = {"file": name, "cache": stem, "group": group, "locale": locale,
           "policy": policy, "why": why, "bytes": len(b),
           "records": 0, "new": 0, "fingerprints": [], "pack": b"",
           "note": "", "header": b[:wdblib.HEADER_LEN]}
    if policy != scrub.PUBLISH:
        return out
    if not info.standard:
        out["policy"] = scrub.QUARANTINE
        out["why"] = info.note or "not a recognised cache header"
        return out
    known = read_index(index_dir, stem)
    fps = []
    packed = bytearray()
    for entry, size, payload in wdblib.iter_records(b):
        h = sha1(payload)
        fps.append((entry, h))
        # Fingerprints alone are not currently consumed by central intake.
        # Ship the complete observation; central record merging performs dedup.
        packed += struct.pack("<II", entry, len(payload)) + payload
    out["records"] = len(fps)
    out["fingerprints"] = fps
    out["pack"] = bytes(packed)
    out["new"] = (len(fps) if known is None
                  else sum(1 for _e, h in fps if h not in known))
    out["note"] = ("no index given, packing every record"
                   if known is None else f"{len(known):,} already archived")
    return out


def sanitize_savedvariables(path):
    """Run the same non-executing parser/policy used by browser and collector.
    Missing Node/policy is an explicit refusal, never a regex-only fallback.
    """
    import subprocess, shutil
    from pathlib import Path
    candidates = [Path(__file__).resolve().parents[2]/"cache-upload/shared/policy.mjs",
                  Path(config.WORK).parent/"upload-service/shared/policy.mjs"]
    policy = next((p for p in candidates if p.is_file()), None)
    node = shutil.which("node") or os.environ.get("ASCENSION_NODE")
    if not policy or not node:
        raise ValueError("Shared structural policy requires Node; use the browser uploader")
    raw = Path(path).read_bytes()
    if len(raw) > 8*1024*1024:
        raise ValueError("Lua input exceeds supported bound")
    source = raw.decode("utf-8", "strict")
    script = """import {sanitizeLua, canonicalName, identifiersFromPath, bytes} from %s;
import {readFileSync} from 'node:fs';
const input=JSON.parse(readFileSync(0,'utf8'));
const name=canonicalName(input.path);
const result=sanitizeLua(name,bytes(input.source),identifiersFromPath(input.path));
process.stdout.write(JSON.stringify({name,text:new TextDecoder().decode(result.data)}));
""" % json.dumps(policy.as_uri())
    result = subprocess.run([node,"--input-type=module","-e",script],
        input=json.dumps({"source":source,"path":str(path)}), text=True,
        encoding="utf-8", capture_output=True, timeout=60)
    if result.returncode:
        raise ValueError("Structural filtering refused this file")
    out=json.loads(result.stdout)
    return out["text"],out["name"]


def plan(root, index_dir):
    caches, luas, skipped = [], [], []
    seen_group = {}
    candidates = realm_candidates(root)
    for path, group, locale in find_caches(root):
        c = plan_cache(path, group, locale, index_dir)
        if c["policy"] == scrub.PUBLISH:
            caches.append(c)
            if group not in seen_group:
                m, why = resolve_mode(group, candidates)
                m = dict(m); m["ambiguity"] = why
                seen_group[group] = m
        else:
            skipped.append((c["file"], c["why"]))
    lua_seen = set()
    for path, name, why, allowed in find_lua(root):
        try:
            clean, canonical = sanitize_savedvariables(path)
        except (ValueError, OSError, __import__("subprocess").TimeoutExpired) as exc:
            skipped.append((name, "Structural filtering unavailable or requires private review; original retained"))
            continue
        digest = hashlib.sha256(clean.encode("utf-8")).hexdigest()
        if digest in lua_seen:
            continue
        lua_seen.add(digest)
        luas.append({"file": f"capture-{len(luas)+1}/{canonical}", "why": why,
                     "bytes": len(clean.encode("utf-8")), "text": clean,
                     "findings": {"structurally_filtered": 1}})
    return caches, luas, skipped, seen_group


def human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:,.0f} {u}" if u == "B" else f"{n/1:,.1f} {u}"
        n /= 1024.0


def size(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024.0


def report(root, caches, luas, skipped, groups, index_dir):
    print(f"\nAscension install: {root}")
    if not index_dir:
        print("No --index given, so every record will be packed. Point --index at "
              "a copy of\nthe published cachedata/raw folder to send only what is new.")
    print(f"\nCache folders found: {len(groups)}")
    for g, m in sorted(groups.items()):
        how = {"path": "read from the folder name", "override": "known folder",
               "modes.json": "local override",
               "WTF realm folder": "recovered from your WTF folder",
               "unknown": "COULD NOT be identified"}.get(m["source"], m["source"])
        print(f"  {g:<30} mode: {m['mode']:<22} ({how})")
        if m.get("ambiguity"):
            print(f"  {'':30} {m['ambiguity']}")
    if any(m["mode"] == modes.UNKNOWN for m in groups.values()):
        print("\n  An unidentified mode is still worth sending -- the records are\n"
              "  real either way. It just cannot be filed under a ruleset, so it\n"
              "  lands in the archive's 'unknown' bucket rather than being guessed.")

    print(f"\nCaches to send: {len(caches)}")
    tot_rec = tot_new = tot_pack = 0
    for c in sorted(caches, key=lambda c: (c["group"], c["cache"])):
        tot_rec += c["records"]; tot_new += c["new"]; tot_pack += len(c["pack"])
        print(f"  {c['group'][:26]:<26} {c['cache']:<16} "
              f"{c['records']:>7,} records, {c['new']:>7,} new  "
              f"({size(len(c['pack']))})")
    print(f"  {'':26} {'TOTAL':<16} {tot_rec:>7,} records, {tot_new:>7,} new  "
          f"({size(tot_pack)})")

    if luas:
        print(f"\nAddon data to send: {len(luas)}")
        for l in luas:
            f = ", ".join(f"{k}:{v}" for k, v in sorted(l["findings"].items()))
            print(f"  {l['file']:<34} {size(l['bytes']):>9}  {l['why']}")
            if f:
                print(f"  {'':34} redacted -> {f}")

    print(f"\nLeft behind: {len(skipped)}")
    for fn, why in sorted(set(skipped))[:20]:
        print(f"  {fn:<26} {why}")
    if len(set(skipped)) > 20:
        print(f"  ... and {len(set(skipped)) - 20} more")
    print("\nNothing above has been written anywhere yet.")


def write_bundle(out, root, caches, luas, skipped, groups):
    stamp = datetime.date.today().isoformat()
    slugs = sorted({groups[c["group"]]["slug"] for c in caches}) or ["unknown"]
    manifest = {
        "format": FORMAT,
        "tool": "contribute.py",
        "captured": stamp,
        "modes": [{"group": g, **m} for g, m in sorted(groups.items())],
        "caches": [{"cache": c["cache"], "group": c["group"],
                    "locale": c["locale"], "records": c["records"],
                    "new": c["new"],
                    # Descriptive manifest only; central intake consumes the
                    # complete WDB records, not unauthenticated fingerprint claims.
                    "fingerprints": [[e, h] for e, h in c["fingerprints"]]}
                   for c in caches],
        "lua": [{"file": l["file"], "bytes": l["bytes"],
                 "redacted": l["findings"]} for l in luas],
        "skipped": [{"file": fn, "why": why} for fn, why in sorted(set(skipped))],
    }
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    # ZIP_DEFLATED and a fixed date so the same install twice makes the same file.
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        def put(name, data):
            zi = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            z.writestr(zi, data)

        put("manifest.json",
            json.dumps(manifest, indent=1, sort_keys=True).encode("utf-8"))
        for c in caches:
            if c["pack"]:
                put(f"new/{c['locale']}/{c['group']}/{c['cache']}.wdb", c["header"] + c["pack"] + bytes(8))
        for l in luas:
            put(f"lua/{l['file']}", l["text"].encode("utf-8"))
    return out, os.path.getsize(out)


def main():
    ap = argparse.ArgumentParser(
        description="Package your Ascension cache data for contribution.")
    ap.add_argument("--install", help="path to your Ascension folder")
    ap.add_argument("--index", help="a copy of the published cachedata/raw folder, "
                                    "to report unseen records (complete observations are still packed)")
    ap.add_argument("--out", help="bundle to write (default: alongside this tool)")
    ap.add_argument("--write", action="store_true",
                    help="actually write the bundle; without it this only reports")
    a = ap.parse_args()

    root, note = find_install(a.install)
    if note:
        print(f"note: {note}")
    caches, luas, skipped, groups = plan(root, a.index)
    if not caches and not luas:
        sys.exit("\nNothing to send: no readable caches or addon data under "
                 f"{root}.\nIs this the right folder?")
    report(root, caches, luas, skipped, groups, a.index)

    if not a.write:
        print("\nRun again with --write to create the bundle.")
        return 0
    slug = sorted({groups[c["group"]]["slug"] for c in caches} or {"unknown"})[0]
    out = a.out or os.path.join(
        os.getcwd(), f"ascension-{slug}-{datetime.date.today():%Y%m%d}.zip")
    path, n = write_bundle(out, root, caches, luas, skipped, groups)
    print(f"\nwrote {path}  ({size(n)})")
    print("Open it and look inside before you send it -- it is a plain zip.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
