#!/usr/bin/env python3
"""List XML virtual templates that the pack redefines under a name the stock 3.3.5a
FrameXML already owns.  The stock client keeps the FIRST definition of a template name
(its own), so every collision silently loads the addon frame from the stock template --
different children, no parentKeys, wrong scripts.

    python template_collisions.py --stock <stock-ui-tree>\\Interface --pack build/p2-panel

Prints one line per colliding name: where the stock copy lives and where the pack copy
lives.  Read-only.
"""
import argparse, os, re, sys

TPL_RE = re.compile(r'<\s*([A-Za-z]+)\b[^>]*\bname\s*=\s*"([^"]+)"[^>]*\bvirtual\s*=\s*"true"', re.I)
TPL_RE2 = re.compile(r'<\s*([A-Za-z]+)\b[^>]*\bvirtual\s*=\s*"true"[^>]*\bname\s*=\s*"([^"]+)"', re.I)


def templates(root):
    out = {}
    for base, _, files in os.walk(root):
        for f in files:
            if not f.lower().endswith(".xml"):
                continue
            p = os.path.join(base, f)
            text = open(p, encoding="utf-8", errors="replace").read()
            for rx in (TPL_RE, TPL_RE2):
                for m in rx.finditer(text):
                    out.setdefault(m.group(2), []).append((m.group(1), os.path.relpath(p, root)))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stock", required=True)
    ap.add_argument("--pack", required=True)
    a = ap.parse_args()
    stock = templates(a.stock)
    pack = templates(a.pack)
    names = sorted(set(stock) & set(pack))
    for n in names:
        print("%-40s stock: %-45s pack: %s" % (n, stock[n][0][1], ", ".join(sorted({p for _, p in pack[n]}))))
    print("%d colliding template name(s); pack defines %d, stock defines %d" % (len(names), len(pack), len(stock)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
