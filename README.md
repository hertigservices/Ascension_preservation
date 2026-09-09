# Ascension Preservation — Conquest of Azeroth (local server archive)

A preservation archive of **Project Ascension**'s classless custom WoW 3.3.5a
experience — focused on the **Conquest of Azeroth (CoA) custom-class /
Character-Advancement** system — rebuilt so the **unmodified Ascension client can
log into a fully local server and play offline**.

Project Ascension's live service shut down **2026-09-04**. This repository is the
post-shutdown record of *how the client works* and a working local re-host of the
login → world → Character-Advancement path.

> **What this is:** original reverse-engineering, protocol documentation, and a
> small Python server stack (auth shim + world server) that speaks enough of
> Ascension's wire protocol to satisfy the real client.
>
> **What this is NOT:** it does **not** contain the Ascension/Blizzard client,
> its DBCs, MPQs, art, or bulk content. You bring those from your own legally
> obtained client. See [`NOTICE.md`](NOTICE.md) and
> [`data/MANIFEST.md`](data/MANIFEST.md).

---

## Start here

1. **[`docs/HOW-THE-REDIRECT-WORKS.md`](docs/HOW-THE-REDIRECT-WORKS.md)** — the
   detailed writeup of **how the client is redirected to a local server and gets
   in-game**. Read this first; it is the heart of the project.
2. **[`docs/handoffs/HANDOFF-ARCHIVE-SESSION.md`](docs/handoffs/HANDOFF-ARCHIVE-SESSION.md)**
   — full boot runbook + everything already solved.
3. **[`docs/handoffs/HANDOFF-SPELLS-TALENTS-CLASSES.md`](docs/handoffs/HANDOFF-SPELLS-TALENTS-CLASSES.md)**
   — the current frontier: CoA spells, talent trees, custom classes.
4. **[`docs/KNOWN-ISSUES.md`](docs/KNOWN-ISSUES.md)** — symptom-first list of
   failures that look like server or protocol bugs but are not. Check it before
   diagnosing a hang or a silent disconnect.

## What works

- The **unmodified `Ascension.exe`** logs into a local stack (auth shim on
  `127.0.0.1:3799`, world server on `127.0.0.1:8085`), unelevated, no UAC, no
  binary patching, no network-crypto defeat.
- Client reaches **character-select**, **enters and stays in the world**
  (Sunstrider Isle), and touches the live service for nothing but CDN pings.
- The **Conquest-of-Azeroth / Character-Advancement panel renders** — 153
  populated class|tab buckets, 8873 entries, 42 classes — plus archetypes and
  working character creation into custom classes.
- 3.3.5a account-data exchange (keybinds, action bars) served from the server.

See the handoffs for the precise state and the open items.

## Repository layout

```
docs/            Protocol specs and narrative write-ups (our RE work)
  HOW-THE-REDIRECT-WORKS.md     ← how the redirect + in-game entry works
  WIRE-SPEC.md                  auth (3799) handshake, byte-level
  WORLD-WIRE-SPEC.md            world (8085) handshake
  ASCENSION-NOTES.md            master notes: client layout, redirect, DB rebuild
  KNOWN-ISSUES.md               symptom-first: hangs/disconnects and their real causes
  protocol/                     opcode + handler maps
  handoffs/                     session handoffs (boot runbook, CoA deep-dives)

server/          The runnable local stack
  shim3799.py                   permissive auth shim (port 3799)
  world_server.py               world server (port 8085)
  ascension_bridge.py           opcode bridge / forwarder
  rpm_readk.py                  reads the session key from client memory
  *.ps1                         launch / restart helpers
  worldserver-bridge.conf       AzerothCore worldserver config (DB password REDACTED)
  data/                         server seed/state we generated (builds, chars, keybinds…)

data/            CoA CONTENT derived from the client (schema + exports)
  ca-export/                    Character-Advancement tree export (classes, tabs, edges…)
  sanitized-for-core.json       CA entries sanitized for import
  MANIFEST.md                   proprietary files you must supply from your own client

tools/           Reverse-engineering + analysis toolkit (~60 scripts)
reference/       Curated screenshots + Lua/opcode reference text
  ascension_opcodes.json        opcode id → name, 2058 entries (this client)
  ascension_custom_opcodes.json 754-entry subset, 749 of them above stock's 0x500 ceiling
area-52/         Area-52 "Free-Pick" realm-flavour specifics (see its README)
contrib/         Tools contributed by others, adapted (see each README)
  AscensionRedirect/            WinDivert packet redirect + Frida auth-send probe
  CoADataComparison/            Read-only CA/community comparison + synthetic tests
```

[`contrib/CoADataComparison`](contrib/CoADataComparison/README.md) compares
locally supplied CA and community exports by identifiers and source hashes.
It is an optional research tool, not a server component or a client-data bundle.

## Requirements

- Windows, Python 3.x.
- A legally obtained Project Ascension 3.3.5a client (see `data/MANIFEST.md` for
  the exact files the server reads/needs). Paths in the scripts assume a layout
  like `C:\AzerothRealm\client-ascension\…`; adjust to yours.
- For the AzerothCore-backed world-DB route: a MySQL/MariaDB instance and stock
  AzerothCore 3.3.5a binaries (nothing is recompiled).

## Privacy / secrets note

This package was scrubbed before publishing: the maintainer's account
email/passwords and the real DB password were removed, and raw live-login packet
captures (`e0-captures/`, `live-*.bin`) and server logs are **excluded**. The
local login uses account `test` / any password. If you regenerate content from a
live capture, do not commit captures containing real credentials.

## Credits

Reverse-engineering, documentation, and the local server stack were developed
iteratively with **Claude Code**. Preservation project — not affiliated with or
endorsed by Project Ascension or Blizzard Entertainment.
