#!/usr/bin/env python3
"""Turn a stock 3.3.5a (12340) client plus Ascension's data archives into a CoA client.

    python install_client.py --stock <ChromieCraft client dir> --ascension-data <dir with Ascension's Data\\patch-*.MPQ>
                             --out <new client dir> [--realmlist 127.0.0.1:3724] [--copy] [--dry-run]
                             [--pack build/p2-panel] [--no-pack] [--no-glue]

What it does, in order (all idempotent; nothing under --stock or --ascension-data is ever written):
  1. links (NTFS hardlinks on the same volume; --copy to copy) the stock base + enUS archives
     and Ascension's single-letter patch archives, minus patch-B (Ascension's Interface tree,
     which needs Extensions.dll; the addon pack replaces it);
  2. exposes Ascension's multi-letter archives (CA.., CHA, TA/TM/TW, W..) under free single
     character slots the stock loader can see, keeping the recorded overlap winners
     (tools/mpq_slots.py, data/mpq-overlap.txt) and writes ARCHIVE-MAP.txt;
  3. writes Data\\patch-Y.MPQ from server/dbc-overrides (stock ChrClasses / CharBaseInfo /
     CharStartOutfit so character creation survives, Ascension's SkillRaceClassInfo opened to
     carrier classes) -- loose DBFilesClient\\ copies are ignored by the stock loader;
  4. copies the loose runtime files, patches Wow.exe (client/patch_wow.py: signature caller
     dispatch, LAA, 128 archive slots, keep loose UI folders), writes realmlist.wtf and a
     first Config.wtf;
  5. installs the addon pack (build/p2-panel, made by tools/assemble_panel_pack.py) and the
     GlueXML class-chooser overlay (client/Interface/GlueXML).

Sources of the file lists: p0_mpq_reach.py / p0_dbc_reach.py measurements (2026-09-09), see DESIGN.md.
"""
import argparse, os, shutil, subprocess, sys, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STOCK_BASE = ["common.MPQ", "common-2.MPQ", "expansion.MPQ", "lichking.MPQ", "patch.MPQ", "patch-2.MPQ", "patch-3.MPQ"]
STOCK_ENUS = ["backup-enUS.MPQ", "base-enUS.MPQ", "expansion-locale-enUS.MPQ", "expansion-speech-enUS.MPQ",
              "lichking-locale-enUS.MPQ", "lichking-speech-enUS.MPQ", "locale-enUS.MPQ", "patch-enUS-2.MPQ",
              "patch-enUS-3.MPQ", "patch-enUS.MPQ", "speech-enUS.MPQ"]
# NOT enUS\patch-enUS-4.MPQ: ChromieCraft's own AreaTable/MapDifficulty, which must not shadow Ascension's.
ASC_EXCLUDED = ["patch-B.MPQ"]
LOOSE_RUNTIME = ["Battle.net.dll", "DivxDecoder.dll", "Scan.dll", "dbghelp.dll", "ijl15.dll", "msvcr80.dll",
                 "unicows.dll", "WowError.exe", "Repair.exe"]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def same_file(a, b):
    try:
        sa, sb = os.stat(a), os.stat(b)
    except OSError:
        return False
    return (sa.st_ino, sa.st_dev) == (sb.st_ino, sb.st_dev) and sa.st_size == sb.st_size


def place(src, dst, dry, copy):
    if not os.path.exists(src):
        print("  !! missing source", src)
        return False
    if os.path.exists(dst):
        if same_file(src, dst) or (copy and os.path.getsize(src) == os.path.getsize(dst)):
            return True
        if dry:
            print("  would replace", dst)
            return True
        os.remove(dst)
    if dry:
        print("  would %s %s -> %s" % ("copy" if copy else "link", dst, src))
        return True
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if copy:
        shutil.copy2(src, dst)
        return True
    try:
        os.link(src, dst)
    except OSError as ex:
        print("  hardlink failed (%s), copying instead" % ex)
        shutil.copy2(src, dst)
    return True


