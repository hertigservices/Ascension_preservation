"""Put the published cache data INTO a WoW client -- merged with what is there.

This is the tool for a player, not for the maintainer of the dataset. You have
a WoW 3.3.5a client (Ascension's, or a stock one) and you want it to know the
names, tooltips and quest text that the published caches hold. The client keeps
that knowledge in `Cache/WDB/<locale>/`, one file per kind of thing, and this
script folds the published records into those files without losing your own.

    python tools/install.py                         look: client, folders, plan
    python tools/install.py --write                 do it
    python tools/install.py --client "D:/Games/Ascension" --write
    python tools/install.py --target "[Ascension-Local]" --mode conquest-of-azeroth --write
    python tools/install.py --target root --mode season-10-freepick --write
    python tools/install.py --addons --write        also merge the addon data
    python tools/install.py --fetch                 download the dataset first

Nothing is written unless `--write` is given. Every file that is replaced is
copied first to `Cache/WDB-backup-<timestamp>/` inside the client, so `--undo`
(or dragging the folder back by hand) restores exactly what was there.

HOW THE CLIENT FILES ITS CACHES -- AND WHY THAT DECIDES WHERE THIS WRITES
-------------------------------------------------------------------------
Ascension's client keeps one cache folder PER REALM AND GAME MODE:

    Cache/WDB/enUS/Rexxar - Conquest of Azeroth/itemcache.wdb
    Cache/WDB/enUS/Area 52 - Free-Pick/itemcache.wdb

The folder name is the only place the game mode is recorded, so it is also how
this script decides WHICH published mode to merge into each folder: it reads
"<Realm> - <Mode>" off the folder. A folder whose name does not say the mode --
a local realm you called "[Ascension-Local]", or a stock client's flat
`Cache/WDB/enUS/` root -- has to be told with `--mode`, or with
`--map "<folder>=<mode>"` for several at once.

A stock 3.3.5a client keeps everything flat in `Cache/WDB/enUS/`. That target
is spelled `--target root` here.

MERGING, NOT REPLACING
----------------------
Your own cache is the only record of what YOUR realm told YOUR client, and a
published file can never contain that. So each published cache is merged in:
records you do not have are added, records you already hold are kept as they
are. Where both sides hold a record for the same entry and the bytes differ,
yours wins unless you pass `--prefer archive`. The count of such conflicts is
printed either way.

THE CACHE VERSION -- THE ONE THING THAT MAKES INSTALLED CACHES VANISH
---------------------------------------------------------------------
Every cache file carries a 4-byte "cache version" in its header. On login the
server sends its own number (AzerothCore: `ClientCacheVersion` in
worldserver.conf, or `version.cache_id` in the world DB when that is 0). If the
two differ the client throws its caches away and starts over, and everything
installed here is gone before you can look at it.

This script keeps YOUR existing header (which already carries your realm's
number) whenever you have a file to merge into, and prints the number it ended
up with so you can set the server to match. `--cache-version N` writes a
specific number into every installed file instead. Stock AzerothCore ships with
17. Do not run "Recache" style tools; their whole purpose is to discard caches.

CLOSE THE GAME FIRST. The client rewrites these files on exit, so a cache
installed while it runs is overwritten on the way out and you end up testing
the cache you already had. This script refuses to write while Ascension.exe or
Wow.exe is running.

ADDON DATA (--addons)
---------------------
The published `lua/` files (MobSpells, GatherMate2, Auctionator) are addon
SavedVariables. They are merged into `WTF/Account/<account>/SavedVariables/`
with the same rule -- your data is kept, the published data fills the gaps --
except MobSpells, whose numeric ranges widen the way the addon itself would.
They only show anything if the addon is installed in `Interface/AddOns`.
"""
import argparse
import glob
import json
from pathlib import Path
import gzip
import io
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
import zipfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wdblib
import modes
import luaser

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DATA_MANIFEST = "https://raw.githubusercontent.com/hertigservices/ascension-data/main/datasets/cache.json"
import config
DEFAULT_DATA = config.DATA

