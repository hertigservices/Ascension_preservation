#!/usr/bin/env python3
"""Assemble the P2 panel pack: the core preview shim + Ascension's SharedXML/FrameXML
compat layer + the original Collections/TalentUI/CoATalents addons, and install it
into a client's Interface\\AddOns.

    python assemble_panel_pack.py --core build/p2-core-preview --ui-tree <ui-tree>\\Interface
                                  --out build/p2-panel --install C:\\AzerothRealm\\client-coa

Layering (load order):
  !AscensionShim   = Codex core (data/api/events) + compat/ (Ascension SharedXML & FrameXML
                     files verbatim, in Ascension's own FrameXML.toc order) + zz_StockShim.lua
                     (our glue: metatable getters, UnitClass override, LoadUI, strings, error log)
  AscensionResources, Ascension_Collections, Ascension_TalentUI, Ascension_CoATalents = verbatim.

The compat MANIFEST below is the first-iteration selection; extend it when a load error
names a missing global. Nothing under the ui-tree is modified.
"""
import argparse, os, shutil, sys

# SharedXML/FrameXML files, in the order Ascension's FrameXML.toc loads them (subset).
MANIFEST = [
    "SharedXML/Util/Const.lua",
    "SharedXML/Util/GarbageCollectionUtil.lua",
    "SharedXML/TypeExtensions/TypeExtensions.xml",
    "SharedXML/Util/Mixin.lua",
    "SharedXML/Util/Util.lua",
    "SharedXML/Util/ColorUtil.lua",
    "SharedXML/Util/Vector.lua",
    "SharedXML/Util/DrawUtil.lua",
    "SharedXML/Util/Timer.lua",
    "SharedXML/Util/C_Deflate.lua",
    "SharedXML/Util/C_Serialize.lua",
    "SharedXML/AtlasInfo.lua",
    "SharedXML/Enum.lua",
    "SharedXML/Util/SoundKit.lua",
    "SharedXML/Util/SoundKitExtra.lua",
    "SharedXML/SharedConstants.lua",
    "FrameXML/Constants.lua",
    "SharedXML/CallbackRegistryMixin.lua",
    "SharedXML/GlobalCallbackRegistry.lua",
    "SharedXML/DataProvider.lua",
    "FrameXML/Fonts.xml",
    "FrameXML/FontStyles.xml",
    "SharedXML/CustomFonts.xml",
    "SharedXML/CustomFontStyles.xml",
    "SharedXML/Util/TableUtil.lua",
    "SharedXML/Backdrop.xml",
    "SharedXML/ButtonStateBehavior.lua",
    "SharedXML/C_Hook.lua",
    "SharedXML/Util/KeyCommand.lua",
    "SharedXML/Util/TextureUtil.lua",
    "SharedXML/Util/C_TrinityCore.lua",
    "SharedXML/Pools.lua",
    "SharedXML/NineSliceLayouts.lua",
    "SharedXML/NineSlice.lua",
    "SharedXML/Util/FunctionUtil.lua",
    "SharedXML/Util/TimeUtil.lua",
    "SharedXML/Util/C_Flipbook.lua",
    "SharedXML/Util/EventUtil.lua",
    "SharedXML/Util/FrameUtil.lua",
    "SharedXML/Util/PixelUtil.lua",
    "SharedXML/Util/AnchorUtil.lua",
    "SharedXML/Util/GridLayoutUtil.lua",
    "SharedXML/Util/ClassInfoUtil.lua",
    "SharedXML/TypeExtensions/Region.xml",
    "SharedXML/TabSystem/TabSystem.xml",
    "SharedXML/Scroll/Scroll.xml",
    "SharedXML/AnimationTemplates.xml",
    "SharedXML/HelpTip.xml",
    "SharedXML/LayoutFrame.xml",
    "SharedXML/SharedTemplates.xml",
    "SharedXML/SharedPanelTemplates.xml",
    "SharedXML/UIDropDownMenu.xml",
    "SharedXML/ScrollableDropDown.xml",
    "SharedXML/FilterDropDown.xml",
    "FrameXML/UIPanelTemplates.lua",
    "FrameXML/UIPanelTemplates.xml",
    "SharedXML/EffectTemplates.xml",
    "FrameXML/Util/C_Player.lua",
    "FrameXML/Util/C_GameMode.lua",
    "FrameXML/Util/C_PopupQueue.lua",
    "FrameXML/Util/C_Spell.lua",
    "FrameXML/Objects/Spell.lua",
    "FrameXML/Util/C_PrimaryStat.lua",
    "FrameXML/Util/SpecializationUtil.lua",
    "FrameXML/Util/CharacterAdvancementUtil.lua",
    "FrameXML/Util/CharacterAdvancementCostUtil.lua",
    "FrameXML/Util/BuildCreatorUtil.lua",
    "FrameXML/Util/TokenUtil.lua",
    "FrameXML/Util/VanityCollectionUtil.lua",
    "FrameXML/Util/TalentUtil.lua",
    "FrameXML/CurrencyBar.xml",
    "FrameXML/IconSelectorFrame.xml",
    "FrameXML/SpecListItem.xml",
    "FrameXML/SpecializationMenu.xml",
    "FrameXML/CharacterAdvancement/CharacterAdvancementBaseTemplates.xml",
    # P6: the classic (Hero / Free-Pick) Character Advancement panel and the Mystic Enchant panel
    "SharedXML/Util/HyperlinkUtil.lua",
    "SharedXML/Util/AccessibilityUtil.lua",
    "FrameXML/Data/Items.lua",
    "FrameXML/Objects/Item.lua",
    "FrameXML/Util/DraftUtil.lua",
    "FrameXML/SpellListItem.xml",
    "FrameXML/Util/MysticEnchantUtil.lua",
    "FrameXML/Util/CostUtil.lua",
    "FrameXML/Util/EnchantCollectionUtil.lua",
    "FrameXML/Util/MysticEnchantManagerUtil.lua",
    "AddOns/AscensionUI/Shared/IconSelector.lua",
]
# Whole directories copied so XML <Script>/<Include> references resolve (relative paths).
DIRS = ["SharedXML/TypeExtensions", "SharedXML/TabSystem", "SharedXML/Scroll", "FrameXML/CharacterAdvancement"]
ADDONS = ["AscensionResources", "Ascension_Collections", "Ascension_TalentUI", "Ascension_CoATalents", "Ascension_BuildCreator",
          "Ascension_CharacterAdvancement", "Ascension_EnchantCollection"]
