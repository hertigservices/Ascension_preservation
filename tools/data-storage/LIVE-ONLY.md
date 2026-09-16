# Live-only public storage

The owner selected this policy on September 16, 2026: the personal WD SSD stores
backups; R2 stores the current public project and temporary incoming publication.
Historical catalog retention in R2 is disabled. Private contribution intake remains
separate and retains its acknowledgment-based cleanup.

`live_runner.py` is the sole scheduled publisher. The legacy GitHub website workflow
stays disabled, and the public Worker rejects its former publication endpoints.
Source and data remain on GitHub; the local collector continues normal intake,
Release publication and acknowledgments. The website remains Cloudflare-hosted.

## Data layout

Browser URLs retain `catalog/snapshots/<snapshot>/<filename>`. Each live snapshot has
an immutable storage manifest, a small `layout.json`, and SHA-256-sharded path maps.
The maps point to immutable content objects, reusing equal existing live content
even when a filename changes. New bytes use `catalog/objects/<content-sha256>`.
Existing legacy snapshot objects may remain as referenced content; their directory
name does not mean a historical catalog is retained. Only the exact files required
by the current layout remain. Images retain their existing URLs.

The gateway reads bounded index shards and verifies size and available SHA metadata.
Gzip files remain raw gzip bytes for the browser's decompressor. Expired snapshots
return 410 with a refresh instruction. Open tabs may need refresh after publication;
there is no indefinite cloud history. The current pointer has `previous: null`;
rollback means verified restore from WD, not a retained second R2 catalog.

## Local controller

Configuration is a private JSON file with these fields:

```json
{
  "backup_root": "/mounted/WD/AscensionBackups/R2",
  "volume_uuid": "the-verified-WD-filesystem-UUID",
  "credential_file": "/home/user/.config/ascension/r2-cleanup.json",
  "monthly_write_attempt_limit": 400000,
  "billing_cycle_day": 10,
  "maximum_public_bucket_bytes": 6000000000,
  "maximum_new_objects_per_publication": 25000,
  "minimum_publication_interval_seconds": 86400,
  "minimum_free_bytes": 20000000000
}
```

The actual filesystem UUID is verified before writing and before deleting remote
objects. An unavailable/wrong disk stops work; it never silently stores backups on
the system disk. The service uses an exclusive host lock and refuses retention while
the old GitHub workflow is enabled or still running. A user systemd timer checks
hourly; lingering permits execution without desktop login. Data becomes public at
most once per 24 hours through the routine path. `--publish-now` waives that interval
only; archive, write and capacity limits still apply.

Catalog inputs are fingerprinted by logical file SHA/size, ignoring contribution
receipts and packaging location changes. Builder source is the immutable `source_ref`
in the disabled data workflow. Feature releases should still update that source pin,
then run the local controller; do not re-enable the old CI publisher. The controller
fetches only the named public repositories, in its isolated WD working copies.

Before publication, every output file is copied to the WD content store, fsynced and
rehashed. Its restorable manifest and publication plan are written before remote
uploads. New objects are uploaded with Content-MD5 and immutable conditions. SDK
network retries are bounded; a completed local build is retained for exact-output
resume. Pointer promotion is conditional on the previously read ETag. Failed uploads
leave the current pointer intact, and a later maintenance pass archives and removes
unreferenced ordinary objects.

Before deletion, every candidate must have an exact key/size/ETag archive receipt
and its local blob must rehash correctly. All candidates pass backup validation
before the first batch. Live references and images are protected, unknown namespaces
are left alone, and pointer checks occur before each batch. A final listing verifies
the deletions and unchanged retained objects. `remote-index.sqlite` and per-run
JSONL receipts allow recovery and audit. Do not run another S3 writer concurrently.

The publication-write budget reserves four possible SDK attempts before dispatch,
settles reported attempts after complete success, and does not refund failed or
interrupted runs. This is a conservative **controller write-attempt cap**, not a
Cloudflare account billing cap: past usage, contribution intake, reads, list requests
and other account services are separate. The byte cap includes all public objects
and incoming writes, but not the private contribution bucket. Hitting either cap
pauses publication with `publisher-status.json`; the current website stays live.

## Backup and restore

The WD root contains `blobs/<hash-prefix>/<sha256>`, historical census indexes,
`catalogs/<snapshot>/storage-manifest.json`, source/data working copies, budgets and
per-run receipts. The initial migration additionally retains all historical and
partial remote objects, not just complete catalogs. Original HWI-013 archives remain
preserved in the WD legacy archive.

Restore without a network connection using `restore_catalog.py --archive BACKUP_ROOT --snapshot HASH --out NEW_DIRECTORY`. The command verifies every copied byte and saves a restore receipt.

To reconstruct a saved complete catalog manually, read its storage manifest, copy each named
file from the matching SHA-256 blob, and check every size/hash. Run the catalog
validator. Publish through the same controller functions with current-pointer CAS
and budgets; do not copy an old current pointer whose objects are absent. Keep source
revision and data revision in the restore receipt. Restoring a historical catalog is
an explicit operational action and should not be performed by routine retention.

The WD disk is the selected backup medium, not an independent off-site copy. The
controller does not automatically delete WD history. Local capacity is checked
before builds, and future WD retention can be chosen separately.

Tests: `python -B -m unittest discover -s tools/data-storage -v` and
`node --test tools/data-storage/test_worker.mjs`.
