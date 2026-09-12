# CoA-Databank: Byte's reference data, republished with the identities taken out

[f3rr311/CoA-Databank](https://github.com/f3rr311/CoA-Databank) is the reference data Byte gathered for a Conquest of
Azeroth rebuild:
- a scrape of coabuildhub.com, taken 2026-07-31: talent trees, skills and community builds;
- indexes derived from the client's MPQs, captured 2026-07-29: spells, spell dependencies, assets and reduced DBC rows;
- harvest provenance: spell and icon captures, class rosters, QA data, and eight ascension.gg pages.

Byte gave permission to republish, relayed by James Hertig on 2026-09-12. The repository carries no licence file.

`tools/import_coa_databank.py` puts one commit of it into the data repository under
`supplemental/coa-databank/<commit>/`. It reads every file from git's object store at that commit, never from a
working tree. Each file is then:
- **published unchanged**, byte-identical to the upstream blob; or
- **published transformed** by one of two named transforms, with the upstream blob id and the counts recorded; or
- **omitted**, named in the manifest with the reason.

Identical blobs at two paths share one artifact: `databank/` repeats `provenance/`'s palette and inventory.

## The two transforms

### community-identities

A coabuildhub build names people: its author (username, Discord avatar URL and site id), every commenter, and the
author of each "similar build" it links. The transform:
- deletes `build.author`, `build.author_id`, `comments`, and `author`/`author_id` from every similar build;
- deletes the `- **Author:**` line and the `## Comments` section from the build's Markdown twin;
- replaces those people's usernames with `[user]` inside the build files, in string values only and never in JSON
  keys.

It re-serialises each JSON file with the settings that reproduce the upstream file exactly (indent, escaping, line
endings), and refuses a file it cannot reproduce. It also refuses a build carrying any other `author*` field.

**A username can also be an ordinary word.** Two of the 248 names occur in coabuildhub's own skill, talent, spell
and page text. "Will" is in 1,999 places there, as in "will increase", and "Crimson" in 157. Replacing those
everywhere would wreck the guides. So the corpus decides: a name found in the game text is replaced **only in build
titles, and only as written**. Any other name is replaced in every string of the build files, case-insensitively
from three characters up.

The rule touches nothing outside `coabuildhub/builds/`. It was measured there: 7 of the 248 names also occur as
written in published non-build files. Every occurrence is game text: a talent such as "… of the Serpent", a spell or
creature name, or lore such as "the Plains of …". None names a person.

### local-paths

Byte's harvest recorded where each file lay on disk, and the palette's asset index stores a source path per asset:
`C:\CoA\dl\Patch-N-Loose\…` alone occurs 356,948 times. The transform:
- replaces a drive with `<local>` (`C:\CoA` becomes `<local>\CoA`);
- replaces the name after a drive's `Users` folder with `<user>`.

Where `<local>` sits keeps the folder layout readable. A drive only counts when a separator and a path character
follow it: every ascension.gg page carries an FAQ label `A:` followed by an escaped quote, which a looser rule
rewrites. That happened in the first build, and a test now covers it.

## What is omitted

- **Code:** the author's `*.py` scrapers and harvest scripts, which several of them embed local paths in. Read them
  upstream.
- **Raw build pages** (`coabuildhub/raw/builds/*.html` and the two `sample-build` captures). Their markup embeds the
  same usernames, Discord user ids and comments, and cannot be stripped reliably. The stripped build files carry the
  same builds.
- **`.gitignore`.**

## The screen

After the transforms, every published text is refused if it holds any of these:
- an address, except exactly `support@ascension.gg` (the public support mailbox in the schema.org block of
  ascension.gg pages) and `techbot@gnome.mail` (game text on a GM ban spell);
- anything shaped like a player GUID;
- an account path or a drive path;
- a Discord avatar or user URL;
- a `"username":` field.

Binary files (the 3,018 JPEG icons and the WebP sprite sheet) are published as they are, and not screened.

## Commands

Run these from `tools/cache-consolidator`:

```
python -B tools/import_coa_databank.py import <checkout> --commit <sha> --output <data repo>/supplemental/coa-databank
python -B tools/import_coa_databank.py verify <data repo>/supplemental/coa-databank/<sha>
python -B tools/import_coa_databank.py verify <data repo>/supplemental/coa-databank/<sha> --checkout <checkout>
python -B tools/test_coa_databank.py
```

Plain `verify` checks every artifact's hash and every untransformed file's git blob id. It also re-runs the screen,
and checks that each omission matches its rule. With `--checkout` it re-derives every published file from the
commit, and requires the same bytes and the same transform counts. That proves each change is exactly the recorded
transform applied to the upstream blob. On the first snapshot it takes about five and a half minutes.

A Windows note for anyone extending the tests: pass `-c core.autocrlf=false` to `git add` as well as to
`git commit`. A machine-wide `autocrlf=true` turns a CRLF fixture into an LF blob when it is added.

## How it relates to the captured data

The talent trees mostly overlap with the Exiles mirror: 3,598 of coabuildhub's 3,611 talent spell ids are already in
`supplemental/exiles-db`'s talent trees. The other 13 are new, e.g. 300607 "Titanfall" in the Templar Crusader tree.
Exiles already records each cell's position, icon and maximum rank. What coabuildhub adds per node is its essence
costs (`teCost`, `aeCost`) and its description text. The community builds are unique to this set.
The palette indexes are derived from client archives that the public client package already carries. What they add
is the dependency graph between spells and the assets each spell uses.