# (relative path under compat/, exact text before, text after) - see client/PATCHES.md
TEXT_PATCHES = [
    # The Util is FrameXML on Ascension, so issecure() holds when it writes WTF\MysticEnchantSaved.wtf.
    # In the pack it is addon code, issecure() is always false, and every preset save would be refused.
    ("FrameXML/Util/MysticEnchantManagerUtil.lua",
     "\tif not issecure() then\n\t\treturn C_Logger.Error(\"Tried to write %s.wtf from insecure code!\", saveFile)\n\tend\n",
     "\t-- stock-client port: this file loads as addon code, so the issecure() guard is removed here\n"),
]
HERE = os.path.dirname(os.path.abspath(__file__))
GLUE = os.path.join(HERE, "..", "client", "Interface", "AddOns", "!AscensionShim", "compat", "zz_StockShim.lua")


def copytree(src, dst):
    for root, _, files in os.walk(src):
        for f in files:
            s = os.path.join(root, f)
            d = os.path.join(dst, os.path.relpath(s, src))
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(s, d)


# ---------------------------------------------------------------------------------------
# Template-name collisions.  The stock client keeps the FIRST definition of a virtual
# template name, and its own FrameXML loads before any addon: every Ascension template
# that reuses a stock name (UIDropDownMenuTemplate, UIPanelButtonTemplate, the
# HybridScrollFrame family, ...) would silently be replaced by the stock one, which has
# different children and no parentKeys (measured: CreateFrame(..., "UIDropDownMenuTemplate")
# .Button == nil while Ascension-only names work).  So the pack renames every colliding
# non-font template to ASC_<name> and rewrites all references (XML inherits lists, Lua
# string literals) in the copied compat files and addons.  Fonts are left alone: the stock
# font object of the same name exists, so nothing breaks, it merely renders in stock metrics.
# ---------------------------------------------------------------------------------------
import re
TPL_RES = [re.compile(r'<\s*([A-Za-z]+)\b[^>]*\bname\s*=\s*"([^"]+)"[^>]*\bvirtual\s*=\s*"true"', re.I),
           re.compile(r'<\s*([A-Za-z]+)\b[^>]*\bvirtual\s*=\s*"true"[^>]*\bname\s*=\s*"([^"]+)"', re.I)]
