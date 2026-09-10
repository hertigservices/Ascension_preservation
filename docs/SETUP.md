# Unified setup

This ecosystem has two code repositories and one data repository. Keep the code
checkouts together if convenient; your installed realms may stay anywhere.

```text
source/
  azeroth-control/
  Ascension_preservation/
archive/
  ascension-data/
installed-hub/
  control/                deployed panel code and machine state
  realms/                 local profiles and deployed preservation helpers
  server*/                installed binaries, configs and extracted data
  mysql/                  existing databases
```

## Choose a component

- **Manage a realm:** follow Azeroth Control's `docs/INSTALL.md`, retaining its
  pinned build prerequisites. Existing servers do not need a rebuild for this consolidation.
- **Restore a character:** `cd tools/character-importer`, install `requirements.txt`,
  and run `python bms_import.py --help`. Run a preview against the intended realm
  before an apply. The importer remains its own installable Python package.
- **Install recovered caches:** `cd tools/cache-consolidator`, then
  `python -B tools/install.py --data <ascension-data>/cachedata`. Its default
  invocation previews changes; `--write` applies them. Client files are supplied
  by the user.
- **Prepare world data:** from that same folder,
  `python -B tools/import_world.py --help`. Keep its SQL staging and backups
  outside source and verify the target config/database.
- Both cache commands are walked through step by step in
  [using the data](../tools/cache-consolidator/docs/USING-THE-DATA.md). Every
  command in that guide runs from `tools/cache-consolidator`, which is why the
  two above start by moving there.
- **Use the Ascension bridge:** configure the private runtime file below, retain your
  existing core and client, and follow the protocol/boot handoffs for the client redirect.
  The bridge listens on loopback 8088 and reaches AzerothCore on 8086. The default
  AuthGate route validates against authserver 3724 and serves the client login
  responder inside the client on 3725. Follow [the current AuthGate guide](HOW-THE-REDIRECT-WORKS.md);
  an already migrated installation needs no client reinstall. The legacy shim on
  3799 is optional. The Python testbed uses 8087 and separate JSON character state.
- **Run the client on Linux, or on another machine:** both are opt-in and neither
  changes the single-box default. `ASC_BRIDGE_HOST` moves the bridge off loopback
  and `authgate.cfg` points the client at it; read
  [Linux, Wine and LAN](LAN-AND-LINUX.md) first, including its security section.
- **Run against a core that has the CoA classes:** set `ASC_AC_VALID_CLASSES`
  so classes 12..32 are created natively instead of on a carrier class. See
  [CoA-capable core](COA-CAPABLE-CORE.md).

## Runtime configuration

Azeroth Control accepts `AZCTL_HOME` as its installed hub root. HTML/Python code is
loaded from the checkout or deployment; credentials and installed-addon state are
resolved under the installed hub. Realm registry paths stay relative to that hub.

Preservation reads `ASC_CONFIG`, or `server/runtime.local.json` beside deployed
server code. Copy `templates/runtime.example.json` into a private location and set
real paths. Environment variables override the file:

| Setting | Environment override | Purpose |
|---|---|---|
| runtime_dir | ASC_RUNTIME_DIR | Root for new runtime state and logs |
| state_dir | ASC_DATA_DIR | Existing JSON character/build/account state |
| log_dir | ASC_LOG_DIR | Logs |
| worldserver_conf | ASC_WORLDSERVER_CONF | Core config actually used by the bridge |
| dbc_dir | ASC_DBC_DIR | User-supplied Ascension DBC directory |
| ca_ref_dir | ASC_CA_REF | User-supplied Character Advancement reference directory |

An existing installation can point state_dir and log_dir at its current paths;
there is no need to move character state. New defaults live under the user's
local application-data AscensionPreservation directory. Testbed fixtures are under
`fixtures/testbed`; services never write to them.

Cache tools read private `runtime.local.json` beside `tools/config.py`, with keys
`work`, `out`, `publish_repo`. Alternatively use `ASCENSION_CACHE_WORK`,
`ASCENSION_CACHE_OUT`, and `CONSOLIDATOR_REPO`. `ASCENSION_CACHE_DATA` selects a
read-only input dataset for install/import tools. Keep intake `config.json`, its
scan roots, raw submissions and ledger in the existing work directory.

## Deployment and rollback

Azeroth Control's `tools/deploy.py` deploys either repository using its explicit
manifest. No implicit directory mirroring occurs, and unlisted files are preserved.

```powershell
python <control-source>/tools/deploy.py --plan <private>/panel-plan.json --source <control-source> --target <installed-hub> --manifest <control-source>/manifests/deployment.json
python <control-source>/tools/deploy.py --apply <private>/panel-plan.json
```

For preservation use its `manifests/deployment.json` with the preservation source
root and the same installed hub. For cache tools use `manifests/cache-deployment.json`
and the intake work directory as target. Configure private runtime paths first.

Each plan records the source commit and exact before/after file hashes. Apply
refuses source or destination drift. Backups and receipts are under the target's
`deployments/`. `--verify <receipt.json>` checks every installed hash;
`--rollback <receipt.json>` restores the old owned files and refuses later edits.
The tool never restarts a realm or changes a database/client. Running processes
continue with their loaded modules until their next normal restart.

## Reproduce the recorded code and data

`manifests/compatibility.json` records the ecosystem baseline, mode/port distinctions,
and existing build pins. `manifests/dataset.json` identifies a hash-checked data
snapshot; the data repository retains the full file manifest and source provenance.
Use tagged code releases and dataset snapshots, rather than assuming every `main`
revision is interchangeable. Deployment receipts identify the exact installed code.

Existing core checkouts have local modifications. Their recorded base commit alone
is not a complete recipe for the already-built server binaries. Consolidation keeps
those independent checkouts and installed binaries intact; it does not claim a new
core build or a newly verified stock-client gameplay path.
