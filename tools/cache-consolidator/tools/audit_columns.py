"""Pre-publish audit: scan everything about to go public for data we LOST.

audit_publish.py asks whether the dataset contains something it must not.
This asks the opposite question, and it is the one that went unasked:

    Does the dataset still contain what it is supposed to contain?

WHY THIS EXISTS

    harvestmerge.to_py() folded a Lua mixed table's array part under a "_array"
    key.  Vendor prices live in the array part.  So info, costInfo and costs
    became unreachable, every price field fell through to its default, and
    3,791 vendor rows were published with money_cost, extended_cost,
    honor_cost, arena_cost and every cost_item column set to zero.

    Every existing check passed.  They had to: rebuild.py re-parses what it
    wrote, luamerge.py re-reads its own output, selftest.py compares published
    .wdb payloads against the client's own bytes -- and the .lua side of the
    dataset has no client bytes to compare against, because the game did not
    write it, an addon did.  Nothing anywhere compared the output against the
    SHAPE the data is supposed to have, so a field that quietly became a
    column of zeros looked exactly like a field that is genuinely zero.

    It is worth being precise about how bad that failure mode is: the loss was
    not merely undetected, it was written down as a finding.  A downstream
    importer read those zeros and recorded in its docstring that "every one of
    the 3,791 captured rows has ... every cost_item column set to zero.  That
    is a gap in the capture, not a discount."  A silent decoder bug had become
    a documented fact about the game.  That is the thing this file is here to
    make impossible.

WHAT IT CHECKS

  1. NO DEAD COLUMN      a .tsv column that holds the same nothing -- "", 0,
                         false, nil -- in every single row is a field nobody
                         decoded until someone writes down why it is empty.
                         This is the check that would have caught the bug: 14
                         of vendors.tsv's 22 columns tripped it.
  2. NO EMPTY TABLE      a .tsv with a header and no rows.
  3. NO DEAD BRANCH      a .json key path that is an empty list or dict at
                         every one of its occurrences.  wildcard.rollSpells
                         and wildcard.events were [] on every realm for the
                         same reason and nothing said so.
  4. NOTHING SHRANK      row counts, list lengths, and the number of rows in
                         which each column carries a real value, all compared
                         against a recorded baseline.  Growth is normal and
                         reported.  A DROP is a failure -- that is rollSpells
                         going 1,592 -> 7 and events 13,499 -> 116, which is
                         what "a list is replaced rather than unioned" looks
                         like from the outside, and it is money_cost going
                         413 -> 0, which is what the original bug looks like.

    A column constant at a real value is a note, not a failure.  Some fields
    honestly only ever hold one value and failing on those would train
    everyone to ignore this tool, which is the only way it can actually break.

    Check 4 is the load-bearing one, and 1 is how you get there.  Being empty
    is a property of the data and is often legitimate; BECOMING empty never
    is.  Check 1 fires on the first run and forces someone to say which of
    those two it is, once; check 4 then holds that answer to account forever,
    for every column, whether or not anything explains it.

EXPLAINING A FINDING RATHER THAN SILENCING IT

    tools/column_expectations.json holds the baseline and the explanations.
    An entry needs a reason in prose, because the reason is the deliverable:

        "constant": {
          "lua/harvest/vendors.tsv:cost_item_2": "No captured vendor item
              costs more than one currency, so the second and third cost
              slots are empty for every row. Verified against the pages
              themselves, not inferred from the column being empty."
        }

    Rewrite the reason when the data changes.  Do not add an entry to make a
    red line go away -- the entry IS the claim that you looked.

    python tools/audit_columns.py             check, exit 1 on any failure
    python tools/audit_columns.py --accept    record current counts as the
                                              baseline (do this only when you
                                              have read what changed)
"""
import os, sys, io, csv, gzip, zlib, json, fnmatch, argparse, textwrap, collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
EXPECT = os.path.join(HERE, "column_expectations.json")

