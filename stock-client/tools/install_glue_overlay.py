#!/usr/bin/env python3
"""Install the loose GlueXML overlay (the Ascension class chooser) into a client.

    python install_glue_overlay.py --stock-ui-tree <stock-ui-tree>\\Interface --install <client root>
                                   [--uninstall]

Writes <client>\\Interface\\GlueXML\\GlueXML.toc (the STOCK toc with AscensionCreate.xml added
before GlueLocalizationPost.xml), AscensionCreate.xml/.lua and the generated
AscensionCreateData.lua. Loose glue files override the archive copies; the reviewed
executable patch already disables the GlueXML signature check that would otherwise refuse
them. --uninstall removes exactly those four files (the directory is left in place).
"""
import argparse, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "client", "Interface", "GlueXML")
FILES = ["AscensionCreate.xml", "AscensionCreate.lua", "AscensionCreateData.lua"]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stock-ui-tree", required=True)
    ap.add_argument("--install", required=True)
    ap.add_argument("--uninstall", action="store_true")
    a = ap.parse_args()
    dst = os.path.join(a.install, "Interface", "GlueXML")
    if a.uninstall:
        for f in FILES + ["GlueXML.toc"]:
            p = os.path.join(dst, f)
            if os.path.exists(p):
                os.remove(p)
                print("removed", p)
        return 0
    stock_toc = os.path.join(a.stock_ui_tree, "GlueXML", "GlueXML.toc")
    lines = open(stock_toc, encoding="utf-8-sig").read().splitlines()
    out = []
    added = False
    for line in lines:
        if line.strip().lower() == "gluelocalizationpost.xml" and not added:
            out.append("AscensionCreate.xml")
            added = True
        out.append(line)
    if not added:
        out.append("AscensionCreate.xml")
    os.makedirs(dst, exist_ok=True)
    for f in FILES:
        src = os.path.join(SRC, f)
        if not os.path.exists(src):
            print("!! missing", src, "(run gen_glue_data.py first)")
            return 1
        with open(src, "rb") as i, open(os.path.join(dst, f), "wb") as o:
            o.write(i.read())
    with open(os.path.join(dst, "GlueXML.toc"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(out) + "\n")
    print("glue overlay installed into", dst, "(%d toc lines)" % len(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
