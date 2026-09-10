# Running the original client on Linux, and across a LAN

The default original-client path assumes one Windows box: client, authserver,
bridge and core all on `127.0.0.1`. Two deployments break that assumption, and
they are **independent** — you can want either without the other:

- **Linux host.** The client runs under Wine. Nothing about the client, its
  assets or its Lua changes; what changes is that Wine's loader and its `ws2_32`
  do not look like Windows' to the hooks AuthGate installs.
- **Separate machines.** The server sits on one box and the client on another,
  over a trusted LAN.

Both were contributed by **maribela**, who runs exactly that arrangement. This
page is the deployment guide; [KNOWN-ISSUES.md](KNOWN-ISSUES.md) §8 and §9 are
the symptom-first entries for when it goes wrong.

> **Verification boundary.** On the maintainer's Windows box the patched
> `proxy.c` builds warning-free with MSVC, `verify-build.py` passes, and all
> **59/59** protocol checks pass — including *"credential validation refuses
> non-loopback endpoints"*, which is what proves the loopback-only default
> survived. The Wine runtime path, the clang cross-build and multi-machine play
> were verified by the contributor on their own hardware, not here. No
> cross-compiler is installed on the maintainer's box.

---

## Linux under Wine

### What Wine breaks

`Extensions.dll` redirects the client's authentication by hooking `connect` and
`WSAConnect`. On Windows both hooks land. Under Wine, both of the original
mechanisms miss, for two unrelated reasons:

1. **The inline hook correctly refuses.** `install_inline_hook()` only patches a
   Microsoft hotpatch prologue (`8B FF 55 8B EC`). Wine's `ws2_32` is GCC-built
   (`55 89 E5 ...`), so the hook declines rather than corrupting it — the right
   call, but it leaves no redirect.
2. **The name-based import walk finds nothing.** `hook_iat()` matches by import
   name. Wine's loader presents every `ws2_32` `OriginalFirstThunk` entry with
   `IMAGE_ORDINAL_FLAG` set, even though the file's imports are by name, so the
   name comparison never matches.

The result is silent and total: no redirect is installed and the client
authenticates against **the real Ascension servers**. `proxy_auth.log` says so
in one line — see [KNOWN-ISSUES.md](KNOWN-ISSUES.md) §8.

### What the fix does

`hook_iat_addr()` matches the **resolved address** instead of the name: it
compares each `FirstThunk` entry against `GetProcAddress(ws2_32, "connect")`.
That works whether the import was recorded by name or by ordinal, and needs no
stolen prologue bytes. It runs only as a fallback, after the inline hook has
declined, so the proven Windows path is untouched.

`WSAConnect` is absent from some builds' imports; the startup line now reports
success on `connect` alone, which is the one that matters.

### Building the DLL on Linux

MSVC's `__try`/`__except` is not available to clang or GCC, so the two guarded
readers have a second implementation under `#else`: `kr_readable()` validates the
page with `VirtualQuery` (committed, not `PAGE_NOACCESS`/`PAGE_GUARD`, and large
enough for the read) before dereferencing. Same intent — a bad auth-object
pointer must never take the client down — reached by checking rather than
trapping. MSVC builds keep SEH exactly as reviewed.

```sh
clang --target=i686-w64-mingw32 -shared -O2 \
      -o Extensions.dll src/proxy.c \
      -luser32 -lws2_32 -lbcrypt
```

One consequence worth knowing: the `authsrv_thread` accept loop is
SEH-wrapped on MSVC and unwrapped on the cross-build. The reads that wrapping
guarded are page-validated above it, so the residual exposure is the socket path
itself.

`server/rpm_readk.py` also imports cleanly off Windows now — `ctypes.WinDLL`
does not exist there. The AuthGate route reads the login key in-process and
never takes the `ReadProcessMemory` path at all; if something does call it on
Linux it now raises `OSError` with a real message instead of failing at import
time. (That module belongs to the **legacy shim**, not to AuthGate.)

---