# What "the field was never filled in" looks like once it has been through a
# TSV.  -1 is deliberately NOT here: the merchant API returns -1 for unlimited
# stock, so a column of -1 is a reading, not an absence.
NOTHING = {"", "0", "0.0", "0.000000", "false", "False", "nil", "none",
           "None", "null", "[]", "{}"}

# How much a count may fall before it is called a loss.  Zero tolerance reads
# well but cries wolf on a snapshot branch where the newest capture legitimately
# has fewer rows than the one it replaced; one row in a thousand does not.
SHRINK_TOLERANCE = 0.001

# How many elements of a list to look inside, and how many readings a field
# found INSIDE a list needs before its emptiness counts as a pattern.  A field
# on an object has as many readings as there are objects; a field inside a list
# has only as many as we sampled, so it needs a floor of its own or the tool
# reports "empty everywhere" after looking twice.
LIST_SAMPLE = 200
LIST_MIN_SEEN = 8

# How many keys to name under one shared explanation before summarising the
# rest as a count.  Report named rows, not just counts -- but 1,126 names under
# one sentence is the wall this number exists to stop.
SHOW_PER_REASON = 8


def say(key, msg, width):
    """One finding, with the key printed WHOLE.

    An earlier version padded the key to a fixed width and cut the overflow.
    That turned "...events.[].startingChoice" into "...events.[].start", a key
    which exists in no capture and in no output file, and the better part of an
    hour went into hunting it.  A findings list whose identifiers are
    approximations of identifiers is worse than a line that wraps.
    """
    if len(key) <= width:
        print("  %-*s %s" % (width, key, msg))
    else:
        print("  %s\n      %s" % (key, msg))


def explained(expect, key):
    """The reason recorded for this key, exact match or glob.

    A glob is here for one honest case and not as a way to wave a hand at a
    directory. The .wdb-derived tables are fixed-width record layouts with
    hundreds of fields the client never writes, so their dead columns number in
    the four figures and share a single reason; writing that reason 1,126 times
    would not make it truer. It is safe precisely because a glob cannot hide a
    LOSS -- the per-column count in `counts` is what guards that, and it is
    compared for every column whether or not anything explains it.
    """
    if key.startswith("by-locale/"):
        parts = key.split("/", 2)
        if len(parts) == 3 and parts[2].startswith(("by-mode/", "union/")):
            key = parts[2]
    if key in expect["constant"]:
        return expect["constant"][key]
    for pat, why in expect["constant"].items():
        if ("*" in pat or "?" in pat) and fnmatch.fnmatchcase(key, pat):
            return why
    return None


def load_expect():
    if not os.path.exists(EXPECT):
        return {"constant": {}, "counts": {}}
    with io.open(EXPECT, encoding="utf-8") as f:
        d = json.load(f)
    d.setdefault("constant", {})
    d.setdefault("counts", {})
    return d


def opener(path):
    return gzip.open if path.endswith(".gz") else io.open


def read_text(path):
    """The published tree ships some files gzipped and some not.

    Reading only the plain ones would make this tool silently skip whichever
    half of the dataset happens to be compressed today -- and a check that
    skips its subject reports CLEAN, which is worse than not running.
    """
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
            return f.read()
    with io.open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def walk(root):
    for dp, _d, fs in os.walk(root):
        for fn in fs:
            p = os.path.join(dp, fn)
            low = fn.lower()
            base = low[:-3] if low.endswith(".gz") else low
            if base.endswith(".tsv") or base.endswith(".json"):
                yield p, os.path.relpath(p, root).replace("\\", "/")


