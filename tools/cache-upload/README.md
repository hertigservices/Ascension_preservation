# Ascension community upload service

A browser contribution page and private HTTP bridge for the existing Ascension cache consolidator. Source lives here in the canonical preservation repository; the live intake and `ascension-data` checkout are separate deployments.

## Current state — 2026-09-11

The community service is deployed at **https://ascension-cache-upload.ascension-archive.workers.dev**.
Real contributions have completed validation, consolidation and dataset publication.
The source includes the folder picker, browser filtering, private R2/D1 intake API,
collector, receipt status, batching and the shared desktop/manual queue.

Read [How contributions become the public dataset](../cache-consolidator/docs/PROCESSING-PIPELINE.md)
for a stage-by-stage explanation, direct source links, privacy and data-loss gates,
and the dated comparison with installed processing code.

The current review list is **AIO, CoASniff and WildcardHarvest**. CoAReader and
AscensionHarvest have explicit filtered reference-data paths. The exact accepted
names and review policy live in `shared/policy-contract.json`; historical verification
notes may describe earlier, more restrictive versions.

The collector runs separately from the website and desktop window. Operators must
configure private storage, credentials, retention and startup; publishing this source
does not provision those services. Production secrets and raw contributions are excluded.

## Local development

Requires Node 22.13+ (tested with Node 24.19) and Python 3. Standard npm commands are shown below. On this machine the wrapper used by Sites scripts had a Windows npm path problem; the explicit entrypoint works:

```powershell
node 'C:/Program Files/nodejs/node_modules/npm/bin/npm-cli.js' run dev
node 'C:/Program Files/nodejs/node_modules/npm/bin/npm-cli.js' run build
node 'C:/Program Files/nodejs/node_modules/npm/bin/npm-cli.js' test
node 'C:/Program Files/nodejs/node_modules/npm/bin/npm-cli.js' run test:collector
node 'C:/Program Files/nodejs/node_modules/npm/bin/npm-cli.js' run typecheck
```

The preview remains usable for on-device file inspection while uploads are disabled. `lib/prepare.worker.mjs` handles local ZIP/Lua processing. `shared/policy.mjs` is the shared upload-policy implementation; `lib/intake-api.mjs` owns the HTTP contract; `collector/collector.py` bridges to the existing publisher.

Tests use isolated temporary directories, real SQLite through Node, an in-memory R2 adapter, and a mocked Turnstile response. The HTTP integration test uses the real Python collector and Node validator with a separate test inbox. These tests do **not** constitute a live Cloudflare/Turnstile test or a new push to the real dataset. Real browser contributions have also exercised the deployed flow; automated tests remain separate from that live evidence. Optional WebMCP exposes only the sanitized preview summary; a compatible runtime was unavailable for its interactive validation.

## Deployment

Two deployment surfaces are prepared:

1. Sites: `.openai/hosting.json` declares logical `DB` and `BUCKET` bindings. Resume registration reconciliation in the owning task, retain the returned project ID, and follow the Sites publishing workflow. Do not initialize a nested Git repository inside the canonical source tree; use a clean, source-only deployment checkout for the Sites remote. A private Sites deployment is a maintainer preview; public community access and machine collector access must be configured explicitly before opening intake.
2. A Worker in the maintainer's own Cloudflare account, using the same compiled `dist/server/index.js` and `dist/client` output. This path is useful for anonymous public access and direct collector authentication.

For the direct Cloudflare path, complete these once in the intended account:

```powershell
node node_modules/wrangler/bin/wrangler.js login --scopes account:read user:read workers:write workers_scripts:write workers_routes:write d1:write
node node_modules/wrangler/bin/wrangler.js d1 create ascension-contribution-queue
node node_modules/wrangler/bin/wrangler.js r2 bucket create ascension-contribution-private
```

Create a Turnstile widget in the Cloudflare dashboard restricted to the final hostname (for example the chosen `workers.dev` hostname). Keep its secret off chat and source control. R2 must have public access disabled. Configure a bucket lifecycle rule to delete upload objects after seven days and abort incomplete multipart uploads; the collector also deletes expired objects and metadata. Lifecycle expiration is required to enforce retention when the local computer is offline.

Build first, then prepare ignored local configuration:

```powershell
python scripts/prepare-cloudflare.py --origin https://YOUR-FINAL-HOSTNAME --database-id YOUR-D1-UUID --site-key YOUR-TURNSTILE-SITE-KEY
```

This creates `wrangler.production.json` with uploads disabled and private collector settings under `C:/AscensionArchive/upload-private`. It generates collector and quota secrets there without printing them. Restrict that directory to the service user and administrators. Apply the generated migration, deploy with intake disabled, and then install secrets:

