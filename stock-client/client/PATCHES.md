# Original addon patch ledger

No original Ascension addon, SharedXML, FrameXML or GlueXML source was changed by the Codex continuation on 2026-09-09. The generated preview packs contain only the authored shim and recovered data.

When original addons are integrated, record each dependency-only TOC change here with source path, before/after hash and reason. Broader source edits must be justified in the design and reviewed rather than silently replacing the original UI.

## 2026-09-10 (P6): dependency-only TOC change, applied at assembly time

`tools/assemble_panel_pack.py` copies the original addons verbatim and then removes the token
`AscensionUI` from the `## Dependencies:` line of any copied `.toc` (today only
`Ascension_EnchantCollection.toc`: `Ascension_Collections, AscensionUI` -> `Ascension_Collections`).
AscensionUI is Ascension's always-loaded UI overhaul and is not part of the pack; without the edit
`LoadAddOn` fails with `DEP_MISSING`. The entry points those addons call from it are restated in
`!AscensionShim/compat/AscensionUIExtras.lua` (`BaseFrameFadeIn/Out`, `MagicButton_OnLoad`) and
`Shared/IconSelector.lua` is copied whole into the compat layer. The source tree under
`rexxar-reference/ui-tree` is not modified; the edit exists only in the assembled pack.

## 2026-09-10 (P6): one source edit in a copied FrameXML file, applied at assembly time

`FrameXML/Util/MysticEnchantManagerUtil.lua`, function `WritePresets`: the three-line
`if not issecure() then return C_Logger.Error("Tried to write %s.wtf from insecure code!", saveFile) end`
guard is removed (`TEXT_PATCHES` in `tools/assemble_panel_pack.py`). On Ascension the file is
FrameXML and runs secure; in the pack it is addon code, `issecure()` is always false, and every
preset save was refused with that message. The write itself goes through the shim's
`WriteCustomWTF`, which keeps the text in `AscensionShimDB.customWTF` (saved variables) because
stock Lua cannot touch `WTF\*.wtf` files. The assembler aborts if the guard text is not found exactly
once, so an upstream change to that function surfaces instead of silently shipping unpatched.