def check_tsv(path, rel, expect, fails, notes, counts):
    try:
        text = read_text(path)
    except (OSError, EOFError, zlib.error) as e:
        # A gate that raises is a gate that did not run.  The first real
        # encounter with a truncated .gz in the tree took this whole check
        # offline with a traceback, which reads like a broken tool rather than
        # like a broken dataset -- and the dataset was the broken one.
        fails.append(("UNREADABLE", rel, "%s: %s" % (type(e).__name__, e)))
        return
    # One pass, csv.reader rather than DictReader, and no per-column re-walk of
    # the rows.  The obvious shape -- a set() of each column's values, then a
    # second sweep to count the live ones -- is O(rows x columns) twice over and
    # took this check into double-digit minutes on the 550,000-row item caches.
    # A gate that slow is a gate somebody turns off, which is the same as not
    # having written it.
    rdr = csv.reader(io.StringIO(text), delimiter="\t")
    try:
        header = next(rdr)
    except StopIteration:
        fails.append(("EMPTY TABLE", rel, "no header at all"))
        return
    n = len(header)
    NOSEEN = object()
    first = [NOSEEN] * n
    varies = [False] * n
    # rows in which this column carries a real value. This is the number the
    # shrink check then guards, and it is the load-bearing half of the tool: a
    # column may be dead for good reasons, but a column that WAS alive and is
    # now dead is a bug every time, whatever any explanation says.
    live = [0] * n
    nrows = 0
    for row in rdr:
        nrows += 1
        for i in range(min(n, len(row))):
            v = row[i]
            if v not in NOTHING:
                live[i] += 1
            if first[i] is NOSEEN:
                first[i] = v
            elif not varies[i] and v != first[i]:
                varies[i] = True
    counts["%s:rows" % rel] = nrows
    if not nrows:
        fails.append(("EMPTY TABLE", rel, "header but no rows"))
        return
    for i, c in enumerate(header):
        if not c:
            continue
        key = "%s:%s" % (rel, c)
        counts[key] = live[i]
        if varies[i]:
            continue
        only = "" if first[i] is NOSEEN else first[i]
        why = explained(expect, key)
        if only in NOTHING:
            if why:
                notes.append(("explained", key, "always %r" % (only,),
                              " ".join(why.split())))
            else:
                fails.append(("DEAD COLUMN", key,
                              "%d rows, every one of them %r" % (nrows, only)))
        else:
            notes.append(("constant", key,
                          "%d rows, every one of them %r" % (nrows, only), ""))


def norm(path_parts):
    """Collapse id-shaped and realm-shaped keys so occurrences group together.

    byRealm.Darkmoon.wildcard.events and byRealm.Dawnrise.wildcard.events are
    two readings of one branch.  Keeping them apart would mean a branch that is
    empty on every realm never looks like a pattern, only like a series of
    unrelated single blanks -- which is exactly how the empty rollSpells lists
    stayed invisible.
    """
    out = []
    for p in path_parts:
        s = str(p)
        out.append("*" if (s.isdigit() or ":" in s or len(s) > 24) else s)
    return ".".join(out)


def scan_json(node, parts, seen, depth=0):
    if depth > 12:
        return
    key = norm(parts)
    if isinstance(node, dict):
        seen[key].append(len(node))
        for k, v in node.items():
            scan_json(v, parts + [k], seen, depth + 1)
    elif isinstance(node, list):
        seen[key].append(len(node))
        # A sample, because a 10,000-element list of identical shapes costs
        # minutes and says nothing new.  But a *small* sample is worse than no
        # check: three readings of one field inside a list is not evidence that
        # the field is empty, it is evidence that we looked three times.  See
        # LIST_MIN_SEEN, which is the other half of this compromise.
        for v in node[:LIST_SAMPLE]:
            scan_json(v, parts + ["[]"], seen, depth + 1)