```powershell
node node_modules/wrangler/bin/wrangler.js d1 migrations apply ascension-contribution-queue --remote --config wrangler.production.json
node node_modules/wrangler/bin/wrangler.js deploy --config wrangler.production.json
node node_modules/wrangler/bin/wrangler.js secret put COLLECTOR_TOKEN --config wrangler.production.json
node node_modules/wrangler/bin/wrangler.js secret put RATE_SECRET --config wrangler.production.json
node node_modules/wrangler/bin/wrangler.js secret put TURNSTILE_SECRET --config wrangler.production.json
```

Use the private generated `collector-token` and `rate-secret` values for their corresponding Worker secrets; enter the Turnstile secret through Wrangler's prompt. Do not put secrets into shell command arguments, Git, the hosting manifest or the source ZIP. `scripts/install-cloudflare-secrets.py` can send the two generated values on stdin and prompt privately for the Turnstile secret.

Validate the deployed disabled page and then configure the collector. Only after real Turnstile, retention, upload and receipt checks succeed should `UPLOADS_ENABLED` become `true` in the production configuration and the Worker be redeployed. Parser CPU time must be measured on the chosen Workers plan: the free tier's small CPU allowance is not a promise that all 4 MiB Lua submissions will succeed. A paid Worker CPU budget or smaller Lua limit may be needed. Storage is bounded to 2 GiB of reserved submissions and 100 submissions per day, with five daily admissions per hashed IP; R2 free allowances are not a billing cap.

## Collector operation

`collector/config.example.json` is configured for the known live intake paths but contains no endpoint or credentials. `prepare-cloudflare.py` writes a private usable copy once the origin is known. The collector takes its token from `ASCENSION_UPLOAD_COLLECTOR_TOKEN`, or from `token_file` in that private configuration.

```powershell
python -B collector/collector.py --config C:/AscensionArchive/upload-private/collector-config.json --once
```

After verifying the first actual contribution, install the optional user-session startup task:

```powershell
powershell -File collector/install-startup.ps1 -ConfigPath C:/AscensionArchive/upload-private/collector-config.json
```

The task uses limited privileges, runs at sign-in, and checks every 30 seconds. If the PC is off, the private queue waits; seven-day retention still applies. This is a sign-in task, not a Windows service that runs while nobody is logged in. The collector code is installed separately, and its limited-privilege startup task is enabled and running. The first real publication receipt remains to be checked.

The public upload Worker never needs a GitHub token. Use a dedicated non-admin collector account where practical. Validation is a separate process with restricted inherited environment, bounded input and heap/time limits, **not an OS security sandbox**. It still runs with the service account's filesystem permissions. No shell or Lua interpreter executes uploaded content. Raw archives never reach the collector. Dedicated account/VM isolation is appropriate before anonymous public traffic; do not use an administrator account with broad unrelated credentials.

## Receipts, retries and provenance

- Receipt tokens are URL-fragment capabilities sent as bearer headers to the status endpoint. They reveal status only, never file bytes. Upload tokens and collector tokens are separate.
- Completed upload slots are immutable by checksum; retrying an interrupted upload is safe while its in-memory upload session remains available. Reloading during an incomplete upload currently requires a new submission; the private receipt remains bookmarkable after successful completion.
- The collector resumes verified downloads, uses an OS advisory lock, renews its server lease, retries transient publication failures, and retains a result receipt before acknowledgment. After five processing attempts, the submission becomes review-required.
- Exact repeated accepted bundles share a content-derived inbox path. Partial overlaps and anonymous submissions are observations, not proof of independent people. No executable AIO code is promoted by anonymous corroboration counts.
- Original realm-directory text and timestamps are not uploaded in this version. Detected/selected **mode** is preserved per file; ambiguous data stays unknown. Generated mode folders use an explicitly unknown realm. The manifest is private provenance and retains per-file hashes; ingestion time must not be described as the original capture date. Extending capture/realm metadata requires a reviewed policy change.
- Private upload/download/review copies expire after seven days. Accepted filtered game data remains in the consolidator and public archive. Published Git history cannot be recalled by deleting a receipt. Reviews must happen before expiry or the contributor must resubmit.
- `needs_review` can include a commit link when the accepted portion was published and held files remain. Review files are under the private job directory, outside all scan roots. This first version requires a maintainer to inspect and manually release such files; there is no automatic review approval endpoint.
- Stop new intake by setting `UPLOADS_ENABLED=false` and redeploying. Existing upload tokens can finish an admitted submission until expiry. Stop local processing with `Stop-ScheduledTask -TaskName 'Ascension Private Upload Collector'` if installed. Pause/delete the task separately to prevent future sign-ins from restarting it.

