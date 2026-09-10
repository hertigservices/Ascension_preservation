# AuthGate: the default original-client login and world path

Updated 2026-09-09 after successful original-client gameplay verification.
**AuthGate is the default authentication path**, adapted from **FirstOni's
AscensionAuthGate**. Use the reviewed package in
[`contrib/AscensionAuthGate`](../contrib/AscensionAuthGate/README.md).

The original `Ascension.exe` stays unchanged. Its genuine `Extensions.dll` is
preserved as `Extensions_orig.dll`, and the reviewed proxy is installed under
the original DLL name. This is a client-side proxy installation, so older
claims that the entire client installation remains unmodified no longer describe
the default. Shared game data is not changed.

## Current architecture and ports

```text
Original Ascension.exe + reviewed AuthGate proxy
  |-- SRP credential validation --> 127.0.0.1:3724  AzerothCore authserver
  |-- custom login responder ----> 127.0.0.1:3725  inside this client process
  `-- selected realm ------------> 127.0.0.1:8088  ascension_bridge.py
                                                  |
                                                  v
                                     127.0.0.1:8086  AzerothCore worldserver
```

| Component | Endpoint | Role |
|---|---|---|
| AzerothCore authserver | `127.0.0.1:3724` | Validate the existing account/password through SRP and supply the server proof |
| AuthGate | `127.0.0.1:3725` | Answer the client's custom login protocol and advertise the local bridge |
| World bridge | `127.0.0.1:8088` | Translate between the Ascension client and the configured core world |
| AzerothCore worldserver | `127.0.0.1:8086` | Serve the selected realm's world and character database |

Every endpoint above is loopback, and that is the reviewed default: one Windows
machine. Two opt-in deployments move it — the client under **Wine**, and the
client on a **different machine** from the server. Both are configured through
`authgate.cfg` plus `ASC_BRIDGE_HOST`, and both are documented, with their
security trade-off, in [LAN-AND-LINUX.md](LAN-AND-LINUX.md) (contributed by
**maribela**). Nothing on this page changes if you do not use them.

Port 3725 appears only while the client is running. Do not start a separate
helper on that port. The old Python auth shim on **3799 is not required** and
should be stopped/removed from this realm's startup dependencies. The standalone
Python world server on 8087 is an alternative historical route, not the world
endpoint advertised by this AuthGate build.

AuthGate requires an existing local account and its **correct password**. It
verifies the complete authserver SRP proof. The legacy instruction to use `test`
with any password does not apply. AuthGate neither creates accounts nor moves
characters; the selected profile, bridge configuration and account determine
the roster.

## Set up the original client

First prepare the intended AzerothCore realm, its databases and client-supplied
assets. The [fresh-machine reference](handoffs/HANDOFF-FRESH-INSTALL.md) covers
those prerequisites; use this guide for the current authentication/launch steps.
The archive's full loose `Interface/GlueXML/AccountLogin.lua` must contain its
`ASCENSION_ARCHIVE_REALMLIST` override. If preparing it from your own client,
retain the complete file and its login functions; a tiny replacement containing
only the override breaks the login UI. The historical research below explains
that hook, but its old port and boot commands are superseded.

From the repository root, build and inspect the reviewed source:

```powershell
Set-Location .\contrib\AscensionAuthGate
python -m pip install pefile
.\build.bat
.\test.bat
python .\verify-build.py
```

Use Visual Studio 2022 C++ x86 build tools; adjust the batch files' vcvars32 path
for another Visual Studio edition. No prebuilt DLL or genuine client asset is
shipped. The isolated tests do not load or patch a game client.

Close Ascension clients, resolve any hub junction to the **real original client
directory**, then install from the same package directory:

```powershell
.\install-client.ps1 -ClientRoot 'C:\YourAscensionClient'
```

The installer checks the supported executable and genuine extension hashes,
preserves the original extension, and creates the new proxy. An unexpected
existing proxy/backup is rejected for review. It does not copy or edit `Data`.
The original-client migration is already complete on the maintainer's hub;
existing users there should launch through the hub rather than reinstalling.

## Start the realm and launch

1. Start the **intended realm's** authserver on loopback 3724, configured
   worldserver on 8086, and world bridge on 8088. Keep their existing database
   names and mode-specific bridge arguments. Configure any redacted database
   placeholders locally; never commit the real credentials.
2. Keep the legacy shim stopped. Close competing Ascension copies; the world
   bridge still relies on one matching client process.
3. From a **normal unelevated PowerShell**, run the reviewed package launcher:

```powershell
.\start-client.ps1 -ClientRoot 'C:\YourAscensionClient' -Mode coa -ExpectedAuthserverPath 'C:\YourRealm\authserver.exe'
```

Use `-Mode ascension` for Free-Pick. The mode selects the matching realm name
and metadata; it does not switch the server database for you. The launcher
verifies the intended authserver executable, client hashes and local listeners.
Adding `-NoLaunch` performs a **read-only preflight**; it does not prepare settings
for a later direct launch.

The launcher sets both the archive glue override and the realm settings to
**`127.0.0.1:3725`** and uses process-scoped `RunAsInvoker`. Config.wtf alone is
insufficient because the native client can restore its own realm address.
AuthGate also redirects the known native production-auth endpoint to its local
responder. Leave `Data/enUS/realmlist.wtf` and all shared Data untouched.

The supported path is the package's `start-client.ps1`, or the already-updated
hub launcher. The old repository `server/launch-client.ps1` and the direct-launch
recipes in older handoffs belong to the legacy shim workflow. Their `-NoLaunch`
and restore behavior must not be confused with the current script.

## Hub integration and other realms

The maintainer's `ascension` and `coa` profiles intentionally share the original
client. Both now start their authserver and bridge without the shim. Their
world configs, bridge arguments and character databases remain distinct and
unchanged; the other four realm/client profiles were compared unchanged.

For another hub with those profiles, set `world.authserver` to `true`, remove
only the `shim3799` helper, and route the existing client launcher to AuthGate
with the selected mode and expected authserver executable. Preserve each
profile's other fields. Do not add the client-owned 3725 listener as a server
readiness dependency, and do not change unrelated realm launchers.

## Verify the result

Log in with an existing local account, choose its character, enter the world,
and move around. Look in the original client's **`proxy_auth.log`** for:

```text
[startup] original extension loaded
[gate] SRP6 self-test PASS
[gate] VALID (proceed)
=== AUTHSRV listening on 127.0.0.1:3725 (in-process) ===
***** M2 ACCEPTED -- client sent 0x10 (realm-list request) *****
```

These are status markers, not a required ordering. A normal auth-channel close
can follow world entry. The client should own listener 3725 and have an
established loopback connection to bridge 8088; the bridge should report
`AUTH_OK`, `CMSG_PLAYER_LOGIN` and `SMSG_LOGIN_VERIFY_WORLD`. AuthGate does not
log usernames, passwords, keys or packet contents. Do not revive the old capture
files or diagnostic rollback.

The reviewed build passed **59 isolated checks and two real local credential
checks**. With the shim stopped, the user verified the original client's existing
character was **working flawlessly**. Only then was the test copy removed. Its
shared Data junction was unlinked without traversing the original target, and
original-file hashes/shared Data were checked afterward.

Read the [security review](../contrib/AscensionAuthGate/SECURITY-REVIEW.md) for the
scope: a trusted single-machine design with build-specific hooks. The original
proprietary client has its own networking; AuthGate does not certify or filter
all of that traffic. Credit and supplied-source provenance are in
[FirstOni's attribution notice](../contrib/AscensionAuthGate/THIRD-PARTY-NOTICE.md).

## Historical shim research

The expandable record below preserves the earlier 3799/8087 workflow and its
protocol discoveries. **It is not the default setup guide.** Its permissive
password rules, shim startup commands and old launcher advice apply only to a
deliberate legacy setup; use the AuthGate instructions above for the maintained
original-client route.

<details>
<summary>Earlier shim-based redirect and standalone-world runbook (legacy)</summary>

# How the client was redirected to a local server — and got in-game

This is the part people ask about first: **how do you make the real, unmodified
Project Ascension client (`Ascension.exe`) log into a server running on your own
machine, instead of the official realm — and then actually walk around in the
world?**

The short version: **we never patched the client binary and never touched its
network crypto.** We redirected it the same way the client redirects itself, in
one line of glue Lua, and then stood up two small Python servers that speak
enough of Ascension's wire protocol to satisfy it. The client believes it is
talking to the live service.

Everything below was measured against a running client, not theorised.

---

## 0. The picture

```
                    ┌──────────────────────────┐
   Ascension.exe    │  1. glue Lua forces the  │
   (UNMODIFIED)  ───┤     realmList to          │──► 127.0.0.1:3799  auth  (shim3799.py)
                    │     127.0.0.1:3799        │
                    └──────────────────────────┘
                                                 ──► 127.0.0.1:8087  world (world_server.py)