# Where a client usually lives, tried in order when --client is not given.
GUESSES = ["C:/Ascension", "C:/Games/Ascension", "C:/Program Files/Ascension",
           "C:/Program Files (x86)/Ascension",
           "C:/Program Files (x86)/World of Warcraft",
           "C:/Program Files/World of Warcraft",
           "C:/Games/World of Warcraft", "C:/WoW", "C:/Games/WoW",
           os.path.expanduser("~/Ascension"),
           os.path.expanduser("~/Documents/Ascension")]

CLIENT_EXES = ("Ascension.exe", "Wow.exe", "WoW.exe")
ROOT = "root"                     # the flat Cache/WDB/<locale>/ target
LOCALES = ("enUS", "enGB", "deDE", "frFR", "esES", "esMX", "ruRU", "koKR",
           "zhCN", "zhTW", "ptBR", "itIT")

# Published caches that may be installed. itemtextcache (mail text) and wowcache
# are never published, so they can never be here; the list is the allow-list.
CACHES = ("itemcache", "creaturecache", "gameobjectcache", "questcache",
          "npccache", "pagetextcache", "itemnamecache")

# Published addon file -> the SavedVariables file it belongs in.
ADDON_FILES = {"MobSpells.lua": "MobSpells.lua",
               "GatherMate2.lua": "GatherMate2.lua",
               "Auctionator_Price_Database.lua": "Auctionator.lua"}
# MobSpells leaf fields that accumulate rather than being kept/filled: the
# smallest hit anyone saw, the largest, and whether a flag was ever observed.
MOBSPELLS_RULES = {"amountMin": "min", "minSpeed": "min",
                   "amountMax": "max", "maxSpeed": "max",
                   "critical": "or", "resisted": "or", "missing": "or",
                   "dodge": "or", "parry": "or", "glancing": "or",
                   "blocked": "or", "resist": "or", "reflect": "or",
                   "interruptable": "or", "immune": "or", "absorbed": "or",
                   "lastGUID": "drop", "lastTime": "drop"}


def size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# ------------------------------------------------------------------ the client
def looks_like_client(root):
    if not root or not os.path.isdir(root):
        return False
    has_exe = any(os.path.exists(os.path.join(root, n)) for n in CLIENT_EXES)
    return os.path.isdir(os.path.join(root, "Cache")) and \
        (has_exe or os.path.isdir(os.path.join(root, "Data")))


def find_client(explicit):
    if explicit:
        root = os.path.abspath(explicit)
        if not looks_like_client(root):
            sys.exit(f"!! {root}\n   does not look like a WoW install: expected a "
                     f"Cache folder beside Wow.exe / Ascension.exe / Data")
        return root
    for g in GUESSES:
        if looks_like_client(g):
            return os.path.abspath(g)
    sys.exit('!! could not find a client. Pass --client "C:/path/to/your/WoW"')


def client_kind(root):
    if os.path.exists(os.path.join(root, "Ascension.exe")):
        return "ascension"
    return "stock"


def running_clients():
    """Names of WoW executables currently running (Windows only; [] elsewhere)."""
    if os.name != "nt":
        return []
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
                             text=True, errors="replace", timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found = set()
    for line in out.splitlines():
        name = line.split('","')[0].strip('"').lower()
        if name in ("ascension.exe", "wow.exe"):
            found.add(name)
    return sorted(found)


def locale_dirs(root):
    wdb = os.path.join(root, "Cache", "WDB")
    if not os.path.isdir(wdb):
        return []
    return [d for d in sorted(os.listdir(wdb))
            if os.path.isdir(os.path.join(wdb, d)) and d in LOCALES] or \
           [d for d in sorted(os.listdir(wdb)) if os.path.isdir(os.path.join(wdb, d))]


class Target:
    """One folder the client reads caches from: a realm folder, or the root."""

    def __init__(self, root, locale, folder):
        self.locale = locale
        self.folder = folder                  # ROOT or "<Realm> - <Mode>"
        base = os.path.join(root, "Cache", "WDB", locale)
        self.path = base if folder == ROOT else os.path.join(base, folder)
        self.mode = None                      # slug, once decided
        self.how = ""                         # how the slug was decided
        self.exists = os.path.isdir(self.path)

    @property
    def label(self):
        return f"{self.locale}/" + ("(root)" if self.folder == ROOT else self.folder)

    def files(self):
        if not self.exists:
            return {}
        out = {}
        for fn in os.listdir(self.path):
            if fn.lower().endswith(".wdb"):
                out[fn.lower()[:-4]] = os.path.join(self.path, fn)
        return out


