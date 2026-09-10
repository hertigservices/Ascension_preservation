"""Pre-publish audit: scan everything about to go public for player data.

Run before any upload. Checks the exported tree for emails, player GUIDs, WTF
account paths, and absolute local paths that leak the maintainer's own machine.
Binary .wdb payloads are scanned as raw bytes too -- a name string inside a record
is still a string.
"""
import os, re, sys, gzip, zlib
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scrub, export

PATTERNS = {
    "email":        re.compile(rb"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}"),
    "player-guid":  re.compile(rb"0x[0-9A-Fa-f]{16}"),
    # Capture the segment AFTER Account/ too.  Without it every hit looks the
    # same, and a description of the path cannot be told from a real login.
    "wtf-account":  re.compile(rb"WTF[\\/]Account[\\/][^\s\\/\"'`)\]]{0,40}", re.I),
    "local-path":   re.compile(rb"C:[\\/](?:Users|AscensionArchive)", re.I),
}
# our own docs legitimately describe the client's cache path
ALLOW_SUBSTR = [b"World of Warcraft\\Cache", b"<redacted",
                # The commit trailer publish.py writes. A documented no-reply
                # mailbox belonging to no one, allowed as this exact string and
                # nothing wider -- any other address still fails.
                b"noreply@anthropic.com",
                # Game text, not a person. The GM "BAN Hammer" spell in the
                # Exiles database mirror tells a banned player to appeal to a
                # mailbox at a TLD that does not exist. Allowed as this exact
                # string and nothing wider -- another name at the same domain,
                # or this name at a real domain, still fails.
                b"techbot@gnome.mail"]

# A placeholder is not a leak.  The tool has to be able to name the path it
# refuses -- "WTF/Account/<login email>/" IS the documentation of the rule --
# and a submitter who redacted their own file by hand leaves the same shape.
# A real exposure has a real login after Account/, never an angle bracket.
PLACEHOLDER = re.compile(rb"[\\/]<[^>]*>?$")


def git_ignored(paths, root):
    """Which of these would git refuse to commit?

    A gitignored file cannot be published -- `git add` will not take it -- so a
    finding in one is a finding nobody can act on.  That is not harmless noise:
    a stray __pycache__ entry failed this gate and stopped publishing outright,
    and a gate that fails for reasons unrelated to publishing is a gate people
    learn to pass with a flag.

    Deliberately narrow.  Only what git itself reports as ignored is dropped;
    an untracked file that is not ignored is still scanned, because `git add -A`
    would commit it.  If this is not a git working tree -- the export directory
    is not one, and it is scanned too -- nothing is skipped.
    """
    if not paths:
        return set()
    base = root if os.path.isdir(root) else os.path.dirname(root)
    # Forward slashes, always. Handed a Windows path, git check-ignore reads
    # the backslashes as escapes, mangles the string and exits 128 -- which
    # the fail-safe below correctly reads as "no answer", so the whole call
    # silently does nothing. Keep the mapping so the reply can be matched
    # back to the caller's own path spelling.
    posix = dict((p.replace("\\", "/"), p) for p in paths)
    try:
        r = subprocess.run(["git", "-C", base, "check-ignore", "--stdin"],
                           input=("\n").join(posix), text=True,
                           capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return set()
    # 0 = some ignored, 1 = none ignored, anything else (128: not a repo) means
    # git did not answer the question and we must not pretend it did.
    if r.returncode not in (0, 1):
        return set()
    out = set()
    for line in r.stdout.splitlines():
        line = line.strip().strip(chr(34))
        if line in posix:
            out.add(posix[line])
    return out


def walk(root, skipped=None):
    """Yield (path, label) for everything to scan.

    A single file is a valid target, not just a directory: the repository's own
    README and LICENSE sit at the root rather than inside one of the folders
    that get walked, and they are published too.
    """
    if os.path.isfile(root):
        yield root, os.path.basename(root)
        return
    found = []
    for dp, _d, fs in os.walk(root):
        for fn in fs:
            p = os.path.join(dp, fn)
            found.append((p, os.path.relpath(p, root).replace("\\", "/")))
    ignored = git_ignored([p for p, _r in found], root)
    for p, rel in found:
        if p in ignored:
            if skipped is not None:
                skipped.append(rel)
            continue
        yield p, rel


def main(root=export.OUT):
    hits = {}
    unreadable = []
    scanned = 0
    skipped_paths = []
    for p, rel in walk(root, skipped_paths):
        with open(p, "rb") as f:
            b = f.read()
        if p.endswith(".gz"):
            # Scan what is INSIDE the archive.  Compressed bytes match none of
            # these patterns, so an archive we cannot open would sail through
            # looking clean -- and now that the whole dataset ships gzipped,
            # that is not a corner case, that is the dataset.  An unreadable
            # member is therefore a failure, never a skip.
            try:
                b = gzip.decompress(b)
            except (OSError, EOFError, zlib.error) as e:
                unreadable.append((rel, str(e)))
                continue
        scanned += 1
        for name, pat in PATTERNS.items():
            for m in pat.finditer(b):
                frag = m.group(0)
                if any(a in frag for a in ALLOW_SUBSTR):
                    continue
                if PLACEHOLDER.search(frag):
                    continue
                hits.setdefault(name, []).append((rel, frag[:80]))
    print(f"scanned {scanned} files under {root}")
    if skipped_paths:
        # Named, not just counted: "skipped 1 file" is indistinguishable
        # from a gate quietly declining to look at the one that mattered.
        print(f"  ({len(skipped_paths)} git-ignored file(s) not scanned, "
              f"they cannot be committed: "
              f"{', '.join(sorted(skipped_paths)[:4])}"
              f"{' ...' if len(skipped_paths) > 4 else ''})")
    if unreadable:
        print(f"\n!! {len(unreadable)} compressed file(s) could not be opened, "
              f"so their contents were never scanned:")
        for rel, why in unreadable[:20]:
            print(f"  {rel}: {why}")
        return 1
    if not hits:
        print("CLEAN: no emails, player GUIDs, account paths or local paths found")
        return 0
    for name, v in hits.items():
        print(f"\n{name}: {len(v)} hit(s)")
        seen = set()
        for rel, frag in v[:20]:
            k = (rel, frag)
            if k in seen:
                continue
            seen.add(k)
            print(f"  {rel}: {frag!r}")
    return 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