```

Two servers, both bound to loopback. **The world port is not a free choice:** the
client's `Extensions.dll` only accepts a world address of `127.0.0.1` on `8085`,
`8087` or `8088`; anything else crashes the client at the realm list. `8085` is
AzerothCore's default and is usually taken by a real realm, so the Python world
server uses `8087` and the AzerothCore bridge uses `8088` (`tools/archive_ports.py`
is the one place this is decided, and the shim advertises whichever is listening):

| Server | Port | File | Job |
|---|---|---|---|
| Auth shim | `127.0.0.1:3799` | `server/shim3799.py` | Answer the login handshake, hand back a realm list pointing at the world server |
| World server | `127.0.0.1:8087` | `server/world_server.py` | Auth-session, character enumeration, drop the client into the world, keep it there |

---

## 1. Why the obvious approaches do **not** work

**The Electron launcher is a dead end.** It never sets the realm. Grepping the
entire `app.asar` — including the bytenode-compiled `main.jsc` — for `realmlist`,
`realmList`, or `Config.wtf` returns **zero hits**. The launcher only
authenticates against `https://api.ascension.gg/api`, which dies with the
service. So there is nothing *in the launcher* to hook. The launcher is bypassed
entirely.

**`realmlist.wtf` is neutered.** Ascension ships `Data/enUS/realmlist.wtf` as
exactly **13 bytes** — the literal `set realmlist` with no value. It cannot
override anything. Left untouched.