def discover_targets(root):
    out = []
    for loc in locale_dirs(root):
        base = os.path.join(root, "Cache", "WDB", loc)
        names = sorted(os.listdir(base))
        if any(n.lower().endswith(".wdb") for n in names):
            out.append(Target(root, loc, ROOT))
        for n in names:
            if os.path.isdir(os.path.join(base, n)):
                out.append(Target(root, loc, n))
    return out


def decide_modes(targets, mapping, single_mode, only, available):
    """Give every target a mode slug, or a reason it has none."""
    for t in targets:
        if t.folder in mapping:
            t.mode, t.how = mapping[t.folder], "--map"
        elif single_mode and (only is None or t.folder == only):
            t.mode, t.how = single_mode, "--mode"
        elif t.folder != ROOT:
            m = modes.classify(t.folder)
            if m["mode"] != modes.UNKNOWN:
                t.mode, t.how = m["slug"], "folder name"
        if t.mode and t.mode not in available:
            t.how = f"!! no published mode named {t.mode!r}"
            t.mode = None


# ----------------------------------------------------------------- the dataset
def modes_available(data):
    d = os.path.join(data, "wdb")
    if not os.path.isdir(d):
        return []
    return sorted(x for x in os.listdir(d)
                  if glob.glob(os.path.join(d, x, "*.wdb.gz")))


def fetch_release_dataset(dest):
    """Restore the client files from the current checksummed public Release."""
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'data-storage'))
    import dataset
    request=urllib.request.Request(DATA_MANIFEST,headers={'User-Agent':'AscensionPreservation/1.0'})
    try:
        with urllib.request.urlopen(request,timeout=60) as response:
            raw=response.read(16*1024*1024+1)
    except urllib.error.HTTPError as error:
        if error.code!=404:raise
        # During rollout older data revisions still contain the bulk cache tree.
        with tempfile.TemporaryDirectory(prefix='ascension-legacy-cache-') as temporary:
            archive=Path(temporary)/'data.zip'
            url='https://github.com/hertigservices/ascension-data/archive/refs/heads/main.zip'
            with urllib.request.urlopen(url,timeout=60) as response,archive.open('wb') as output:
                shutil.copyfileobj(response,output,1024*1024)
            return fetch_dataset(dest,archive)
    if len(raw)>16*1024*1024:raise ValueError('Oversized dataset manifest')
    manifest=dataset.validate(json.loads(raw))
    destination=Path(dest).resolve();destination.mkdir(parents=True,exist_ok=True)
    cache=destination.parent/'.ascension-downloads';count=0
    with tempfile.TemporaryDirectory(prefix='ascension-client-data-') as temporary:
        restored=Path(temporary)
        for prefix in ('cachedata/wdb','cachedata/lua'):
            if not any(p.startswith(prefix+'/') for p in manifest['files']):continue
            dataset.restore(manifest,restored,cache,select=prefix)
            for name in manifest['files']:
                if not name.startswith(prefix+'/'):continue
                target=destination/Path(name).relative_to('cachedata')
                current=target
                while current!=destination.parent:
                    if current.is_symlink() or (hasattr(current,'is_junction') and current.is_junction()):raise ValueError('Download destination contains a link')
                    current=current.parent
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(restored/name,target);count+=1
    print(f'  restored and verified {count} client data files into {dest}')
    return count