def artifact_keys(o, trail, out, depth=0):
    """Find keys the published data should not contain at all.

    Every other check here asks whether the data still holds what it should.
    This asks the opposite, because the opposite happened: the Lua tables these
    files come from carry an "_array" sidecar beside the real entries, and one
    of them was published inside advancement.json's entries{} looking exactly
    like a node -- a list of 3,075 elements whose first element was None.  It
    went out in a push.  Nothing was watching for a key that should not exist.

    An underscore prefix is the whole rule.  It is crude, and it is free: the
    published tree contains no such key anywhere today, so it raises nothing
    until something like this leaks again.
    """
    if depth > 20:
        return
    if isinstance(o, dict):
        for k, v in o.items():
            if isinstance(k, str) and k.startswith("_"):
                out.append("/".join(trail[-2:] + [k]))
            artifact_keys(v, trail + [str(k)[:20]], out, depth + 1)
    elif isinstance(o, list):
        for v in o:
            artifact_keys(v, trail, out, depth + 1)


def check_json(path, rel, expect, fails, notes, counts):
    try:
        obj = json.loads(read_text(path))
    except (ValueError, OSError, EOFError, zlib.error) as e:
        fails.append(("UNREADABLE", rel, "%s: %s" % (type(e).__name__, str(e)[:110])))
        return
    seen = collections.defaultdict(list)
    # byRealm.json's top level is one bucket per realm, and those buckets have
    # to group or a branch that is empty on every realm reads as a series of
    # unrelated single blanks, never as a pattern -- see norm().  norm() does
    # group them today, but only because it collapses any key over 24
    # characters and "Darkmoon - Season 10 Wildcard" happens to be 29.  That is
    # luck, not design: a realm called "Area 52" would keep its own key, be the
    # only reading of every branch beneath it, and never reach the floor below
    # -- so a dead branch on that realm would be silently skipped by the tool
    # written to catch dead branches.  Name the bucket instead of hoping.
    if os.path.basename(rel).lower() == "byrealm.json" and isinstance(obj, dict):
        for v in obj.values():
            scan_json(v, ["*"], seen, 1)
    else:
        scan_json(obj, [], seen)
    art = []
    artifact_keys(obj, [], art)
    for where in sorted(set(art)):
        full = "%s:%s" % (rel, where)
        why = explained(expect, full)
        if why:
            notes.append(("explained", full, "serializer artifact",
                          " ".join(why.split())))
        else:
            fails.append(("ARTIFACT KEY", full,
                          "a key the published data should not contain"))
    for key, lens in sorted(seen.items()):
        if not key:
            continue
        full = "%s:%s" % (rel, key)
        total = sum(lens)
        counts[full] = total
        floor = LIST_MIN_SEEN if "[]" in key else 2
        if len(lens) >= floor and total == 0:
            why = explained(expect, full)
            if why:
                notes.append(("explained", full,
                              "empty in all %d places" % len(lens),
                              " ".join(why.split())))
            else:
                fails.append(("DEAD BRANCH", full,
                              "empty in all %d places it appears" % len(lens)))