**`Config.wtf` is read but ignored for the realm.** This is the trap that cost
the most time. You can put `SET realmList "127.0.0.1"` in `WTF/Config.wtf`, watch
the client read the file (the `realmName` even reaches the login screen), and the
client will **still dial the official address**. Measured, from
`client-ascension/Logs/connection.log` with our value in the file at process
start:

```
GRUNT: state: RESPONSE_CONNECTED result: LOGIN_OK 51.210.230.10:3724
```

`51.210.230.10` is the **live** realm. By the time the login screen renders, the
client has already put the official address back into the realmList CVar in
memory. And on a clean exit the client **rewrites `Config.wtf`**, stamping the
official address back onto disk. So `Config.wtf` is cosmetic for our purposes.

---

## 2. The actual lever: one line of glue Lua

The address is forced in **glue Lua** — the last code that runs before the client
calls `ConnectToServer()`, after the in-memory CVar has already been reset. This
is the whole redirect:

**File:** `client-ascension\Interface\GlueXML\AccountLogin.lua` — the **whole** stock
file extracted from `Data\patch-B.MPQ` with the block below pasted in. A loose file
containing only the block shadows the MPQ copy and removes every other
`AccountLogin_*` function the glue XML needs (the login screen then fails to build). — the **whole** stock
file extracted from `Data\patch-B.MPQ` with the block below pasted in. A loose file
containing only the block shadows the MPQ copy and removes every other
`AccountLogin_*` function the glue XML needs (the login screen then fails to build).