def fetch_dataset(dest, zip_path=None):
    """Fetch verified Release data, or read an explicitly supplied legacy ZIP."""
    if zip_path is None:return fetch_release_dataset(dest)
    os.makedirs(dest, exist_ok=True)
    tmp = None
    n = 0
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            parts = info.filename.split("/")
            # <repo>-<branch>/cachedata/wdb/<mode>/x.wdb.gz
            if len(parts) < 4 or parts[1] != "cachedata" or info.is_dir():
                continue
            if parts[2] not in ("wdb", "lua", "FILE-GUIDE.md", "README.md"):
                continue
            if parts[2] == "lua" and (len(parts) != 4 or not parts[3].endswith(".lua")):
                continue
            if any(p in (".","..") or "\\" in p or ":" in p for p in parts[2:]):
                raise ValueError("Unsafe archive path")
            rel = os.path.join(*parts[2:])
            out = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with z.open(info) as src, open(out, "wb") as f:
                shutil.copyfileobj(src, f, 1 << 20)
            n += 1
    if tmp is not None:
        try:
            os.remove(zip_path)
        except OSError:
            pass
    print(f"  unpacked {n} file(s) into {dest}")
    return n


# -------------------------------------------------------------------- merging
def read_records(b):
    """{entry: payload} plus the 24-byte header, from cache bytes."""
    recs = {}
    for entry, _size, payload in wdblib.iter_records(b):
        recs[entry] = payload
    return b[:wdblib.HEADER_LEN], recs


def cache_version(header):
    return struct.unpack_from("<I", header, 20)[0]


def with_version(header, n):
    return header[:20] + struct.pack("<I", n & 0xFFFFFFFF)


def with_locale(header, locale):
    loc = locale.encode("latin1", "replace")[:4].ljust(4, b" ")
    return header[:8] + loc[::-1] + header[12:]


def build(header, recs):
    buf = bytearray(header)
    for entry in sorted(recs):
        p = recs[entry]
        buf += struct.pack("<II", entry, len(p)) + p
    buf += b"\x00" * 8
    return bytes(buf)


class Plan:
    """What one cache file in one target folder would become."""

    def __init__(self, cache, target, src_gz):
        self.cache, self.target, self.src = cache, target, src_gz
        self.dst = os.path.join(target.path, cache + ".wdb")
        self.user_records = self.archive_records = 0
        self.added = self.same = self.conflicts = 0
        self.version_in = None
        self.version_out = None
        self.header_from = ""
        self.bytes = None
        self.note = ""
        self.skip = False

    def compute(self, prefer, version_policy, locale):
        with gzip.open(self.src, "rb") as g:
            arch = g.read()
        ainfo = wdblib.inspect(self.src, data=arch)
        if not ainfo.standard or not ainfo.clean_end:
            self.skip, self.note = True, "published file failed its own header check"
            return
        aheader, arecs = read_records(arch)
        self.archive_records = len(arecs)

        user_header, urecs = None, {}
        if os.path.exists(self.dst):
            with open(self.dst, "rb") as f:
                ub = f.read()
            uinfo = wdblib.inspect(self.dst, data=ub)
            if uinfo.standard and uinfo.cache == ainfo.cache:
                if not uinfo.clean_end:
                    self.skip = True
                    self.note = "your file does not end cleanly -- not touching it"
                    return
                user_header, urecs = read_records(ub)
                self.version_in = cache_version(user_header)
            elif uinfo.standard:
                self.skip, self.note = True, f"your file is a {uinfo.cache}, not a {ainfo.cache}"
                return
            else:
                self.skip = True
                self.note = "your file has a non-standard header -- not touching it"
                return
        self.user_records = len(urecs)

        merged = dict(urecs)
        for entry, payload in arecs.items():
            mine = urecs.get(entry)
            if mine is None:
                merged[entry] = payload
                self.added += 1
            elif mine == payload:
                self.same += 1
            else:
                self.conflicts += 1
                if prefer == "archive":
                    merged[entry] = payload

        if user_header is not None:
            header, self.header_from = user_header, "yours"
        else:
            header, self.header_from = with_locale(aheader, locale), "published"
        if version_policy not in ("keep",):
            header = with_version(header, int(version_policy))
        self.version_out = cache_version(header)
        self.merged = merged
        self.bytes = build(header, merged)

        if user_header is not None and self.added == 0 and \
                (prefer != "archive" or self.conflicts == 0) and \
                self.version_out == self.version_in:
            self.skip, self.note = True, "nothing to add -- already has everything"

    def verify(self):
        """Re-parse the bytes about to be written and check every record against
        the decision that produced it. This is the same check rebuild.py makes
        on the published files: a wrong length field or a bad terminator would
        otherwise become a plausible-looking file the client silently rejects."""
        info = wdblib.inspect(self.dst, data=self.bytes)
        want = self.user_records + self.added
        if not info.standard or not info.clean_end:
            return False, "rebuilt file failed the header check"
        if info.records != want:
            return False, f"record count {info.records} != {want}"
        seen = 0
        for entry, _size, payload in wdblib.iter_records(self.bytes):
            if self.merged.get(entry) != payload:
                return False, f"entry {entry} did not survive the rebuild intact"
            seen += 1
        if seen != len(self.merged):
            return False, f"walked {seen} records, expected {len(self.merged)}"
        return True, ""


