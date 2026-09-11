# Independent pipeline review: recovery rollout

The September 2026 review identified independent failure modes, not proof that
all retained data was lost. Corrections use synthetic failures and isolated replay.

* Merge metadata is prepared as a verified generation and replayed after process
  interruption. Packs are append-only and flushed first. On Windows this is a
  process-interruption guarantee, not a claim about sudden hardware power loss.
* Sources retain WDB locale, build, and original header bytes. Locale views live
  under by-locale; rebuilt English caches keep their original paths and other
  locales use wdb/by-locale. Legacy combined views prefer English where available.
  Mode-specific dates and locale provenance come only from matching sources.
* Lua byte escapes decode as UTF-8 bytes. Invalid encodings and unknown nonempty
  schemas remain explicit unresolved sources. A failed file's trial state is
  discarded while other files can finish. Conflicting observations remain in
  private state; no raw account source is published.
* Broad identity substitution is removed. Ambiguous identity-bearing text and
  keys are retained for review, not silently rewritten. Privacy filters are
  structure-specific and shared with the older contribution helper.
* Source parser revisions enable deliberate WDB replay. Lua requires a fresh
  shadow state after semantic rule changes, to avoid mixing corrected text with
  old corrupted values. Never clear the live completion ledger to force replay.
* The column gate detects entirely missing baseline columns/branches. Decode
  exceptions and inexact consumption block publication. Creature quest-item
  links, five vendor cost slots, gossip quest data and CoASniff argument structure
  are preserved. Legacy contribution ZIPs now contain complete valid WDBs.

## Controlled replay

1. Pause processing at an idle boundary. Copy input archives, extracted files,
   merge state and generated output into a separate private recovery directory.
2. Keep the prior merge state and outputs as immutable comparison baselines.
   Reprocess retained originals with a frozen candidate tool snapshot and a fresh
   Lua state. Run luamerge with `--replay-baseline-state` pointing to the copied
   prior Lua state so known source dates and provenance remain unchanged. Set the publish checkout to an unusable recovery-only path.
3. Compare every existing WDB payload hash and source attribution, then compare
   corrected Lua sources, unresolved files, output coverage and audit findings.
4. Obtain an adversarial review of differences. Only a fully verified candidate
   can replace live state and proceed through normal publication. Preserve rollback
   copies and receipts. A failed candidate never replaces the public dataset.

Information removed in a contributor's browser before upload cannot be recovered
from the uploaded copy. Reprocessing does not invent missing bytes or unknown
capture dates. Raw originals and unresolved material stay out of source control.

## Findings caught during the controlled pass

Malformed locale header bytes are classified as `unknown`. TSV writers reject
embedded separators, and readers reject inconsistent row widths. Existing capture
dates and unchanged mode evidence survive parser-revision reprocessing; a copied
file's filesystem date cannot silently become a newer observation.

Auctionator v4's observed market-record schema is explicitly recognized. The
legacy price view uses `mr`; complete observations, daily history and source
attribution are retained in `lua/Auctionator.observations.json`. Unknown fields
remain review-held. A mixed capture commits only wholly validated branches with
canonical modes; incompatible branches remain private and keep the input incomplete.
The [addon's own format documentation](https://github.com/Sleepywalker69/Auctionator-CoA-Ascension/blob/main/Auctionator/README.md)
explains `mr`, item identifiers and daily high/low fields. `lastScan` is preserved
without assuming a timestamp unit.

For WildcardHarvest, an ambiguous gossip observation is held individually rather
than discarding clean vendor and reference data in the same file. Private state
records the source hash, record digest and fixed reason; the original stays
private. `processed_with_retained` is not full completion, and filing refuses
such sources. No player-name substitutions are invented.

Sol adversarial reviews cover recovery, privacy, each correction found during
replay, and the final baseline comparison. Passing a source test does not approve
a replay candidate for live promotion. Static column expectations are supplemented
by a direct comparison with the snapshotted currently published Lua output.