def write_if_differs(path, text, dry):
    cur = open(path, encoding="utf-8", errors="replace").read() if os.path.exists(path) else None
    if cur == text:
        return
    if dry:
        print("  would write", path)
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stock", required=True, help="stock 3.3.5a client (ChromieCraft), read only")
    ap.add_argument("--ascension-data", required=True, help="directory holding Ascension's Data\\patch-*.MPQ archives, read only")
    ap.add_argument("--out", required=True, help="the client to create / refresh")
    ap.add_argument("--realmlist", default="127.0.0.1:3724")
    ap.add_argument("--realm-name", default="", help="realm the client should log into when the list has more than one "
                    "(written as SET realmName; without it a fresh client stops at the Realm Selection dialog)")
    ap.add_argument("--resolution", default="1600x900", help="the Collections panel is 1294 UI units wide; 1600x900 fits it")
    ap.add_argument("--pack", default=os.path.join(ROOT, "build", "p2-panel"), help="assembled addon pack (Interface\\AddOns inside)")
    ap.add_argument("--stock-ui-tree", help="stock GlueXML.toc source for the glue overlay (default: extracted from --stock is NOT attempted; pass ...\\stock-ui-tree\\Interface)")
    ap.add_argument("--no-pack", action="store_true")
    ap.add_argument("--no-glue", action="store_true")
    ap.add_argument("--copy", action="store_true", help="copy archives instead of hardlinking")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    dry, out = a.dry_run, a.out
    asc = a.ascension_data
    if os.path.isdir(os.path.join(asc, "Data")) and not any(n.lower().startswith("patch-") for n in os.listdir(asc)):
        asc = os.path.join(asc, "Data")
    print("assembling", out, "(dry run)" if dry else "")
    for sub in (("Data", "enUS"), ("Interface", "AddOns"), ("WTF",), ("Cache",)):
        if not dry:
            os.makedirs(os.path.join(out, *sub), exist_ok=True)

    print("stock base archives")
    for n in STOCK_BASE:
        place(os.path.join(a.stock, "Data", n), os.path.join(out, "Data", n), dry, a.copy)
    print("stock enUS archives")
    for n in STOCK_ENUS:
        place(os.path.join(a.stock, "Data", "enUS", n), os.path.join(out, "Data", "enUS", n), dry, a.copy)

    archives = sorted(n for n in os.listdir(asc) if n.lower().startswith("patch-") and n.lower().endswith(".mpq"))
    single = [n for n in archives if len(n[6:-4]) == 1 and n not in ASC_EXCLUDED]
    print("Ascension single-letter patches (%d)" % len(single))
    for n in single:
        place(os.path.join(asc, n), os.path.join(out, "Data", n), dry, a.copy)

    print("Ascension multi-letter patches (mapped onto free single-character slots)")
    ms = load("mpq_slots", os.path.join(HERE, "mpq_slots.py"))
    overlap = os.path.join(ROOT, "data", "mpq-overlap.txt")
    mapping, notes = ms.plan(archives, ms.parse_overlaps(overlap) if os.path.exists(overlap) else [], excluded=tuple(ASC_EXCLUDED))
    if any(n.startswith("NO SLOT") for n in notes):
        print("  !! incomplete archive plan; refusing assembly:", notes)
        return 1
    mapped = set()
    lines = ["# archive map: Ascension archive -> name the stock loader sees",
             "# order = stock load order; later overrides earlier; computed by mpq_slots.py from data/mpq-overlap.txt", ""]
    for src_name in sorted(mapping, key=lambda x: ms.suffix(x)):
        slot = mapping[src_name]
        sub = "enUS" if "-enUS-" in slot else ""
        mapped.add(os.path.normcase(os.path.join(sub, slot)))
        place(os.path.join(asc, src_name), os.path.join(out, "Data", sub, slot) if sub else os.path.join(out, "Data", slot), dry, a.copy)
        lines.append("%-16s -> %s" % (src_name, (sub + "\\" if sub else "") + slot))
    write_if_differs(os.path.join(out, "ARCHIVE-MAP.txt"), "\n".join(lines) + "\n", dry)
    print("  %d archives mapped; see ARCHIVE-MAP.txt" % len(mapping))
    for n in ASC_EXCLUDED:
        p = os.path.join(out, "Data", n)
        if os.path.normcase(n) not in mapped and os.path.exists(p):
            print("  removing excluded", p)
            if not dry:
                os.remove(p)

    print("patch-Y.MPQ (DBC overrides)")
    override_dir = os.path.join(ROOT, "server", "dbc-overrides")
    mw = load("mpqwrite", os.path.join(HERE, "mpqwrite.py"))
    files = [("DBFilesClient\\" + f, open(os.path.join(override_dir, f), "rb").read())
             for f in sorted(os.listdir(override_dir)) if f.lower().endswith(".dbc")]
    data = mw.build(files)
    ypath = os.path.join(out, "Data", "patch-Y.MPQ")
    if not dry and not (os.path.exists(ypath) and open(ypath, "rb").read() == data):
        with open(ypath, "wb") as f:
            f.write(data)
    print("  %d DBCs: %s" % (len(files), ", ".join(n.split("\\")[-1] for n, _ in files)))

    print("loose runtime files")
    for n in LOOSE_RUNTIME:
        s, d = os.path.join(a.stock, n), os.path.join(out, n)
        if os.path.exists(s) and not (os.path.exists(d) and os.path.getsize(d) == os.path.getsize(s)):
            if dry:
                print("  would copy", d)
            else:
                shutil.copy2(s, d)

    print("patched Wow.exe")
    if not dry:
        r = subprocess.run([sys.executable, os.path.join(ROOT, "client", "patch_wow.py"), os.path.join(a.stock, "Wow.exe"), os.path.join(out, "Wow.exe")],
                           capture_output=True, text=True)
        print("  " + r.stdout.strip().replace("\n", "\n  "))
        if r.returncode:
            print("  !! patcher failed:", r.stderr.strip())
            return 1

    print("realmlist / Config.wtf")
    write_if_differs(os.path.join(out, "Data", "enUS", "realmlist.wtf"),
                     'set realmlist %s\nset patchlist 127.0.0.1\nset realmlistbn ""\n' % a.realmlist, dry)
    cfg = os.path.join(out, "WTF", "Config.wtf")
    if not os.path.exists(cfg):
        # hwDetect 0: otherwise the first launch runs hardware detection and replaces the
        # requested resolution with its own pick (measured: 1600x900 asked, 1024x768 window).
        text = ('SET realmList "%s"\nSET patchlist "127.0.0.1"\nSET locale "enUS"\nSET gxWindow "1"\nSET gxMaximize "0"\n'
                'SET gxResolution "%s"\nSET hwDetect "0"\nSET videoOptionsVersion "3"\nSET readTOS "1"\nSET readEULA "1"\n'
                'SET readTerminationWithoutNotice "1"\nSET readScanning "-1"\nSET readContest "-1"\nSET accounttype "LK"\n'
                'SET movie "0"\nSET checkAddonVersion "0"\nSET scriptErrors "1"\n') % (a.realmlist, a.resolution)
        if a.realm_name:
            text += 'SET realmName "%s"\n' % a.realm_name.replace('"', '')
        write_if_differs(cfg, text, dry)

    if not a.no_pack:
        src = os.path.join(a.pack, "Interface", "AddOns")
        if not os.path.isdir(src):
            print("  !! no addon pack at %s (run assemble_panel_pack.py, or --no-pack)" % src)
            return 1
        print("addon pack from", src)
        dst = os.path.join(out, "Interface", "AddOns")
        for name in os.listdir(src):
            target = os.path.join(dst, name)
            if dry:
                print("  would install", target)
                continue
            if os.path.exists(target):
                shutil.rmtree(target)
            shutil.copytree(os.path.join(src, name), target)
    if not a.no_glue:
        if not a.stock_ui_tree:
            print("  !! --stock-ui-tree not given: glue overlay (class chooser) skipped")
        elif not dry:
            r = subprocess.run([sys.executable, os.path.join(HERE, "install_glue_overlay.py"), "--stock-ui-tree", a.stock_ui_tree, "--install", out],
                               capture_output=True, text=True)
            print("  " + (r.stdout or r.stderr).strip())
    print("done:", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
