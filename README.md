# Ascension Preservation


**Community data processing:** [Inspect how uploads are validated, merged, audited and published](tools/cache-consolidator/docs/PROCESSING-PIPELINE.md).

Tools and protocol research for reconstructing Ascension locally with a user-supplied
3.3.5a client and AzerothCore installation.

**[Start with the unified setup guide](docs/SETUP.md).**

| Component | Purpose |
|---|---|
| [AuthGate](contrib/AscensionAuthGate/) | Default original-client authentication, adapted from FirstOni |
| [Server](server/) | AzerothCore bridge, legacy auth shim, and separate Python protocol testbed |
| [Cache tools](tools/cache-consolidator/) | Intake, lossless variants, cache installation, world import |
| [Character importer](tools/character-importer/) | Standalone offline Bind My Soul importer |
| [Stock-client work](stock-client/) | Experimental generators and authored compatibility shim |
| [Protocol reference](docs/WIRE-SPEC.md) | Auth and world wire research |
| [Linux, Wine and LAN](docs/LAN-AND-LINUX.md) | Original client on a Linux host, or on a different machine than the server |
| [CoA-capable core](docs/COA-CAPABLE-CORE.md) | Creating classes 12..32 natively instead of on a carrier class |
| [Compatibility manifest](manifests/compatibility.json) | Runtime distinctions and recorded dependency baseline |

[Azeroth Control](https://github.com/hertigservices/azeroth-control) manages realms
and provides the launcher independently of Ascension.
[Ascension Data](https://github.com/hertigservices/ascension-data) maintains recovered
datasets, provenance, hashes, and release snapshots independently of source code.

The character importer and cache tools remain standalone commands. Neither requires
the panel or Python testbed. Source history and original component licenses are retained.

Mutable state, credentials, logs, client files, DBCs, MPQs, database backups and
intake submissions belong in your installation or archive, outside this checkout.
See [runtime configuration](docs/SETUP.md#runtime-configuration) and
[deployment and rollback](docs/SETUP.md#deployment-and-rollback).

The default original-client path uses the reviewed AuthGate package and the
AzerothCore bridge. See [the current authentication guide](docs/HOW-THE-REDIRECT-WORKS.md).
The legacy shim on 3799 remains an explicit alternative. It defaults to one
Windows machine on loopback; running the client under Wine, or on a separate
machine from the server, is opt-in and documented in
[Linux, Wine and LAN](docs/LAN-AND-LINUX.md) — contributed by **maribela**.

The bridge and Python testbed are distinct runtime paths. A feature verified in one
is not automatically verified in the other. Stock-client compatibility work remains
experimental. The [pre-consolidation server README](docs/architecture/LEGACY-SERVER-README.md)
and handoffs are historical research context; use the setup guide for current paths.

[Migration notes](docs/MIGRATION.md) · [Source origins](manifests/source-origins.json)
· [Notice](NOTICE.md) · [License](LICENSE)