## Integration ownership

No existing consolidator implementation, live consolidator configuration, raw submissions, merged data or `ascension-data` files were edited for this project. The canonical consolidator had unrelated uncommitted work when this task began; it was left alone. The browser policy covers eight addon filenames without changing the old CLI packager. The two added Ascension capture formats remain private for review; the consolidator has no automatic merge rules for them yet. Future registry changes should update this policy and the shared privacy tests together.

## Large-folder and replacement-location verification (2026-09-10)

The alternate folder picker updates the matching WDB/Account button for the current page session, displays the replacement folder name, and preserves the other folder choice. Selecting a new installation resets both replacements. Browser directory inputs remain the fallback where the native directory picker is unavailable. Existing prepared files remain selected; exact duplicates are reported without adding another copy.

The 512 MiB aggregate limit is enforced in the browser, API manifest validator and installed collector. The 4 MiB part limit, 256-part cap, ZIP limits and 2 GiB queue reservation cap remain in place. Direct WDB selection uses bounded reads, keeps complete record boundaries, and discards all staged parts if the source fails validation. Prepared parts are Blobs and progress updates keep large selections responsive.

Read-only local checks prepared and revalidated the Desktop WDB (7 parts / 9,008,107 bytes) and client WDB (88 parts / 319,572,018 bytes). No local game data was uploaded by those checks. Validation: 24 JavaScript tests, 8 Python tests, TypeScript and production build passed. Browser picker interaction still needs user confirmation; native browser automation was unavailable in this session.

## Published worker startup fix (2026-09-10)

The framework transformed the page's `import.meta.url` into a build-machine `file:///ROOT/app/page.tsx` URL, so the earlier source-only preparation checks missed a browser startup failure. The worker now uses Vite's explicit `?worker&url` asset import. Production builds run `scripts/verify-built-worker.mjs` to reject that broken URL, verify the emitted worker asset, and process synthetic WDB and Account fixtures using the actual bundled code. Passing the production origin also checks that the deployed JavaScript assets match the build. Constructor failures now clear the busy state and show a processor error, without incorrectly blaming folder size. The page explicitly distinguishes actual WDB/Account folder selection from installation-root selection.

## Receipt navigation (2026-09-10)

Receipt links now display and focus a status panel above the upload form, respond to same-page hash changes, and expose a manual refresh alongside 30-second polling. The panel shows part/review counts, last successful check time and lookup errors. Incomplete links produce an explicit error; Markdown-escaped separators are accepted. The former bookmark label is now a receipt link with instructions to save or bookmark it. Two parser regression tests, TypeScript, production build and deployed asset verification passed. The first real 90-part submission's receipt endpoint returned processing with two review-only parts during verification; publication remains unconfirmed.

## Background collector windows (2026-09-10)

The collector now launches its validator, publisher and Git verification with Windows CREATE_NO_WINDOW, and starts the publisher unbuffered with no interactive stdin. Existing publisher logging and the cross-process publication lock remain intact. A desktop watcher can report temporary contention while the collector owns that lock; it is not a second active merge. The first real upload's visible publisher console was hidden without stopping its process. The collector's eight integration/unit tests passed after the launch change.

The first real upload completed validation and export but publication was blocked by 11 column-count regressions; the privacy audit passed. Its receipt was held as needs_review after the collector returned it to the retry queue, retaining the files and preventing repeat collector attempts. The idle collector was then restarted to load the hidden-launch fix. The independent desktop watcher had already begun its own retry; no active publisher was killed and no audit baseline was relaxed.

## Automatic contribution processing (2026-09-10)

The website collector owns publishing while the desktop inbox watcher is off. Browser WDB observations have unknown original capture dates; upload time must never make an old record look newer. The merge repairs earlier web-source timestamps from the source ledger without changing any payloads. Dated observations keep priority; a date from another mode cannot promote an undated observation in this mode. Unknown-date selections are stable by first observed source, and every conflicting payload remains in the lossless raw archive. Column/privacy gates are unchanged.

Ascension_CoAReader and AscensionHarvest are automatically filtered and deduplicated into lua/reference-captures by content hash. Their supported reference branches are retained; private/configuration branches are excluded. Different snapshots remain separate, with no invented newest-wins merge. This preserves reference evidence, not a ready-to-import game-server database. AIO_Client, CoASniff and WildcardHarvest still use the existing private-review path.

