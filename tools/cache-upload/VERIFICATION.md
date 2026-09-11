# Source publication verification — 2026-09-11

The publication checkout passed all 15 consolidator test scripts, the Python
collector/shared-queue suite, and 31 JavaScript tests. Tests used temporary work,
output and repository directories, not the live inbox. The included pipeline guide
records the installed-source comparison and its remaining module variants.
Source was screened for credential patterns, matches to installed project secrets,
and private/runtime file inclusion. This is not a guarantee against every possible leak.

## Earlier deployment notes

The entries below are historical checkpoints. Policy and test counts changed afterward;
use the current README and policy-contract.json for current behavior.

# Verification record — 2026-09-10

- 20 JavaScript tests pass: structural WDB checks, record-boundary splitting, literal-only Lua parsing, branch privacy, UTF-8 round trips, ZIP traversal/symlink rejection, manifest normalization, origin enforcement, admission quotas, bearer-token separation, checksum/retry behavior, exclusive queue leases, publication receipt rules, exhausted retry handling, storage reservation and HTTP upload-to-collector delivery.
- Seven Python collector tests pass with the real Node validator: atomic staging, repeated delivery, review-code separation, bad-checksum rejection, already-acknowledged recovery, watched-directory isolation and restricted validator environment.
- TypeScript validation passes.
- Production build completes. The browser preprocessing worker is a separate emitted asset.
- Compiled Worker served the page with HTTP 200, disabled configuration with HTTP 200, and an unauthorized/missing receipt with HTTP 404 against local D1/R2 emulation. The generated schema applied successfully to the local database.
- Read-only checks against existing published AIO_Client.lua, MobSpells.lua, GatherMate2.lua and Auctionator_Price_Database.lua all passed filtering and idempotent revalidation. No real source payload was copied into this project.
- Tests stage into temporary isolated inboxes. No real contributor upload, live consolidator publication, dataset modification or GitHub push has been performed. Cloudflare infrastructure and the public site have now been deployed. Uploads are enabled for the first real browser contribution.
- Live Cloudflare checks: public page HTTP 200; configuration HTTP 200 with uploads disabled; unauthenticated collector request HTTP 401; authenticated installed collector successfully reported an empty queue. A live request exposed Cloudflare rejecting Python’s default User-Agent (1010); the collector now identifies itself as `AscensionArchive-Collector/1.0`, covered by the HTTP integration regression check.
- R2 has no public development URL or custom domain. Verified active lifecycle rule expires all objects after seven days and aborts incomplete multipart uploads after one day. The remote D1 migration succeeded.
- Collector deployment hashes verified through the manifest deployment tool. Most recent receipt: `C:/AscensionArchive/upload-service/deployments/private-upload-collector-20260910T230424090431Z/receipt.json`.
- Live isolated integration passed: a 4,186,112-byte synthetic WDB and a 120,026-byte sanitized synthetic addon each uploaded successfully twice; completion, authenticated collector download, byte hashes, real Node validation and isolated inbox delivery passed. Publishing was explicitly disabled for synthetic data. Remote fixtures and their queue entry were removed. This verifies those payloads on the current Workers plan; it does not establish headroom for every possible 4 MiB Lua input or sustained load.
- Turnstile secret installed after user approval. The canonical Siteverify endpoint returned `invalid-input-response` for a deliberate invalid token, without a secret error. The active public API returns 403 for invalid verification or the wrong origin and 401 for unauthenticated collector access. Public configuration reports uploads enabled.
- The startup task `Ascension Private Upload Collector` is running at limited privilege and restarts at user sign-in. Worker activation version: `5c1f7d47-bb19-46c1-a8e4-180fabde569c`.
- Pending: the first successful human Turnstile challenge and real contribution reaching an actual GitHub publication receipt. No real dataset was changed by the deployment tests.
- Browser interaction and visual QA were not requested and were not performed. Optional WebMCP was not runtime-tested because no compatible permitted context was available.

Source location: `C:/AzerothRealm/source/Ascension_preservation/tools/cache-upload`.
The existing live intake and other in-progress canonical consolidator edits remain unchanged.

## Account recognition fix — 2026-09-10

The selected installation root was correct. Its Account folder contained CoAReader and AscensionHarvest captures that were absent from the six-name allowlist. Added both formats (including `.lua.bak`) with explicit game-reference filtering and mandatory private review, shared by browser, server manifest and collector. New tests cover field exclusions, review enforcement despite misleading manifest flags, idempotence, normal Account-folder discovery and preventing publication of review-only files.

Read-only processing of the user's actual Account files found seven recognized files and prepared two useful captures: 95,043 bytes of CoAReader references and 762,198 bytes of AscensionHarvest references. Five notices concern older files without eligible reference branches and an oversized backup. No local capture bytes were copied into source or uploaded by this check. Build, TypeScript and 27 tests pass.

The new policy was deployed and hash-verified in the running collector before deploying the public Worker. Worker version: `44c04c53-fbd8-49b6-9d61-9f6520d49580`. Collector receipt: `C:/AscensionArchive/upload-service/deployments/private-upload-collector-20260910T233408520215Z/receipt.json`.