## Two machines on a LAN

### The two halves

`ASC_BRIDGE_HOST=0.0.0.0` on the server and `allow_remote_world = 1` on the
client are one feature in two places. Neither is useful alone: bind the bridge
without relaxing the client and the client dies on the endpoint check; relax the
client without binding the bridge and there is nothing to connect to.

**On the server box:**

```sh
ASC_BRIDGE_HOST=0.0.0.0 python server/ascension_bridge.py
```

`LISTEN_HOST` defaults to `127.0.0.1`, so an existing single-box install is
unaffected by the change. Serve the authserver on 3724 as usual.

**On the client box:** copy `contrib/AscensionAuthGate/authgate.cfg.example` to
`authgate.cfg` **beside `Extensions.dll`** and fill it in:

```ini
auth_ip = 192.168.1.50
realm   = 192.168.1.50:8088
allow_remote_world = 1
```

| Key | Effect | Default if absent |
|---|---|---|
| `auth_ip` | authserver the gate validates credentials against (port is always 3724), and the **one** non-loopback address the SRP6 gate will speak to | `127.0.0.1` |
| `realm` | address handed to the client in the realm list | `127.0.0.1:8088` |
| `allow_remote_world` | neutralise the client's world-endpoint allow-list | off |

Parsing rules that bite people: only the **first 511 bytes** of the file are
read, so keep keys at the top; everything after `=` is the value, so there are
**no inline comments**; an absent or unparsable key keeps the compiled default
rather than failing. Every accepted value is echoed to `proxy_auth.log` as a
`[cfg]` line, so the log tells you what was actually applied.

### Why `authgate.cfg` is not `authgate.profile`

They are deliberately separate files:

- **`authgate.profile`** holds *code offsets*. It is generated by
  `tools/find-offsets.py`, trusted only against a matching extension SHA-256,
  and **rewritten by the installer**.
- **`authgate.cfg`** holds *hand-edited deployment values* that must survive a
  reinstall.

`authgate.cfg` is gitignored (it may carry a LAN address); only the `.example`
ships.

### The world-endpoint allow-list

`Extensions.dll` builds the stored endpoint string and searches a **91-entry
allow-list** in `.rdata` whose only local entries are `127.0.0.1:8085`,
`:8087` and `:8088`. A LAN world address is not in it, the guard records "not
found", and the client dies with **ERROR #132** shortly after the world draws —
after a successful login, which is what makes it read like a world-server bug.

`patch_world_allowlist()` forces the guard result to zero, same length:

```
0F 94 45 BF   sete byte [ebp-0x41]      ->   C6 45 BF 00   mov byte [ebp-0x41], 0
```

Three properties keep this honest:

- It runs **only** when `allow_remote_world = 1`.
- The four original bytes are **verified before the write**, so a wrong offset
  logs a mismatch and patches nothing.
- The RVA is extension-relative, so it joins `authobj_slot_rva` under the same
  `ext_sha256` trust and is overridable as `allowlist_rva` in
  `authgate.profile`. It is reported in the `[startup] profile applied` line.

If you leave the allow-list alone, the `realm` value must be one of the three
loopback endpoints above — which is to say, single-box only.

---

## Security posture — read before you do this

The single-box design was reviewed as a **single trusted local machine** design.
Putting it on a LAN moves that boundary, and the patch narrows the move as far
as it can rather than opening it up:

- **The password never crosses the network.** The gate speaks SRP6; it sends a
  public value and a proof, and it verifies the server's `M2` before accepting.
- **The account name and the session are visible on the path.** Anyone on that
  network segment can see who logged in, and the world traffic that follows is
  not protected by this.
- **`auth_ip` permits exactly one additional address, not a range.** The default
  (`g_srp6_allowed_ip = 0`) is loopback-only and is covered by a test.
- Neutralising the allow-list means the client will accept **any** world
  endpoint it is handed, including one handed to it by something that is not
  your bridge.

Use it on a network you control. Do not expose the authserver or the bridge to
the internet.