Validation includes capture-date and mode-isolation regressions, undated-repeat stability, reference filtering/deduplication/conflict preservation, exact round trips of the two real filtered captures, and a Lua control-byte escaping regression. The initial 11 column regressions were traced to arrival timestamps and cross-mode date borrowing; correcting those restores all previously selected payloads in the three affected views while retaining new entries. The pre-repair metadata is saved privately. Full automatic publication is being retried against the existing checks.

The background collector keeps a rotating private diagnostic log (1 MiB, three backups). Validation has a finite batch-scaled timeout of 2 to 30 minutes, retaining the 128 MiB Node heap limit, so a busy desktop does not reject a large valid submission after only two minutes. The scheduled collector starts at sign-in and requires this PC to remain awake and signed in; the desktop consolidator window is optional and its inbox watcher should stay off when the collector owns publication.


## Batched publication and verified repeat handling (source, 2026-09-10)

The collector claims up to eight currently waiting submissions before validation, renews every claim while working, and runs one consolidation/commit/push for the accepted batch. Invalid peers do not block healthy jobs. Batch discovery stops on an empty queue, with a 60-second discovery deadline by default. Each submission retains its own receipt and review hold. The per-submission 512 MiB limit still applies; this is not a 512 MiB aggregate batch limit.

Private batch plans are consumed under the publisher lock. Inputs are copied and verified privately, then the entire batch becomes visible in the inbox with one rename. Batch wrappers do not change logical provenance. Publication evidence requires every accepted WDB in both merger and exported source ledgers, before a WDB-only bundle can establish skip evidence. Privacy, column and preservation checks still run before commit and push.

A later exact bundle can skip rebuilding only when a private index points to matching opaque evidence in a commit verified on the remote branch. Identity includes the multiset of file names, modes, lengths and hashes, plus the processing-policy revision. File order does not matter; multiplicity does. Partial overlap, changed rules, missing evidence and legacy receipts conservatively take the normal processing path. Review-only files never become automatically approved through duplicate handling. This optimization does not backfill proof for older uploads.

A durable private acknowledgment outbox retries receipt updates independently of publishing. If a claim expires after a successful push, the authenticated reconciliation endpoint can finish the receipt after commit proof is reverified, but cannot take over an active claim or release an intentional review hold.

Deployment is coordinated: install the collector and policy contract, the publisher's batch_inputs.py/publish.py/intake.py, and the Worker API together at an idle boundary. Do not replace a running publisher. Canonical deployment manifests include the new files. Existing private state is preserved. This section describes prepared source; deployment status is recorded separately by the maintenance task.

The initial no-op optimization is limited to WDB-only bundles first seen under the current rules. Every input hash must be absent from both the pre-run ledger and merged source index before the checked pipeline runs. Missing or malformed state disables eligibility. Lua or mixed bundles, pre-existing entries and changed-rule repeats continue through ordinary processing and cannot establish new skip evidence. This avoids treating stale merger state or unrelated Lua outputs as proof of complete coverage; it does not claim a rule change rebuilds old merger state. Receipt publication evidence and eligibility to skip future processing are separate fields.


## Shared desktop queue and live contributions (2026-09-10)

The desktop window has separate Manual inbox and Live contributions tabs. The collector publishes a redacted local stream snapshot every 30 seconds; the view shows time, size, detected mode, processing stage, terminal result, commit and local logs. Original realm names are not collected. Closing the window does not stop the collector or its durable manual requests. Stale status is labeled explicitly.

Consolidate now queues manual work. Online batches finish first, with one manual request allowed after each batch to prevent starvation. The publisher owns preparation, collision handling, a fresh five-second content stability check, consolidation and filing under the same lock. Transient manual failures back off automatically for up to five attempts; the live tab can retry a failed manual request. A paused collector leaves requests queued and reports its unavailability.

Only roots with supported contents accounted for in merger/export state can be marked completed. Archives require exact extraction-hash evidence. Failed archives and unsupported-only inputs remain pending. A mixed bundle whose supported data was published can be archived with its explicitly ledgered unread WDB files preserved; the completion journal and `archive/RETAINED-FILES.md` identify those files for future parser work. Zero-record Lua captures qualify only with successful merger completion metadata; bare zero counts do not prove completion. Filing rechecks every original file hash, preserves names, refuses archive collisions and never deletes raw evidence. Changed or newly arriving roots remain in place. Historical archive data stays available to the pipeline for provenance and recovery, but is excluded from pending-inbox counts and watcher triggers. The Tidy button files only journaled completed inputs, never arbitrary inbox folders.
