# Ascension public storage and downloads

GitHub is the front door: source code, small versioned manifests, and links. Public
bulk datasets belong in GitHub Releases. AscensionDB catalogs and preserved images
belong in the separate `ascension-public-data` R2 bucket. Original donations and
unreviewed evidence stay in local intake storage and backups.

## People downloading data

Download a collection's JSON manifest from `ascension-data/datasets/`, then run:

```powershell
python dataset.py download cache.json --out AscensionData
# Optional: retrieve only one folder or file from that collection.
python dataset.py download cache.json --out AscensionData --select cachedata/union
```

Download `dataset.py` from this folder, or run it from a preservation repository
checkout. Python 3.12 or later is sufficient; downloading needs no paid services,
API keys, AI model or Python packages. Interrupted package downloads resume, and
completed packages and restored files are checked with SHA-256. Originals retain
their normal paths in the output. Keep the `.ascension-downloads` cache to avoid
repeating downloads. Manifests describe public exports, not private donor files.

## Preparing and publishing a collection

Only use an **audited public export directory**, never an intake root, raw donor
folder, client installation, or whole Git checkout. This packer is a storage tool;
it does not grant publication permission or replace the existing privacy audit.

```powershell
python dataset.py prepare --input PublicCache --prefix cachedata --dataset cache --out PackageStaging
python dataset.py publish PackageStaging
```

Packaging reads in 64 MiB portions, deduplicates equal portions and writes stable
hash-bucketed TAR packs capped at 512 MiB. Large files span packs. TAR members are
content hashes; use the downloader to restore normal filenames. GitHub Releases
allow fewer than 2 GiB per asset and at most 1,000 assets per release; publication
checks the limit, uploads a draft, verifies GitHub's asset sizes and SHA-256 digests,
then promotes it. It never replaces published assets. `--previous dataset.json`
on `prepare` reuses URLs of unchanged packs from earlier releases.

Commit only `dataset.json` as `datasets/<collection>.json`, after release upload
succeeds. The research publisher does this automatically in its dedicated data
worktree. The cache publisher supports `.ascension-storage.json` with
`{"schema":1,"cache":"github-releases"}`; activate it only after the first verified
release exists, its manifest is committed, `cachedata/` is ignored and its old
files have been removed from the Git index. Its existing audit still runs before
any upload. Installing code alone does not activate that migration. No history
rewrite is part of this tool.

## R2 interface shared with the image work

Bucket: `ascension-public-data` (separate from private contribution storage).
Read service: `https://ascension-public-data.ascension-archive.workers.dev/`.

- Claude's images: `images/<original relative image path>`; URLs are the read
  service plus that exact key. Upload content types correctly. Icons can use
  `images/icons/<lowercase-name>.png` if that matches the verified staging index.
- Catalogs: `catalog/snapshots/<SHA-256>/<generated path>`.
- Current catalog: `catalog/current.json`, containing `schema`, `snapshot`,
  `prefix`, and the previous snapshot hash.
- Optional versioned image collections: `media/snapshots/<SHA-256>/...` with
  `media/current.json`. Existing `images/` URLs do not depend on that pointer.

The service permits public GET/HEAD of these paths; it does not list the bucket
or expose the donor bucket. `.gz` objects are application/gzip bytes without
Content-Encoding because AscensionDB decompresses them itself. Static `images/`
paths cache for a year, so use versioned paths for replacements.

The frontend can keep its existing URL. Set its `config.json` to:

```json
{"catalogService":"https://ascension-public-data.ascension-archive.workers.dev/"}
```

It reads current.json once, pins the session to that immutable prefix, and fetches
all search/record/map parts there. Old prefixes remain readable. Upload failure
keeps the old current pointer. Promotion uses a conditional write so concurrent
publishers cannot silently replace one another's current pointer.

```powershell
python tools/ascension-db/build.py --data PublicDataCheckout --out CatalogStaging --hosting r2
python tools/data-storage/r2_publish.py --input CatalogStaging --report StorageReport.json
# Set ASCENSION_PUBLICATION_URL and ASCENSION_PUBLICATION_TOKEN privately, then:
python tools/data-storage/r2_publish.py --input CatalogStaging --report StorageReport.json --upload
```

No token is committed. The Worker has its own PUBLISH_TOKEN secret. The uploader
supports this narrow HTTPS gateway (64 MiB/object) or the S3 API using boto3 and
private AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY plus ASCENSION_R2_ENDPOINT. The
existing build caps generated catalog objects below the gateway limit. Inventory
and full catalog validation happen before uploading; upload workers are bounded.

`.github/workflows/ascension-db-r2.yml` is the reusable R2 workflow. Its caller must
pass a reviewed source ref and the ASCENSION_PUBLICATION_TOKEN repository secret.
Only small reports are uploaded as Actions artifacts with three-day retention.
The existing Pages workflow remains available until the caller is switched.

## Migration and rollback

First deploy the gateway, publish and validate a complete R2 catalog, and verify
public reads. Then switch the frontend configuration and data workflow. Only then
remove the bulk cache files from the repository's current tree after checking the
Release can reconstruct every byte. Install the release-capable cache publisher
before enabling its storage marker. Leave unrelated pending changes alone.

A rollback can point the frontend to the previous Pages dataBase or conditionally
restore the earlier complete R2 pointer. Never delete old snapshots as part of a
publish. Storage retention/garbage collection needs an explicit policy and must
keep every object referenced by retained manifests. Removing current-tree files
does not shrink existing Git history; a later coordinated history cleanup is a
separate operation. Local raw storage also needs an independent backup.

## Verification

`python -B -m unittest discover -s tools/data-storage -v`

`node --test tools/data-storage/test_worker.mjs`

The research workflow tests reconstruct a release-backed dataset and run the full
AscensionDB builder and validator. Public claims remain source-attributed evidence;
these tools do not infer spawns or write to a playable realm database.