def compare_counts(expect, counts, fails, grew):
    base = expect["counts"]
    for k in sorted(set(counts) | set(base)):
        now = counts.get(k, 0)
        was = base.get(k)
        if was is None:
            continue
        if k not in counts or now < was - max(1, int(was * SHRINK_TOLERANCE)):
            fails.append(("SHRANK", k, "%d -> %d (%+d)" % (was, now, now - was)))
        elif now > was:
            grew.append((k, was, now))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", nargs="?", default=config.OUT)
    ap.add_argument("--accept", action="store_true",
                    help="record the current counts as the baseline")
    a = ap.parse_args(argv)

    expect = load_expect()
    fails, notes, grew = [], [], []
    counts = {}
    n = 0
    for p, rel in walk(a.root):
        n += 1
        low = rel.lower()
        base = low[:-3] if low.endswith(".gz") else low
        if base.endswith(".tsv"):
            check_tsv(p, rel, expect, fails, notes, counts)
        else:
            check_json(p, rel, expect, fails, notes, counts)
    compare_counts(expect, counts, fails, grew)

    print("scanned %d table/JSON file(s) under %s" % (n, a.root))

    if grew:
        print("\ngrew since the baseline (%d):" % len(grew))
        for k, was, now in grew[:15]:
            print("  %-58s %d -> %d (%+d)" % (k[:58], was, now, now - was))
        if len(grew) > 15:
            print("  ... and %d more" % (len(grew) - 15))

    if notes:
        exp = [x for x in notes if x[0] == "explained"]
        con = [x for x in notes if x[0] == "constant"]
        if exp:
            # Grouped by reason, and each reason printed ONCE.  These reasons
            # are paragraphs on purpose -- the prose is the deliverable -- but
            # the first real run printed the same 200-word paragraph 1,133
            # times and buried the failures under 20,000 characters of it.  A
            # report nobody can read is a report nobody reads, which lands in
            # the same place as not having run the check.
            by_why = collections.OrderedDict()
            for _t, k, what, why in exp:
                by_why.setdefault(why, []).append((k, what))
            print("\nexplained, and the explanation still stands "
                  "(%d key(s), %d reason(s)):" % (len(exp), len(by_why)))
            for why, items in by_why.items():
                print("")
                for k, what in items[:SHOW_PER_REASON]:
                    say(k, what, 46)
                if len(items) > SHOW_PER_REASON:
                    print("  ... and %d more key(s) sharing this reason"
                          % (len(items) - SHOW_PER_REASON))
                for line in textwrap.wrap(why, 72):
                    print("      | %s" % line)
        if con:
            print("\nconstant at a real value -- a note, not a failure (%d):" % len(con))
            for _t, k, msg, _w in con[:20]:
                say(k, msg, 46)
            if len(con) > 20:
                print("  ... and %d more" % (len(con) - 20))

    if a.accept:
        expect["counts"] = counts
        with io.open(EXPECT, "w", encoding="utf-8", newline="\n") as f:
            json.dump(expect, f, indent=2, sort_keys=True, ensure_ascii=False)
            f.write("\n")
        print("\nbaseline recorded: %d counts in %s" % (len(counts), EXPECT))
        if fails:
            print("NOTE: %d failure(s) below were NOT accepted; --accept only "
                  "records counts." % len(fails))

    if not fails:
        print("\nCLEAN: no dead columns, no dead branches, no stray keys, nothing shrank")
        return 0

    by_kind = collections.defaultdict(list)
    for kind, k, msg in fails:
        by_kind[kind].append((k, msg))
    print("\n%s" % ("=" * 70))
    print("!! %d FINDING(S) -- do not publish until every one is explained."
          % len(fails))
    for kind, items in sorted(by_kind.items()):
        print("\n%s: %d" % (kind, len(items)))
        for k, msg in items[:25]:
            say(k, msg, 52)
        if len(items) > 25:
            print("  ... and %d more" % (len(items) - 25))
    if "UNREADABLE" in by_kind:
        # Every time this has fired for real, the bytes were fine and something
        # was still writing them -- a second pipeline run, or the tray
        # publishing while a shell run exported into the same tree.  Corruption
        # and a half-written file are the same EOFError, so say so here rather
        # than let the next reader spend an afternoon on a damaged archive that
        # does not exist.
        for line in textwrap.wrap(
                "UNREADABLE usually means SOMETHING IS WRITING THAT FILE RIGHT "
                "NOW, not that it is damaged: a truncated read and a "
                "concurrent write are the same error. Check for another "
                "pipeline or tray run first, then re-test the file once the "
                "disk is quiet -- publish.py takes a lock to stop exactly that "
                "overlap, so a run started outside it is the usual culprit.", 72):
            print("  " + line)

    print("\nEach one is either a bug in the decoder or a fact about the game.")
    print("Decide which, fix it or write the reason into %s"
          % os.path.relpath(EXPECT, os.path.dirname(HERE)).replace("\\", "/"))
    return 1


if __name__ == "__main__":
    sys.exit(main())
