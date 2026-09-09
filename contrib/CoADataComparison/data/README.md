# Identifier comparison data

This directory is the **data** half of the contribution: numeric CoA coverage
that the engine export does not already contain as a community crosswalk.

It is **not** a client dump. It contains no spell descriptions, HTML tooltips,
icons, names, MPQs, DBCs, WDB caches, or Hub page text.

## File

`comparison-ids.json` was produced by `compare_ca.py` against:

- this repository's `data/ca-export` at asserted revision
  `664f2768f0695187349fc38842eae6a1588cc352`
- a private 2026-07-31 CoA Build Hub archive (skills sourced, per that
  archive's README, from `https://db.ascension.gg`)

Re-run the tool on your own Hub archive to verify. Source SHA-256 values
inside the JSON bind the bytes that were compared; they do not ship those
bytes.

## What you can use immediately

| Field | Count | Meaning |
|---|---:|---|
| `master_absent_ids` | 1,571 | Hub master spell IDs that do not appear in any of the five CA rank fields. Classify before treating as missing client content. |
| `description_candidates` | 3,600 | `{ca_id, spell_id}` rows whose CA descriptions are empty and whose Hub master record has text. Availability only; the text is not included. |
| `non_matching_edge_hypotheses` | 193 | Community dependencies that did not map to an existing runtime edge: 142 candidate pairs, 43 unmapped/ambiguous, 8 dangling community node IDs. |
| `runtime_dangling_target_ids` | 3 | `6451`, `7181`, `17567` — already documented in `data/ca-export/SCHEMA.md`. This comparison did not recover them. |

Matching runtime edges (4,809) are omitted; they are already in
`data/ca-export/edges.json`.

These identifiers are investigation inputs. They are not an import, a
replacement graph, or permission to copy third-party wording.
