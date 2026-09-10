# AuthGate source security review — 2026-09-09

## Scope and conclusion

Reviewed the complete retained `proxy.c`, `srp6_client.h`, export definition and build commands, then rebuilt locally with MSVC and inspected the resulting PE. **No credential-exfiltration destination, remote-command facility, persistence mechanism, hidden process launcher or access to other processes was found in this retained source.** That is a scoped code-review finding, not proof that every upstream binary or the whole proprietary client is harmless.

FirstOni authored the original AuthGate. This reviewed adaptation credits that work while removing diagnostics and hardening behavior. Removed functionality is not included in the distributed source or binary.

## Intended sensitive behavior

The proxy chain-loads the user's genuine extension and patches imports/functions **inside its own client process**. A login-function hook necessarily sees the username/password already present in that process. It sends an SRP challenge/proof to **127.0.0.1:3724**, not a plaintext password. Its custom-auth responder binds only **127.0.0.1:3725**; its realm response advertises only the local bridge at **127.0.0.1:8088**. The former production IP in source is an exact redirect-match constant, not an outbound target used by the proxy's validation code.

The client's native custom protocol still requires a guarded read of its own in-process login key and an HMAC response. The bridge independently handles its world-key staging. Those keys are not logged, exported to disk, or passed to SQL by AuthGate. Cryptographic values still necessarily exist in live process memory.

The original game executable/extension may have their own networking: the original client was observed with external HTTP/HTTPS connections in addition to loopback gameplay. This review does not attribute their contents or certify those proprietary components. AuthGate is not a firewall for the original client.

## Findings fixed

- Removed packet/file/key capture, world-key export, disabled SQL containing a credential, send/recv interception, memory scanning and breakpoint experiments.
- A server's two-byte success status previously sufficed. Success now requires the complete stock response and matching **SHA1(A | M1 | K)** server proof; forged/truncated replies are rejected.
- Predictable tick-count RNG fallbacks were removed. Crypto/RNG failures fail closed; the SRP group and nonzero public value are checked.
- Credential inputs are bounded and overlong passwords are rejected, not silently truncated. ASCII usernames are normalized before SRP exchange. This client-facing implementation supports usernames and passwords up to 16 bytes.
- Partial reads/writes and the three-byte account-rejection response are handled; socket timeouts bound stalled exchanges.
- DLL file forwarding previously matched a broad suffix and could redirect writes. It now matches this proxy's full path and permits only read-only, existing-file opens; other clients and write/delete/create access are excluded.
- Module-derived paths are bounded before appending companion names. Sensitive authentication temporaries are cleared where practical.
- Gate state starts denied and remains denied after failures. The local listener requires exclusive ownership. Routine realm polling does not log.
- The launcher verifies the supported client and companion hashes, refuses another realm's expected authserver path, and checks configuration directory boundaries. The installer creates a new proxy file after renaming the genuine DLL, rather than modifying a possibly shared file record in place.

## Validation

59 isolated executable checks cover framing/malformed packets, denial persistence, key-read guards, quiet realm polls, CoA/Free-Pick metadata, a Python-derived independent SRP A/M1/M2 vector, forged/truncated peer replies, wrong groups/zero public keys, failed RNG, and file-open isolation. Two additional checks against the real local authserver verify wrong/correct credentials. The rebuilt DLL is PE32 x86 with one export and no removed capture markers or unnecessary debugging/process-injection imports. The original-client login/world path was verified with the old shim stopped; the user confirmed flawless gameplay.

The two Ascension profiles retained their own character DBs, world configs and bridge arguments; four unrelated hub profiles were compared unchanged. No other realm was started/stopped for this migration. Test-client deletion enumerated ordinary entries without following reparse points, unlinked its single shared Data junction, and verified original binary hashes and shared-data presence afterward.

## Remaining boundaries

This remains a **single trusted local machine** design **by default**, not a LAN/public authentication server. Local processes/users are not isolated from its loopback listener. The optional LAN mode added on 2026-09-10 (see below) does not change that conclusion; it lets an operator opt one deployment out of it deliberately. Fixed RVAs and hook prologues are specific to the supported client. The inherited original-DLL initialization timing under the Windows loader lock and executable hook trampolines were retained to preserve verified startup behavior. The closed-source genuine extension and the bridge are separate trust boundaries; neither receives a whole-program security certification from this review. An import scan alone cannot prove absence of malicious behavior.

Local captured-history removal does not establish erasure from remote retention, unavailable snapshots or physical storage remnants. Never commit real credentials, dumps, private runtime reports or client assets.

## Addendum — 2026-09-10: Linux/Wine and LAN contribution

Contributed by **maribela**; reviewed and rebuilt by the maintainer before inclusion. What it adds that is security-relevant, and what was checked:

- **A second in-process hook mechanism.** `hook_iat_addr()` patches an import thunk matched by resolved address rather than by name, because Wine presents `ws2_32` imports as ordinals. It runs only as a fallback after the reviewed inline hook declines, targets the same two functions, and needs no stolen prologue bytes. Scope is unchanged: this proxy's own process.
- **A four-byte code patch in the genuine extension.** `patch_world_allowlist()` forces the world-endpoint allow-list guard to zero so a non-loopback realm address is accepted. It applies **only** when `allow_remote_world = 1`, verifies the exact original bytes (`0F 94 45 BF`) before writing, and fails closed with a logged mismatch otherwise. Its RVA is extension-relative and therefore carries the same `ext_sha256` trust as the existing auth-object offsets, overridable as `allowlist_rva`. The consequence to understand is that a client with this applied will accept **any** world endpoint it is handed.
- **A relaxed credential-gate destination.** `g_srp6_allowed_ip` permits exactly one additional address — not a range, not a wildcard — and only when `auth_ip` names it. The compiled default is unchanged and loopback-only; the existing check *"credential validation refuses non-loopback endpoints"* still passes, which is the evidence that the default did not move. SRP6 still never transmits the password, but on a LAN the account name and the session are observable on the path, and the world traffic that follows is not protected by this package.
- **A configuration file read at load.** `authgate.cfg` is read once from beside the DLL, bounded to 511 bytes, into fixed buffers, with the module-path length guarded before the filename is appended (matching `load_profile`). Values are echoed to `proxy_auth.log`; nothing else is read from it and it is never written.
- **A non-SEH build path.** Under clang/GCC the guarded reads validate the page with `VirtualQuery` instead of catching the fault, and the `authsrv_thread` accept loop is not exception-wrapped. MSVC builds keep the reviewed SEH exactly. The cross-build was **not** compiled or inspected by the maintainer — no cross-compiler is installed here — so the non-MSVC branch carries the contributor's testing, not this review's.

Rebuilt with MSVC: no compiler warnings, `verify-build.py` PASS (PE32 x86, sole `ClientExtensionsDummy` export, no forbidden imports or capture markers; the one new import is `inet_addr`), and **59/59** protocol checks pass. Two defects found in review were fixed before the commit: an orphaned comment describing a session-key mechanism the code does not implement, and the missing `MAX_PATH` guard noted above.