```lua
ASCENSION_ARCHIVE_REALMLIST = "127.0.0.1:3799";

function AscensionArchive_ForceRealm()
    if GetCVar("realmList") ~= ASCENSION_ARCHIVE_REALMLIST then
        SetCVar("realmList", ASCENSION_ARCHIVE_REALMLIST);
    end
end
```

`AscensionArchive_ForceRealm()` is called in two places so nothing can undo it
before the socket opens:
- in `AccountLogin_OnShow`, **after** the `if IsGMClient and realmList then ... end`
  block that opens the function (≈ line 103 in the extracted file). That block does
  its own `SetCVar("realmList", ...)`; a call placed above it is overwritten. And
- **immediately before `ConnectToServer()`** inside `AccountLogin_Login()`
  (≈ line 283).

**Confirm it fired** — `client-ascension\Logs\LUA.txt`:

```
ASCARCHIVE AccountLogin_OnShow: realmList 51.210.230.10 -> 127.0.0.1:3799
```

That log line, showing the official address being rewritten to loopback the
instant before connect, *is* the redirect working.

> The client rewrites `Config.wtf` back to `51.210.230.10` on exit — that's
> expected and harmless. The **glue constant re-forces `127.0.0.1:3799` at the
> next login**, so it survives the rewrite. The glue is the source of truth, not
> `Config.wtf`.

### 2a. THE PORT — the second half of the bug

`realmList` **must include the port**: `127.0.0.1:3799`, *not* `127.0.0.1`.

A bare `realmList "127.0.0.1"` makes the client dial WoW's default auth port
**3724**. Our shim listens on **3799**. Result: nothing answers 3724 →
`LOGIN_SERVER_DOWN` → on-screen *"Unable to connect."* This looks identical to
"the redirect failed" but is really "redirected to the wrong port." Both halves —
**glue lever** and **explicit `:3799`** — are required.

### 2b. ⚠️ Junction-leak warning (read before touching a live install)

In our setup `client-ascension` is a **Windows junction into the live install**
(`…\resources\ascension-live`). This loose glue file forces the realmList
**unconditionally**, so if the *real* launcher/client is started while the file
exists, it too gets redirected to `127.0.0.1:3799` and fails to reach live. If
you are working against a still-live install, gate `AscensionArchive_ForceRealm`
on an archive-only sentinel, or restore the stock `AccountLogin.lua` before using
the live client. On a fully offline archive this does not matter.

---

## 3. Launching without UAC (and why it must be unelevated)

`Ascension.exe` ships a manifest requesting `requireAdministrator`, so a normal
double-click raises a UAC consent dialog. **The client must NOT be elevated**,
because the two servers read the session key out of the client's memory with
`ReadProcessMemory` (see §5), and a medium-integrity process cannot open a
handle into an elevated one.

> **This integrity constraint exists only because of the `ReadProcessMemory` read.** The
> optional `contrib/InProcessKeyOracle/` add-on reads K from *inside* the client instead, so the
> shim no longer needs to match the client's integrity for the key. `RunAsInvoker` below is still
> the tidy way to avoid the UAC prompt, but the elevation *dance* — matching integrity so RPM
> works — goes away with the oracle. See `docs/AUTH-APPROACHES-EVALUATED.md`.

The fix is a process-scoped compatibility shim — no registry write, no binary
patch:

```powershell
$env:__COMPAT_LAYER = 'RunAsInvoker'                     # satisfies requireAdministrator WITHOUT elevating
Start-Process -FilePath 'C:\AzerothRealm\client-ascension\Ascension.exe' `
  -WorkingDirectory 'C:\AzerothRealm\client-ascension' -NoNewWindow -PassThru