def plan_targets(targets, data, prefer, version_policy):
    plans = []
    for t in targets:
        if not t.mode:
            continue
        for cache in CACHES:
            src = os.path.join(data, "wdb", t.mode, cache + ".wdb.gz")
            if not os.path.exists(src):
                continue
            p = Plan(cache, t, src)
            p.compute(prefer, version_policy, t.locale)
            plans.append(p)
    return plans


# --------------------------------------------------------------------- addons
def combine(rule, old, new):
    if rule == "drop":
        return old
    if old is None:
        return new
    if new is None:
        return old
    try:
        if rule == "min":
            return min(old, new)
        if rule == "max":
            return max(old, new)
    except TypeError:
        return old
    if rule == "or":
        return bool(old) or bool(new)
    return old                                        # keep what the player has


def deep_merge(dst, src, rules, stats):
    """Fold src (a luaser.Table) into dst in place. Missing keys are added;
    scalar keys already present are kept, or combined by `rules`."""
    for k, v in src.hash.items():
        rule = rules.get(k, "keep") if not isinstance(k, int) else "keep"
        if rule == "drop":
            continue
        if isinstance(v, luaser.Table):
            cur = dst.hash.get(k)
            if isinstance(cur, luaser.Table):
                deep_merge(cur, v, rules, stats)
            elif cur is None:
                dst.hash[k] = v
                stats["added"] += 1
            # a scalar where the archive has a table: keep the player's value
            continue
        if k not in dst.hash:
            dst.hash[k] = v
            stats["added"] += 1
        elif rule in ("min", "max", "or"):
            merged = combine(rule, dst.hash[k], v)
            if merged != dst.hash[k]:
                dst.hash[k] = merged
                stats["widened"] += 1
        elif dst.hash[k] != v:
            stats["kept"] += 1
    for v in src.array:
        if v not in dst.array:
            dst.array.append(v)
            stats["added"] += 1


def account_dirs(root):
    acct = os.path.join(root, "WTF", "Account")
    if not os.path.isdir(acct):
        return []
    return sorted(d for d in os.listdir(acct)
                  if os.path.isdir(os.path.join(acct, d, "SavedVariables")) or
                  os.path.isdir(os.path.join(acct, d)))


def plan_addons(root, data, account):
    """[(published path, destination path, merged text or None, stats, note)]"""
    accts = account_dirs(root)
    if not accts:
        return [], "no WTF/Account folder -- log in once first"
    if account:
        if account not in accts:
            return [], f"no account folder named {account!r}; found: " + ", ".join(accts)
        acct = account
    elif len(accts) == 1:
        acct = accts[0]
    else:
        return [], ("several account folders (" + ", ".join(accts) +
                    "); pick one with --account NAME")
    sv = os.path.join(root, "WTF", "Account", acct, "SavedVariables")
    out = []
    for pub, dst_name in ADDON_FILES.items():
        src = os.path.join(data, "lua", pub)
        if not os.path.exists(src):
            continue
        dst = os.path.join(sv, dst_name)
        stats = {"added": 0, "widened": 0, "kept": 0}
        try:
            new = luaser.load(src)
        except Exception as e:                       # noqa: BLE001
            out.append((src, dst, None, stats, f"published file did not parse: {e}"))
            continue
        if os.path.exists(dst):
            try:
                cur = luaser.load(dst)
            except Exception as e:                   # noqa: BLE001
                out.append((src, dst, None, stats, f"your file did not parse, left alone: {e}"))
                continue
            rules = MOBSPELLS_RULES if pub == "MobSpells.lua" else {}
            for g, v in new.items():
                if g not in cur:
                    cur[g] = v
                    stats["added"] += 1
                elif isinstance(v, luaser.Table) and isinstance(cur[g], luaser.Table):
                    deep_merge(cur[g], v, rules, stats)
            if stats["added"] == 0 and stats["widened"] == 0:
                out.append((src, dst, None, stats, "nothing to add -- your file already has it all"))
                continue
            text = luaser.dumps(cur)
            note = "merge into your file"
        else:
            text = luaser.dumps(new)
            stats["added"] = sum(len(v) if isinstance(v, luaser.Table) else 1
                                 for v in new.values())
            note = "new file (you have none yet)"
        addon_dir = os.path.join(root, "Interface", "AddOns", dst_name[:-4])
        if not os.path.isdir(addon_dir):
            note += f"; addon folder Interface/AddOns/{dst_name[:-4]} not present, so nothing will show it"
        out.append((src, dst, text, stats, note))
    return out, ""


