# Ascension stock-client port

Experimental implementation of the full Ascension-to-stock-3.3.5a port. The target includes the original Ascension UI, data, gameplay systems and custom classes, with a bounded executable patch. This directory currently provides data generators, a read-only preview core, pack checks and recovery audits. It does not yet provide a playable replacement client.

## Current phase status

| Phase | Current evidence | Remaining acceptance work |
|---|---|---|
| P0 measurements | Recovered 10 of 36 mapping outputs; compile-only census covers 1,106 Ascension and 322 stock UI files; corrected spell-fidelity join. | Full archive-winner validation, native binding labels, remaining UI/API contracts. |
| P1 stock-client boot | **DONE 2026-09-09.** Rebuilt `client-coa` (patched exe `edc571…`, 75 Ascension archives + `patch-Y.MPQ`) logged in through a stock authserver, created a stock character and entered the world on the fresh `coa2` realm; a custom item (`Akirus the Worm-Breaker`, display 60568) renders in the dressing room and a custom spell (`Mana-forged Barrier`) tooltips. Screenshots in the hub under `realms\coa2\shots\`. Two findings: loose `DBFilesClient\` copies are ignored by the stock loader, and Ascension's 32-row ChrClasses / 42-row ChrRaces / 209-row CharBaseInfo crash stock character creation (EIP = a class id), so `patch-Y.MPQ` (written by `mpqwrite.py`) carries the five stock creation DBCs at the highest general slot. | Tooltip macro lines (`@s:…@`, `@re:…@`) render raw until the shim post-processes them. |
| P2 original talent/Collections UI | **RENDERS 2026-09-10.** `tools/assemble_panel_pack.py` layers the preview core, a verbatim Ascension SharedXML/FrameXML compat set (69 files in FrameXML.toc order, colliding template names renamed `ASC_*`) and the original Collections/TalentUI/CoATalents addons; on the stock client the Character Advancement tab opens with zero Lua errors, shows the Tinker spec cards and, after activating a spec, the class + specialization trees with nodes, connectors, rank counters, gates and passives (hub screenshots `realms\coa2\shots\39`, `44`, `46`). Read-only preview data. Two findings: the stock client keeps the first definition of a template name, and `ChrRaces` must not be overridden (it sizes client tables that Ascension's race-keyed DBCs fill). | Node tooltips/click paths, tooltip macro post-processing, Ascension's exact complexity/primary-stat strings (placeholders now), the other Collections tabs (P7). |
| P3 gameplay | Dataset-scoped SQL staging and measured spell losses are ready for review. | Transaction-safe transport, server state/validation, learn/unlearn effects and persistence. Existing module is HELLO/PING only. |
| P4 custom class creation | 32 class identities, including all 21 custom bytes, joined to CA class IDs; 101 specialization records decoded. | Character creation UI, complete archetype decode, server class identity and verified executable changes. |
| P5 builds | Full 357-build Dawnrise catalogue and 32,570 captured spell-plan rows preserved; read-only browse/search/sort API. | Original Hero Architect integration, 56-archetype reconciliation, activation and persistence. |
| P6 Free-Pick / enchants | Separate mode datasets and captured build enchant fields retained. | Actual rules, complete enchant costs/slot behavior and gameplay. |
| P7 remaining systems | Dependency census and recovered maps provide a starting inventory. | Port remaining UI/content with explicit missing-data behavior. |
| P8 distribution | Portable tested startup patcher, deterministic data/pack builders, and successful relocated-source smoke checks. | Reviewed source reconciliation, full installer/module packaging, clean-clone and gameplay acceptance. |

The original UI files and ten recovered agent maps remain unchanged. Generated data and builds are local artifacts, excluded from source by `.gitignore`. Nothing here imports SQL automatically, launches a client, restarts a realm or publishes a repository.

## Portable executable patcher

`client/patch_wow.py` is a byte-identical source copy of the startup-tested hub patcher. It takes explicit input/output paths, requires the exact recorded stock build hash, refuses source aliases and atomically replaces a separate output. No executable is distributed here. This implements the measured startup/capacity corrections, **not custom-class support**. The 128-slot reserve was tested with the isolated 75-MPQ composition; it is not an unlimited archive loader.

```powershell
python -B client/patch_wow.py 'C:\AzerothRealm\client\Wow.exe' --check-only
python -B tools/test_executable_patch.py --stock 'C:\AzerothRealm\client\Wow.exe'
```

To write a patched copy, supply a second, distinct output path whose parent already exists. Keep the stock original and source MPQs intact. The recorded default output SHA256 is `edc571952f1bc833a9dd262a9266803324420e57c06a4392b98ba56201860e89`. Archive assembly remains hub-local and needs separate portability work.

## Reproduce the data and preview

Use Python 3 with the Lua 5.1 runtime supplied by `lupa`. Development checks were run with Python 3.14 and `lupa==2.8`; generators use the Python standard library. Run these commands from this `stock-client` directory. Paths below identify this workstation's read-only reference sources; use equivalent local paths after moving the source tree.

```powershell
$ref = 'C:\AzerothRealm\realms\ascension\rexxar-reference'
python -B tools/gen_ca_data.py --harvest 'C:\AscensionArchive\ascension-cache-consolidator\cachedata\lua\harvest\advancement.tsv' --dbc-export "$ref\ca-dbc-export" --essence-dbc "$ref\ca-dbc\DBFilesClient_CharacterAdvancementEssence.dbc" --specs-dbc "$ref\ca-dbc\DBFilesClient_ChrSpecs.dbc" --chrclasses-dbc 'C:\AzerothRealm\server-ascension\Data\dbc\ChrClasses.dbc' --out data
python -B tools/gen_build_data.py --harvest-zip 'C:\AscensionArchive\discord resources\ascension-harvest-export.zip' --out data/build-catalogue
python -B tools/build_addon_pack.py --dataset unattributed-7f0c805382 --class-byte 28 --level 80 --out build/p2-core-preview
python -B tools/build_addon_pack.py --dataset unattributed-7f0c805382 --class-byte 28 --level 80 --catalogue data/build-catalogue --out build/p2-catalogue-preview
```

Both generators accept `--verify` to compare deterministic output without writing. The pack builder confines output to a named directory under this source tree's `build/`; it does not install into any client. `--catalogue` is explicit because the community catalogue is a Dawnrise capture, separate from the selected CA dataset. The example selects Tinker byte 28 at level 80 for a read-only preview; it does not establish the underlying character's class or the correct CoA cost rules.

The core pack contains 67 Lua files; the catalogue pack contains 89. Commands provided by their bootstrap are `/asc status`, `/asc selftest`, `/asc spec <ID>`, and `/asc builds [category]`. Original Collections/TalentUI panels and `/ca` are not yet installed by these packs.

## Verify

```powershell
python -B tools/test_data_core.py
python -B tools/test_build_data.py
python -B tools/test_ui_dependencies.py
python -B tools/test_spell_fidelity.py
python -B tools/test_pack_gate.py
python -B tools/luacheck_pack.py --pack 'build/p2-core-preview/Interface/AddOns/!AscensionShim' --report reports/p2-core-preview-pack-check.json
python -B tools/luacheck_pack.py --pack 'build/p2-catalogue-preview/Interface/AddOns/!AscensionShim' --report reports/p2-catalogue-preview-pack-check.json
python -B tools/analyze_ui_dependencies.py --ascension "$ref\ui-tree\Interface" --stock "$ref\stock-ui-tree\Interface" --target AddOns/Ascension_Collections --target AddOns/Ascension_CoATalents --target AddOns/Ascension_TalentUI --out reports/ui-dependencies
python -B tools/audit_spell_fidelity.py --data data --journal 'C:\AzerothRealm\realms\ascension\sanitized-for-core.json' --spell-dbc 'C:\AzerothRealm\server-coa2\Data\dbc\Spell.dbc' --build-catalogue data/build-catalogue --out reports/spell-fidelity
```

The data test compares every one of 30,765 generated records against Lua 5.1 and SQL JSON, plus all shared reference/essence tables. The build test compares all 357 complete records and 32,570 spell rows, and checks sorting, copy isolation, mutation refusal and deferred query events. Pack bootstrap tests use a mocked stock host; they do not prove rendering or native widget compatibility. The authored source was also copied to a separate folder and both packs were rebuilt and checked there using explicit read-only generated inputs; CA/catalogue tests passed from that location. This is source relocation evidence, not full clean-clone client/gameplay acceptance.

The dependency scanner compiles source without executing it. It reads global opcodes rather than counting identifier-like strings. Its recursive provider candidates are review material, not a ready-to-load manifest: the three target addons reach 448 files, including unrelated systems. Potential providers do not establish load order. Native globals, XML handler arguments and dynamic aliases require separate contracts. See the report's limitations before interpreting its unresolved counts.

## Data facts and gaps

- Three CA datasets remain separate: Darkmoon (`Season10Wildcard`), Dawnrise (`Season10Freepick`), and an unattributed capture with unknown mode. 9,134 entries differ between variants.
- Each has 10,255 entries: 10,250 harvested and five DBC-only placeholders. There are 161 class/tab buckets, or 159 excluding the placeholder class; the older 153-bucket estimate is not forced onto the new data.
- Each has six unresolved relationships and 22 entries that fail minimum captured-rule completeness. `CanEvaluateLearn` checks only that minimum; it never authorizes learning.
- Essence includes 5,600 rows. The preview uses unconditional rows and preserves the separate family-28 Tinker and family-10 Free-Pick curves. `(0,0)` is withheld, not a zero budget.
- Class byte and CA class ID are different: Tinker is byte 28 / CA type 30. Join internal class tokens before display labels because Reborn entries reuse display labels.
- Specialization 49 is internally `TINKER/FIREARMS`, displayed as Demolition. The native getter's field layout was checked against the preserved original Extensions.dll, not the current AuthGate wrapper.
- The community catalogue comes from the full 2026-08-29 Dawnrise JSON inside the harvest archive, not the truncated TSV. Empty captured Lua tables may appear as `{}` in JSON; their shape is preserved. Captured spell IDs are not silently converted to CA entry IDs.
- The local server's `builds.json` has 56 plans but includes archive-authored content. It must not be relabelled as 56 verified original archetypes; reconcile it with the creation DBCs separately.
- The current isolated server DBC confirms recorded losses affecting 430 CA entries / 495 referenced spells and 272 community builds. A lost field is not proof that the whole spell is inert. Exact named entries and current fields are in the fidelity report.

SQL files are staging schemas, unapplied. CA entries, edges, spells, essence and reference tables are keyed by dataset; builds have a separate catalogue key. Schema drafts changed during this implementation and are not migrations for an existing populated database.

## Continuation

Review `DESIGN.md` for architecture and the private hub's `realms/ascension/HANDOFF-STOCK-CLIENT-CODEX.md` for executable recovery, client ownership and exact checkpoint evidence. The full port remains the objective. Next work is original-panel dependency integration and visual P1/P2 acceptance, while P3 must account for the measured core-effect gaps.