```

`__COMPAT_LAYER=RunAsInvoker` tells Windows to run the manifested-admin binary at
the caller's integrity level. `-NoNewWindow` forces PowerShell to use
`CreateProcess` (which *cannot* pop a consent dialog) instead of `ShellExecute`,
so if the shim ever stops applying the launch fails **loudly** with error 740
("requires elevation") rather than silently prompting. Measured, with a control
on a purpose-built exe carrying the identical manifest:

```
control: no shim   -> BLOCKED: native error 740 - requires elevation
__COMPAT_LAYER     -> started; elevated=False
```

### 3a. The `finally`-block trap (do not use the launch+restore wrapper to run)

There is a convenience script, `server/launch-client.ps1`, that rewrites
`Config.wtf`/glue, launches, waits, and **restores the official realm in a
`finally` block**. That `finally` blanks the glue ≈2 s after launch — which is
exactly what pointed the client back at the **live** server in early attempts.

- Use `launch-client.ps1 -NoLaunch` to *set* config + glue.
- Use `launch-client.ps1 -Restore` to put the official realm back by hand.
- **To actually play, launch `Ascension.exe` directly** with the two lines
  above. Never let the wrapper's launch+`finally` run while you want the client
  alive.

---

## 4. The auth handshake (port 3799) — `shim3799.py`

Ascension's login is **not** stock SRP6. It is a vanilla-looking SRP6 *envelope*
wrapped around a custom **641-byte `0x00` hello**. We reverse-engineered it from
known-plaintext captures (full field map in `docs/WIRE-SPEC.md`). The parts that
matter for a working local login:

- **Account field (offset `0x135`, 20 bytes)** = `account_name` (ASCII,
  NUL-padded) **XOR a fixed pad K**
  (`K = 28a668efc006e2b5ab81c32acb0842b237219774`). This is pure
  wire-obfuscation — no hash, no key derivation, no secret. One XOR recovers the
  account name, and one XOR writes it. **K is a published deobfuscation constant,
  not a credential.**
- **Password field** is XOR'd with a per-connection keystream we did not fully
  crack — and **did not need to.** The shim is *permissive*: it does not verify
  the password. Log in with account `test`, **any** password.
- **`M2` proof** = the shim replays `M2 = HMAC-SHA256(K, 'OK')`, where `K` is the
  session key it reads **live from the client's memory** at connect time (see
  §5). Because the shim finds the client PID dynamically, you can start the shim
  before the client.
- After the proof, the shim sends a **realm list** whose single realm points at
  `127.0.0.1:8087` — the world server (or `:8088` when the bridge is what is listening).

Success looks like this in `shim_log.txt`:

```
hello ... account='test' (OK)
M2 ACCEPTED
realm list sent (127.0.0.1:8087)
```

---

## 5. Getting into the world (port 8087) — `world_server.py`

Reaching character-select and then the world took solving several things that
each looked like a hang:

1. **World headers are PLAINTEXT, not RC4 — at first.** The initial
   `SMSG_AUTH_CHALLENGE` / `CMSG_AUTH_SESSION` exchange is unencrypted; header
   encryption turns on only afterward. Early attempts assumed RC4 from byte one
   and produced garbage. **Lesson: capture the raw client→server bytes before
   assuming crypt.**
2. **The world SessionKey is read from client memory.** `world_server.py`
   `ReadProcessMemory`s the 40-byte world session key out of the client
   (`authobj+0x140`) and uses it for the ARC4 header crypt once encryption is
   enabled. This is *why* the client must be unelevated (§3).
3. **Addon loadability gate.** Ascension's custom UI (the Character Advancement /
   Conquest-of-Azeroth panel and its friends) refuses to load its 36 addons
   until the server says the realm is a real Ascension realm. Fixed by sending
   the raw wire opcode **`SMSG_REALM_INFO` (0x9BC)** with a 68-byte body whose
   `+0x48` byte is non-zero → `RealmInfo[+0x48] = 1` → all 36/36 addons become
   loadable. Two more realm-flavour bytes (`IsLive`/`IsProduction`) and opcode
   **`0x725` (`SMSG_CA_ACTIVE_SPEC`)** were the difference between "the CoA tree
   shows 0 entries and crashes the client" and "153 populated buckets, 8873
   entries, 42 classes." See `docs/handoffs/HANDOFF-SPELLS-TALENTS-CLASSES.md`.
4. **The +31-second disconnect.** The client dropped ~31 s after entering the
   world. Cause: a **missed periodic time-sync**. Fix: re-send
   `SMSG_TIME_SYNC_REQ` every 10 s, piggybacked on the client ping. With that in
   place the client **stays** in the world (spawned on Sunstrider Isle).
5. **The level-10 UI wall.** The CoA panel greys its whole talent half behind
   *"Unlocks at level 10."* The dataset is not level-filtered — this is purely a
   UI gate — so the fix is server-side: report a real level. `ARCHIVE_LEVEL = 80`
   in `world_server.py`.

Success looks like: client reaches **character-select**, then **enters and stays
in the world**, and the only live IPs it ever touches are harmless Cloudflare CDN
pings to `api.ascension.gg`.

---

## 6. Full boot sequence (copy/paste)

Run all three **unelevated, same user**.

```bash
# Preflight: confirm the glue points at the shim (survives Config.wtf rewrites)
grep -n "ASCENSION_ARCHIVE_REALMLIST" \
  "C:/AzerothRealm/client-ascension/Interface/GlueXML/AccountLogin.lua"
