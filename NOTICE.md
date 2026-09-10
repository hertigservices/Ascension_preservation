# NOTICE — scope, copyright, and intended use

This repository is a **preservation and interoperability** project for Project
Ascension's classless WoW 3.3.5a experience, created after the service's shutdown
on 2026-09-04. It is **not affiliated with, endorsed by, or supported by** Project
Ascension or Blizzard Entertainment.

## What this repository contains

Original work by the contributors:

- Protocol documentation and byte-level wire specs written from our own captures.
- A small Python server stack (auth shim + world server) that re-implements
  enough of the login/world protocol for the client to connect locally.
- Reverse-engineering and analysis tooling.
- Schema and structural exports describing the Character-Advancement data model.

The reverse-engineering here is for **interoperability** (making a client you own
talk to a server you run) and **archival** of a shut-down service.

## What this repository does NOT contain, and must not

- **No Blizzard or Ascension client binaries** (`Ascension.exe`, DLLs, the
  launcher).
- **No game data files** — no MPQs, DBCs, maps/vmaps/mmaps, models, textures,
  sounds, or UI art.
- **No bulk client content** (the loose `Data\Content\*.json` datasets, spell
  graphics, etc.).

Those assets are the copyrighted property of their respective owners. To run this
stack you must supply them **from your own legally obtained Ascension client**.
`data/MANIFEST.md` lists exactly which files the server reads and which extractor
tool regenerates each derived file.

## Derived data (`data/`, `server/data/`)

This repository is **public**. It ships **structure and small exports derived
from the client's own data** (e.g. the Character-Advancement tree layout, sanitized
CA entries, server seed state) so the stack is runnable and useful to the project's
developers — all of whom already own the client. It does **not** ship the client's
copyrighted **binary** assets (MPQs, DBCs, models, textures, audio, UI art). Every
derived file can be regenerated from your own client with the tools in `tools/`
(`data/MANIFEST.md` §C lists which tool rebuilds which file). If a maintainer
prefers a stricter line, deleting the derived JSON and regenerating locally leaves
the repo fully functional.

## Credentials

This package was scrubbed of the maintainer's personal account credentials and
the real database password before publication, and excludes raw live-login packet
captures and logs. Do not re-introduce real credentials or captures.

## No warranty

Provided as-is, for preservation and personal/offline use, without warranty of
any kind.

## Outside contributions

The original AscensionAuthGate contribution is credited to **FirstOni**. See [its third-party notice](contrib/AscensionAuthGate/THIRD-PARTY-NOTICE.md) for the supplied package provenance and license boundary; the repository's original-work MIT grant must not be assumed to relicense that pre-existing contribution.

Linux/Wine support, LAN deployment and the CoA-capable-core class handling are credited to **maribela** (2026-09-10). See [LAN-AND-LINUX.md](docs/LAN-AND-LINUX.md) and [COA-CAPABLE-CORE.md](docs/COA-CAPABLE-CORE.md).