FONT_TAGS = {"font", "fontfamily", "fontstring"}


def xml_templates(root):
    """name -> element type for every virtual template under root."""
    out = {}
    for base, _, files in os.walk(root):
        for f in files:
            if f.lower().endswith(".xml"):
                text = open(os.path.join(base, f), encoding="utf-8", errors="replace").read()
                for rx in TPL_RES:
                    for m in rx.finditer(text):
                        out.setdefault(m.group(2), m.group(1))
    return out


def rewrite_xml(text, ren):
    def fix_tag(m):
        tag = m.group(0)
        if re.search(r'\bvirtual\s*=\s*"true"', tag, re.I):
            tag = re.sub(r'\bname\s*=\s*"([^"]+)"', lambda mm: 'name="%s"' % ren.get(mm.group(1), mm.group(1)), tag, count=1)

        def fix_inherits(mm):
            toks = [t.strip() for t in mm.group(1).split(",")]
            return 'inherits="%s"' % ", ".join(ren.get(t, t) for t in toks)
        return re.sub(r'\binherits\s*=\s*"([^"]*)"', fix_inherits, tag)
    return re.sub(r'<[A-Za-z][^<>]*>', fix_tag, text)


def rename_colliding_templates(pack_root, stock_root):
    """Rename pack templates whose names stock FrameXML already owns; returns the map."""
    stock = xml_templates(os.path.join(stock_root, "FrameXML"))
    pack = xml_templates(pack_root)
    ren = {n: "ASC_" + n for n, kind in pack.items() if n in stock and kind.lower() not in FONT_TAGS}
    if not ren:
        return ren
    lua_rx = re.compile(r'(["\'])(%s)\1' % "|".join(re.escape(n) for n in sorted(ren, key=len, reverse=True)))
    touched = {"xml": 0, "lua": 0}
    for base, _, files in os.walk(pack_root):
        for f in files:
            p = os.path.join(base, f)
            low = f.lower()
            if not (low.endswith(".xml") or low.endswith(".lua")):
                continue
            raw = open(p, "rb").read()
            bom = raw.startswith(b"\xef\xbb\xbf")
            text = raw[3:].decode("utf-8", errors="surrogateescape") if bom else raw.decode("utf-8", errors="surrogateescape")
            new = rewrite_xml(text, ren) if low.endswith(".xml") else lua_rx.sub(lambda m: m.group(1) + ren[m.group(2)] + m.group(1), text)
            if new != text:
                touched["xml" if low.endswith(".xml") else "lua"] += 1
                with open(p, "wb") as out:
                    out.write((b"\xef\xbb\xbf" if bom else b"") + new.encode("utf-8", errors="surrogateescape"))
    print("  renamed %d colliding template(s) (ASC_ prefix) in %d xml / %d lua file(s): %s"
          % (len(ren), touched["xml"], touched["lua"], ", ".join(sorted(ren))))
    return ren


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--core", required=True, help="built core preview pack (build_addon_pack.py output)")
    ap.add_argument("--ui-tree", required=True, help="...\\rexxar-reference\\ui-tree\\Interface")
    ap.add_argument("--out", required=True)
    ap.add_argument("--install", help="client root; the pack's Interface\\AddOns is copied there")
    ap.add_argument("--stock-ui-tree", help="stock 3.3.5a ...\\stock-ui-tree\\Interface (default: sibling of --ui-tree)")
    a = ap.parse_args()
    ui = a.ui_tree
    stock_ui = a.stock_ui_tree or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(ui))), "stock-ui-tree", "Interface")
    addons_out = os.path.join(a.out, "Interface", "AddOns")
    shim_out = os.path.join(addons_out, "!AscensionShim")
    if os.path.exists(a.out):
        shutil.rmtree(a.out)
    # 1. core shim
    copytree(os.path.join(a.core, "Interface", "AddOns", "!AscensionShim"), shim_out)
    # 2. compat files
    compat = os.path.join(shim_out, "compat")
    missing = []
    for d in DIRS:
        src = os.path.join(ui, d)
        if os.path.isdir(src):
            copytree(src, os.path.join(compat, d))
        else:
            missing.append(d)
    import re as _re
    ref_re = _re.compile(r'<\s*(?:Script|Include)\b[^>]*\bfile\s*=\s*"([^"]+)"', _re.I)

    def copy_with_refs(rel, seen):
        """Copy a manifest file and, for XML, every <Script>/<Include> it references (relative)."""
        key = rel.replace("\\", "/").lower()
        if key in seen:
            return
        seen.add(key)
        src = os.path.join(ui, rel)
        if not os.path.exists(src):
            missing.append(rel)
            return
        dst = os.path.join(compat, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        if rel.lower().endswith(".xml"):
            text = open(src, encoding="utf-8", errors="replace").read()
            base = os.path.dirname(rel)
            for m in ref_re.finditer(text):
                ref = m.group(1).replace("\\", "/")
                copy_with_refs(os.path.normpath(os.path.join(base, ref)).replace("\\", "/"), seen)

    seen = set()
    for rel in MANIFEST:
        copy_with_refs(rel, seen)
    # Recorded source edits to copied FrameXML files (client/PATCHES.md). Each is an exact text
    # replacement that must match once; a miss is an error so an upstream change cannot silently
    # drop the fix.
    for rel, old, new in TEXT_PATCHES:
        p = os.path.join(compat, rel)
        if not os.path.exists(p):
            continue
        text = open(p, encoding="utf-8", errors="replace").read()
        if text.count(old) != 1:
            raise SystemExit("TEXT_PATCHES: expected exactly one match in %s" % rel)
        open(p, "w", encoding="utf-8", newline="\n").write(text.replace(old, new))
    # Our overlay: everything under the source tree's compat/ (prelude, glue, and any file
    # that REPLACES an Ascension original at the same relative path, e.g. SharedXML/C_Hook.lua).
    overlay = os.path.dirname(GLUE)
    if os.path.isdir(overlay):
        copytree(overlay, compat)
    # attribute names for the GetAllAttributes shim: every <Attribute name="..."> in the pack's XML
    import re
    names = set()
    for root, _, files in os.walk(a.out if os.path.isdir(a.out) else compat):
        pass
    scan_roots = [compat] + [os.path.join(ui, "AddOns", n) for n in ADDONS]
    for sr in scan_roots:
        for root, _, files in os.walk(sr):
            for f in files:
                if f.lower().endswith(".xml"):
                    text = open(os.path.join(root, f), encoding="utf-8", errors="replace").read()
                    for m in re.finditer(r"<Attribute\b[^>]*\bname=\"([^\"]+)\"", text):
                        names.add(m.group(1))
    with open(os.path.join(compat, "zz_AttributeNames.lua"), "w", encoding="utf-8", newline="\n") as f:
        f.write("-- generated by assemble_panel_pack.py: every <Attribute name> in this pack's XML\nASC_ATTRIBUTE_NAMES = {\n")
        for n in sorted(names):
            f.write("    %s,\n" % ('"' + n.replace('"', '\\"') + '"'))
        f.write("}\n")
    # 3. TOC: core entries (minus Bootstrap) + compat + glue + Bootstrap last
    toc_path = os.path.join(shim_out, "!AscensionShim.toc")
    lines = open(toc_path, encoding="utf-8").read().splitlines()
    head = [l for l in lines if l.startswith("##")]
    core_files = [l for l in lines if l and not l.startswith("##") and l.strip().lower() != "bootstrap.lua"]
    toc = head + ["## SavedVariablesPerCharacter: AscensionShimDB", ""] + core_files
    toc += ["", "# --- Ascension SharedXML/FrameXML compat (verbatim, FrameXML.toc order) ---", "compat\\aa_StockPrelude.lua", "compat\\ab_CVar.lua"]
    toc += ["compat\\" + rel.replace("/", "\\") for rel in MANIFEST if rel not in missing]
    # ours, from the overlay: Ascension-only definitions sliced out of files we do not copy whole
    toc += ["compat\\FrameXML\\GameTooltipExtras.lua", "compat\\FrameXML\\StaticPopupExtras.lua",
            "compat\\FrameXML\\TemplateExtras.xml", "compat\\AscensionUIExtras.lua", "compat\\zz_TooltipMacros.lua"]
    toc += ["compat\\zz_AttributeNames.lua", "compat\\zz_StockShim.lua", "Bootstrap.lua"]
    with open(toc_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(toc) + "\n")
    # 4. original addons verbatim -- except the TOC dependency on AscensionUI (Ascension's
    #    always-loaded UI overhaul, not part of the pack; compat\AscensionUIExtras.lua carries the
    #    entry points these addons call), which would make LoadAddOn fail with DEP_MISSING.
    for name in ADDONS:
        src = os.path.join(ui, "AddOns", name)
        if os.path.isdir(src):
            copytree(src, os.path.join(addons_out, name))
            toc = os.path.join(addons_out, name, name + ".toc")
            if os.path.exists(toc):
                raw = open(toc, "rb").read()
                bom = raw.startswith(b"\xef\xbb\xbf")
                text = raw[3:].decode("utf-8", "replace") if bom else raw.decode("utf-8", "replace")
                lines = []
                for line in text.splitlines():
                    if line.lower().startswith("## dependencies:"):
                        deps = [d.strip() for d in line.split(":", 1)[1].split(",") if d.strip() and d.strip() != "AscensionUI"]
                        line = "## Dependencies: " + ", ".join(deps) if deps else "## X-Dependencies-Removed: AscensionUI"
                    lines.append(line)
                with open(toc, "wb") as f:
                    f.write((b"\xef\xbb\xbf" if bom else b"") + ("\n".join(lines) + "\n").encode("utf-8"))
        else:
            missing.append("AddOns/" + name)
    # 4b. stock-name collisions (see rename_colliding_templates)
    if os.path.isdir(os.path.join(stock_ui, "FrameXML")):
        rename_colliding_templates(addons_out, stock_ui)
    else:
        print("  !! no stock ui tree at %s: colliding templates NOT renamed" % stock_ui)
    print("pack at", a.out)
    print("  compat files: %d, addons: %s" % (len(MANIFEST) - len([m for m in missing if m in MANIFEST]), ", ".join(ADDONS)))
    for m in missing:
        print("  !! missing in ui-tree:", m)
    # 5. install
    if a.install:
        dst = os.path.join(a.install, "Interface", "AddOns")
        for name in os.listdir(addons_out):
            target = os.path.join(dst, name)
            if os.path.exists(target):
                shutil.rmtree(target)
            copytree(os.path.join(addons_out, name), target)
        print("installed into", dst)
    return 0


if __name__ == "__main__":
    sys.exit(main())