# must read:  ASCENSION_ARCHIVE_REALMLIST = "127.0.0.1:3799";
```

```powershell
# 1) Auth shim  (127.0.0.1:3799)   — run in background
cd C:\AzerothRealm\realms\ascension; python shim3799.py TEST TEST

# 2) World server (127.0.0.1:8087) — run in background. Boot prints a resolved-path
#    report (every file it reads, ok/MISSING); fix any MISSING before launching.
cd C:\AzerothRealm\realms\ascension; python world_server.py

# 3) Client — launch DIRECTLY (not via the wrapper's launch+finally)
$env:__COMPAT_LAYER = 'RunAsInvoker'
Start-Process -FilePath 'C:\AzerothRealm\client-ascension\Ascension.exe' `
  -WorkingDirectory 'C:\AzerothRealm\client-ascension' -NoNewWindow -PassThru

# 4) Log in: account `test`, any password. Click Login, then Enter World.
```

**Verify local, not live:**

```bash
tail -6 shim_log.txt        # hello account='test' (OK) / M2 ACCEPTED / realm list sent (127.0.0.1:8087)
tail -4 "C:/AzerothRealm/client-ascension/Logs/connection.log"   # LOGIN_OK (not SERVER_DOWN / "information not valid")
```
```powershell
Get-NetTCPConnection -OwningProcess <clientpid> -State Established | ? RemotePort -in 3799,8085,8087,8088,3724
# want established 127.0.0.1:8087 (world; 8088 on the bridge route). 51.210.230.10 = LIVE = wrong.
```

---

## 7. One-paragraph summary (the thing to remember)

The launcher never sets the realm and there is nothing in it to hook, so the
client is redirected the way it redirects itself: a loose
`Interface\GlueXML\AccountLogin.lua` forces the `realmList` CVar to
**`127.0.0.1:3799`** in the last glue call before `ConnectToServer()` (Config.wtf
is cosmetic; the glue is the lever, and the port is mandatory — a bare
`127.0.0.1` dials 3724 and fails). The binary is launched **directly** with
`__COMPAT_LAYER=RunAsInvoker` so it runs **unelevated** (required, because the
servers `ReadProcessMemory` the session keys out of it) and without a UAC prompt.
A permissive Python **auth shim on 3799** answers the custom 641-byte SRP6-envelope
hello (account is an XOR-obfuscated field, password is not verified) and returns a
realm list pointing at **`127.0.0.1:8087`**, where a Python **world server** does
the auth-session, flips on ARC4 header crypt using the memory-read session key,
sends the realm-flavour + `SMSG_REALM_INFO(0x9BC)` bytes that make the custom
addons loadable, and keeps the client alive with a 10-second `SMSG_TIME_SYNC_REQ`.
Net result: the unmodified client logs in and stays in-world on a fully local
stack, touching the live service for nothing but CDN pings.

</details>