# -------------------------------------------------------------------- writing
def backup_dir(root):
    return os.path.join(root, "Cache", "WDB-backup-" + time.strftime("%Y%m%d-%H%M%S"))


def backup(path, root, bdir):
    """Copy `path` under bdir, keeping its position relative to the client root."""
    rel = os.path.relpath(path, root)
    dst = os.path.join(bdir, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(path, dst)
    return dst


NEW_LIST = "NEW-FILES.txt"


def write_atomic(dst, data, root, bdir):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        backup(dst, root, bdir)
    else:
        # Nothing to back up, but --undo must still know to remove it.
        os.makedirs(bdir, exist_ok=True)
        with open(os.path.join(bdir, NEW_LIST), "a", encoding="utf-8") as f:
            f.write(os.path.relpath(dst, root) + "\n")
    tmp = dst + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, dst)


def do_undo(root):
    dirs = sorted(glob.glob(os.path.join(root, "Cache", "WDB-backup-*")))
    if not dirs:
        sys.exit("!! no WDB-backup-* folder in Cache/ to restore from")
    bdir = dirs[-1]
    n = 0
    newlist = os.path.join(bdir, NEW_LIST)
    if os.path.exists(newlist):
        with open(newlist, encoding="utf-8") as f:
            for rel in f.read().splitlines():
                p = os.path.join(root, rel)
                if rel and os.path.isfile(p):
                    os.remove(p)
                    print(f"  removed  {rel}  (was created by the install)")
                    n += 1
    for dp, _d, fs in os.walk(bdir):
        for fn in fs:
            if fn == NEW_LIST:
                continue
            src = os.path.join(dp, fn)
            rel = os.path.relpath(src, bdir)
            dst = os.path.join(root, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            print(f"  restored {rel}")
            n += 1
    print(f"{n} file(s) restored or removed, from {bdir}")
    print("The backup folder is left in place; delete it yourself once you are sure.")
    return 0


# --------------------------------------------------------------------- report
def print_plan(root, kind, targets, plans, addon_plans, addon_note, prefer):
    print(f"client   {root}  ({'Ascension' if kind == 'ascension' else 'stock 3.3.5a'} client)")
    print("\ncache folders the client has:")
    for t in targets:
        how = f"-> {t.mode}  ({t.how})" if t.mode else (t.how or "no mode: pass --mode or --map")
        files = t.files()
        recs = 0
        for c, p in files.items():
            if c in CACHES:
                try:
                    recs += wdblib.inspect(p).records
                except OSError:
                    pass
        print(f"  {t.label:<45} {len(files):>2} file(s) {recs:>9,} records   {how}")
    if not plans and not addon_plans:
        return
    if plans:
        print(f"\nplan (your record wins a conflict{' -- --prefer archive flips that' if prefer != 'archive' else ''}):")
        print(f"  {'folder / cache':<52}{'yours':>8}{'published':>10}{'+add':>8}"
              f"{'conflict':>9}  {'cache version':<16}")
        for p in plans:
            name = f"{p.target.label}/{p.cache}"
            if p.skip:
                print(f"  {name:<52}{p.user_records:>8,}{p.archive_records:>10,}"
                      f"{'':>8}{'':>9}  skip: {p.note}")
                continue
            ver = f"{p.version_out}" + ("" if p.version_in in (None, p.version_out)
                                        else f" (was {p.version_in})")
            print(f"  {name:<52}{p.user_records:>8,}{p.archive_records:>10,}"
                  f"{p.added:>8,}{p.conflicts:>9,}  {ver:<16}")
    if addon_plans or addon_note:
        print("\naddon SavedVariables:")
        if addon_note:
            print(f"  !! {addon_note}")
        for src, dst, text, st, note in addon_plans:
            print(f"  {os.path.basename(dst):<28} +{st['added']:,} added, "
                  f"{st['widened']:,} widened, {st['kept']:,} kept yours   {note}")


def version_advice(plans):
    vers = sorted({p.version_out for p in plans if not p.skip and p.version_out is not None})
    if not vers:
        return
    print("\ncache version: the server must send the same number or the client")
    print("discards these files at login. In worldserver.conf:")
    for v in vers:
        print(f"    ClientCacheVersion = {v}")
    if len(vers) > 1:
        print("  (folders differ -- each realm keeps its own; set the realm you use)")
    print("  or leave it 0 and set `UPDATE version SET cache_id = N` in the world DB.")
    print("  install.py --cache-version N rewrites the header instead, if you would")
    print("  rather change the files than the server.")


# ----------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Merge the published cache data into a WoW client.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("HOW THE CLIENT")[0])
    ap.add_argument("--client", help="the WoW / Ascension install folder")
    ap.add_argument("--data", default=None,
                    help="the cachedata/ folder from the ascension-data "
                         "repository (default: %s, or "
                         "<client>/cachedata-download when --fetch is used)"
                         % DEFAULT_DATA)
    ap.add_argument("--fetch", action="store_true",
                    help="download the dataset from GitHub into --data first")
    ap.add_argument("--from-zip", metavar="ZIP",
                    help="like --fetch, but from a repository zip already on disk")
    ap.add_argument("--target", action="append", default=[],
                    help='only this cache folder ("Rexxar - Conquest of Azeroth", '
                         'or "root" for the flat layout); repeatable')
    ap.add_argument("--mode", help="published mode slug for --target folders that do "
                                   "not name their mode (see --list-modes)")
    ap.add_argument("--map", action="append", default=[], metavar="FOLDER=MODE",
                    help='fix the mode of one folder, e.g. "[Ascension-Local]=conquest-of-azeroth"')
    ap.add_argument("--create", action="store_true",
                    help="create a --target folder the client has not made yet")
    ap.add_argument("--prefer", choices=("mine", "archive"), default="mine",
                    help="who wins when both hold a record for one entry (default: mine)")
    ap.add_argument("--cache-version", default="keep", metavar="N|keep",
                    help="write this cache version into every installed header "
                         "(default: keep yours; stock AzerothCore uses 17)")
    ap.add_argument("--addons", action="store_true",
                    help="also merge the published addon SavedVariables")
    ap.add_argument("--account", help="WTF/Account folder to receive addon data")
    ap.add_argument("--write", action="store_true", help="actually write (default: show the plan)")
    ap.add_argument("--ignore-running", action="store_true",
                    help="write even though a client process is running (it WILL "
                         "overwrite these files when it exits)")
    ap.add_argument("--undo", action="store_true",
                    help="restore the newest Cache/WDB-backup-* folder and exit")
    ap.add_argument("--list-modes", action="store_true", help="list published modes and exit")
    a = ap.parse_args(argv)

    if a.cache_version != "keep":
        try:
            int(a.cache_version)
        except ValueError:
            sys.exit("!! --cache-version wants a number, or 'keep'")

    root = find_client(a.client)
    if a.undo:
        return do_undo(root)
    kind = client_kind(root)

    data = os.path.abspath(a.data) if a.data else None
    if a.fetch or a.from_zip:
        data = data or os.path.join(root, "cachedata-download")
        fetch_dataset(data, a.from_zip)
    elif data is None:
        data = DEFAULT_DATA
    available = modes_available(data)
    if not available:
        sys.exit(f"!! no dataset at {data}\n   pass --data <cachedata folder>, or "
                 f"--fetch to download it")
    if a.list_modes:
        print("published modes:")
        for m in available:
            files = glob.glob(os.path.join(data, "wdb", m, "*.wdb.gz"))
            print(f"  {m:<24} {len(files)} cache(s), {size(sum(os.path.getsize(f) for f in files))}")
        return 0

    mapping = {}
    for m in a.map:
        if "=" not in m:
            sys.exit(f"!! --map wants FOLDER=MODE, got {m!r}")
        k, v = m.rsplit("=", 1)
        mapping[k.strip()] = v.strip()
    for v in list(mapping.values()) + ([a.mode] if a.mode else []):
        if v not in available:
            sys.exit(f"!! no published mode {v!r}. Available: " + ", ".join(available))

    targets = discover_targets(root)
    if a.target:
        wanted = []
        for name in a.target:
            hit = [t for t in targets if t.folder == name or
                   (name == ROOT and t.folder == ROOT)]
            if not hit:
                if name == ROOT or a.create:
                    locs = locale_dirs(root) or ["enUS"]
                    t = Target(root, locs[0], name)
                    if not a.create and name != ROOT:
                        sys.exit(f"!! no cache folder named {name!r}. Log into that "
                                 f"realm once so the client creates it, or pass --create")
                    hit = [t]
                else:
                    have = ", ".join(repr(t.folder) for t in targets) or "none"
                    sys.exit(f"!! no cache folder named {name!r}. Folders: {have}\n"
                             f"   (pass --create to make it)")
            wanted += hit
        targets = wanted
        only = a.target[0] if len(a.target) == 1 else None
    else:
        only = None
        if a.mode and len(targets) != 1:
            unresolved = [t for t in targets if t.folder not in mapping and
                          (t.folder == ROOT or modes.classify(t.folder)["mode"] == modes.UNKNOWN)]
            if len(unresolved) != 1:
                sys.exit("!! --mode needs --target when the client has several folders "
                         "that do not name their mode: " +
                         ", ".join(repr(t.folder) for t in unresolved))
            only = unresolved[0].folder
    decide_modes(targets, mapping, a.mode, only, available)

    plans = plan_targets(targets, data, a.prefer, a.cache_version)
    addon_plans, addon_note = ([], "")
    if a.addons:
        addon_plans, addon_note = plan_addons(root, data, a.account)
    print_plan(root, kind, targets, plans, addon_plans, addon_note, a.prefer)

    todo = [p for p in plans if not p.skip]
    todo_addons = [x for x in addon_plans if x[2] is not None]
    if not todo and not todo_addons:
        if not any(t.mode for t in targets):
            print("\nnothing to do: no folder has a mode. Use --target/--mode, or --map.")
        else:
            print("\nnothing to do.")
        version_advice(plans)
        return 0

    if not a.write:
        print(f"\ndry run: {len(todo)} cache file(s)"
              + (f" and {len(todo_addons)} addon file(s)" if todo_addons else "")
              + " would be written. Add --write to do it.")
        version_advice(plans)
        return 0

    running = running_clients()
    if running and not a.ignore_running:
        sys.exit(f"\n!! {', '.join(running)} is running. The client rewrites its caches "
                 f"when it exits and would undo this. Quit the game to the desktop, "
                 f"then run again (or --ignore-running if that process is a different client).")

    bdir = backup_dir(root)
    failed = 0
    print(f"\nwriting (backups of replaced files -> {bdir}):")
    for p in todo:
        ok, why = p.verify()
        if not ok:
            print(f"  FAILED   {p.target.label}/{p.cache}: {why}")
            failed += 1
            continue
        write_atomic(p.dst, p.bytes, root, bdir)
        print(f"  wrote    {p.target.label}/{p.cache}.wdb  "
              f"{p.user_records + p.added:,} records, {size(len(p.bytes))}")
    for src, dst, text, st, note in todo_addons:
        write_atomic(dst, text.encode("utf-8"), root, bdir)
        print(f"  wrote    WTF/.../SavedVariables/{os.path.basename(dst)}  +{st['added']:,}")
    version_advice(plans)
    print("\nDone." if not failed else f"\n{failed} file(s) FAILED verification and were not written.")
    print("Run `install.py --undo` to put the replaced files back.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
