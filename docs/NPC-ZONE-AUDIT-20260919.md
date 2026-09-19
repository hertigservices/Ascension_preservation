# NPC zone audit — 2026-09-19

## Result

The live catalog already exposed 64 NPC results in `area:10138` (Northshire
Valley), so the defect was incomplete coverage rather than a completely empty zone.
Comparing the rendered Exiles NPC pages with all 153,064 rows in the companion
database export found:

- 905 NPC pages missing one or more explicit spawn-area memberships;
- 2,350 missing NPC-to-area memberships across 175 areas;
- 17 spawn-backed Northshire NPCs omitted from the live Northshire result;
- Bianca Spada (161700), Moroi Spada (161701), and Sister Alma (161702) absent
  from the export's spawn table as well as from their rendered page locations.

The three reported custom NPCs therefore use separately attributed reviewed claims.
The remaining additions come only from positive `creature_spawn.area_id` values in
the preserved 2026-09-13 export. Blank areas remain unknown; no map ID, coordinate,
NPC name, subtitle, quest relationship, or neighboring ID is treated as a location.

## Northshire additions

The export independently supplies Northshire claims for these 17 NPC entries:

| ID | NPC |
| ---: | --- |
| 94 | Defias Cutpurse |
| 97 | Riverpaw Runt |
| 118 | Prowler |
| 478 | Riverpaw Outrunner |
| 721 | Rabbit |
| 883 | Deer |
| 1423 | Stormwind Guard |
| 2442 | Cow |
| 6491 | Spirit Healer |
| 13321 | Frog |
| 26007 | Arena Battlemaster |
| 26309 | Weapons Vendor |
| 26738 | [DND] TAR Pedestal - Accessories |
| 26740 | [DND] TAR Pedestal - Gems |
| 26741 | [DND] TAR Pedestal - General Goods |
| 26745 | [DND] TAR Pedestal - Weapons |
| 32820 | Wild Turkey |

Together with the three reviewed reports, the corrected zone facet gains 20 NPC
results and should expose 84 Northshire NPCs before later data changes.

## Scope and safety

The compact claim table is versioned in `ascension-data` with its provenance. During
catalog construction it joins only to the companion Exiles NPC pages, whose IDs share
the export's namespace. Client WDB captures, modes, and unrelated sources do not
inherit those zones, and all original records remain unchanged.

The rendered pages cap or omit some location rows, which explains why the complete
spawn export adds areas even to NPCs that already had at least one visible zone. The
largest audited additions occur in Dun Morogh, Exodar, Silvermoon City, Thunder Bluff,
Valley of Trials, Mulgore, Red Cloud Mesa, Crystalsong Forest, Caverns of Time, and
Orgrimmar. This is a zone-facet repair, not a claim that the export is a complete live
spawn census.
