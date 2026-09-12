# Client DBC snapshots: keeping a DBC dump as its difference from the client

People send in DBC dumps: the `DBFilesClient` tables pulled out of an Ascension client, usually zipped. Most of
those bytes are already preserved, because the public client package carries the same tables inside its patch
archives. Republishing them would add size and no information. A dump taken at a different time does hold something
the package does not: the tables as they were then, before an update changed them.

`tools/import_dbc_snapshot.py` keeps exactly that. It compares every member of a dump with the reference client's own
copy of the same table:
- a member with identical bytes is **named** in `manifest.json` with its SHA-256, and not republished;
- a member whose bytes differ is **republished** gzipped, and gets a record-level report in `diff.json`;
- a member that is not a whole WDBC file (a CSV export, say) is named with its hash and not republished.

Anyone holding the original archive can check every hash in the manifest, including those of the members that were
not republished.

The snapshots live in the data repository under `supplemental/client-dbc-snapshots/<archive sha256>/`.

## Building the reference

The reference is a folder of loose DBCs taken from the reference client, one per table, and a holders TSV with the
columns `name`, `archive` and `sha256`. Take each table from the archive that wins for it. Ascension's custom patch
archives carry no `(listfile)`, so read them by exact internal path with `mpqfind` from
[contrib/mpqtools](../../../contrib/mpqtools/README.md):

```
mpqfind <client>/Data --which "DBFilesClient\Spell.dbc"
mpqfind <client>/Data --from patch-T.MPQ "DBFilesClient\Spell.dbc" reference/Spell.dbc
```

`--which` lists every archive that holds the path. `--from` reads one named archive and bypasses precedence. The
importer hashes every reference file and refuses a holders row whose hash does not match.

For the first snapshot, 288 of the 337 reference tables came from `patch-M.MPQ`, 48 from `patch-S.MPQ` and one
(`Spell.dbc`) from `patch-T.MPQ`.

## Commands

Run these from `tools/cache-consolidator`:

```
python -B tools/import_dbc_snapshot.py import <dump.zip> --reference <folder> --holders <holders.tsv> \
    --reference-label "<what the reference client is, without local paths>" --received <YYYY-MM-DD> \
    --output <data repo>/supplemental/client-dbc-snapshots
python -B tools/import_dbc_snapshot.py verify <data repo>/supplemental/client-dbc-snapshots/<sha256>
python -B tools/import_dbc_snapshot.py verify <data repo>/supplemental/client-dbc-snapshots/<sha256> --reference <folder>
python -B tools/test_dbc_snapshot.py
```

Plain `verify` checks every artifact hash and every republished table's bytes and header. It also checks that the
verdicts and the summary agree. With `--reference`, it also re-checks every "identical" verdict against the reference
files and recomputes every diff. On the first snapshot that takes about two minutes, most of it `Spell.dbc`.

The importer and verifier also scan the string block of every republished table for anything shaped like an address,
a player GUID, an account path or a local path. The one exact exception is `techbot@gnome.mail`: game text on the GM
ban spell, at a TLD that does not exist.

## Reading `diff.json`

Each differing table gets two views.

**`by_first_field`** keys every row by its first field. It lists the ids only in the snapshot, the ids only in the
reference, and every changed row with the fields that changed. It is the right view for a table keyed by a real id,
such as `Spell.dbc` or `Quest.dbc`. It is wrong for a table keyed by row index: one row inserted in the reference
shifts the key of every row after it. `ItemAddon.dbc` does exactly that, reporting 188,016 "changed" rows by first
field.

**`by_content`** treats each table as a multiset of rows, ignoring the first field. It counts the rows only in either
file, and it is the view to trust for a row-indexed table. For `ItemAddon.dbc` it reports 2 rows only in the snapshot
and 7 only in the reference: 5 inserted rows and 2 changed ones.

Both views compare **string columns as text**, so a string that merely moved within the string block is not a
change. A column counts as a string column when every nonzero value in it starts a string in both files, and at
least one side has two or more distinct values. That rule has one blind spot: two different offsets that name
identical text compare equal. Empty strings count as strings, because Ascension's `Spell.dbc` points all eight locale
slots at one empty string. Without that, not one of its string columns qualifies, and 127,176 rows read as changed
instead of 325.

Field numbers are positions in the record, counted from 0. The report does not name them: Ascension extended several
of these tables, and no schema here is authoritative.

## What a snapshot is not

- **Not a newer client.** The first snapshot is older than the reference: the reference has more rows in 11 of its 13
  differing tables.
- **Not a server DBC set.** A realm's own `dbc` folder is a third thing. Here, `server-ascension` zeroes 10,928 custom
  effect and aura values in `Spell.dbc` so that stock AzerothCore can load it. Do not treat it as a copy of either
  client.
- **Not something to load into a live realm** without a reason. Older tables carry older numbers: in the first
  snapshot, `Quest.dbc` field 10 is a third of the reference's value for quest 1227 (3500 against 10500).
