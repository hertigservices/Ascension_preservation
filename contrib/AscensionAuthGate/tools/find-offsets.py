#!/usr/bin/env python3
"""AuthGate build detector / offset finder (read-only, fail-closed).

Determines whether a given Ascension client is AuthGate-compatible and, when the
executable has drifted from a known build, re-derives the one offset that can move
safely: the internal login(user,pass) RVA. Everything it cannot derive with high
confidence it leaves UNKNOWN, so setup falls back to the build-agnostic shim rather
than patching a guess.

What can and cannot be adapted, and why:
  * login_rva  (in Ascension.exe)   -- DERIVABLE by name. The client registers the
      Lua C-function `DefaultServerLogin`; it calls the internal login() whose prologue
      is 55 8B EC 80 3D. We find the binding by its name string, disassemble it, and take
      the call target whose prologue matches. Robust across exe rebuilds.
  * authobj_slot_rva + off_login_k (in the EXTENSION) -- NOT derivable here. The auth
      object and its reader live in the VMProtect-obfuscated extension; their addresses
      were found by static RE of one build and cannot be signature-scanned reliably. So
      they are trusted ONLY when the extension's SHA-256 matches a known profile. If the
      extension differs, this tool reports them UNKNOWN and the build is unsupported.

Emits a profile JSON on full success (exit 0), or a report of what was and wasn't found
(exit 2). Never writes to the client. Reuse: mirrors the PE-walk in tools/findfn.py.

Usage:
  python find-offsets.py --exe <Ascension.exe> --ext <Extensions(_orig).dll>
                         [--profiles <dir>] [--out <profile.json>]
"""
import argparse, hashlib, json, struct, sys, os

try:
    import capstone
except ImportError:
    sys.stderr.write("capstone required: python -m pip install capstone\n")
    sys.exit(3)

LOGIN_PROLOGUE = bytes([0x55, 0x8B, 0xEC, 0x80, 0x3D])   # push ebp; mov ebp,esp; cmp byte[..]


def load_pe(path):
    data = open(path, "rb").read()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("not a PE: %s" % path)
    nsec = struct.unpack_from("<H", data, pe + 6)[0]
    optsz = struct.unpack_from("<H", data, pe + 20)[0]
    magic = struct.unpack_from("<H", data, pe + 24)[0]
    if magic != 0x10B:
        raise ValueError("not 32-bit (PE32) : %s" % path)
    base = struct.unpack_from("<I", data, pe + 52)[0]
    secs, off = [], pe + 24 + optsz
    for i in range(nsec):
        s = data[off + i * 40: off + (i + 1) * 40]
        va, vsize = struct.unpack_from("<II", s, 12)[1], struct.unpack_from("<I", s, 8)[0]
        va = struct.unpack_from("<I", s, 12)[0]
        rawsz, rawptr = struct.unpack_from("<II", s, 16)
        secs.append((s[:8].rstrip(b"\0").decode("latin1"), va, vsize, rawptr, rawsz))
    return data, base, secs


def va2off(secs, base, va):
    r = va - base
    for _n, sva, vsize, rawptr, rawsz in secs:
        if sva <= r < sva + max(vsize, rawsz):
            return rawptr + (r - sva)
    return None


def text_bounds(secs, base):
    for n, sva, vsize, rawptr, rawsz in secs:
        if n == ".text":
            return base + sva, base + sva + max(vsize, rawsz)
    return base, base + len(open.__doc__ or "")


def find_binding_func(data, base, secs, name):
    """Return the VA of the C function registered under the Lua name `name`,
    via the luaL_Reg {name_ptr, func_ptr} adjacency. None if not found/ambiguous."""
    needle = name.encode("latin1") + b"\x00"
    str_vas, i = [], 0
    while True:
        i = data.find(needle, i)
        if i < 0:
            break
        if i == 0 or data[i - 1] in (0, 0xCC):
            va = None
            for _n, sva, vsize, rawptr, rawsz in secs:
                if rawptr <= i < rawptr + rawsz:
                    va = base + sva + (i - rawptr)
                    break
            if va is not None:
                str_vas.append(va)
        i += 1
    tlo, thi = text_bounds(secs, base)
    funcs = set()
    for sva in str_vas:
        needle_ptr = struct.pack("<I", sva)
        j = 0
        while True:
            j = data.find(needle_ptr, j)
            if j < 0:
                break
            # the dword AFTER the name pointer is the function pointer
            func = struct.unpack_from("<I", data, j + 4)[0]
            if tlo <= func < thi:
                funcs.add(func)
            j += 1
    return funcs


def derive_login_rva(data, base, secs):
    """Find DefaultServerLogin, disassemble it, and return (login_rva, note).
    login_rva is the call target whose prologue is 55 8B EC 80 3D."""
    funcs = find_binding_func(data, base, secs, "DefaultServerLogin")
    if not funcs:
        return None, "DefaultServerLogin binding not found (name/luaL_Reg layout changed)"
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = False
    candidates = set()
    for fva in funcs:
        foff = va2off(secs, base, fva)
        if foff is None:
            continue
        code = data[foff:foff + 512]
        for ins in md.disasm(code, fva):
            if ins.mnemonic == "call" and ins.op_str.startswith("0x"):
                try:
                    tgt = int(ins.op_str, 16)
                except ValueError:
                    continue
                toff = va2off(secs, base, tgt)
                if toff is not None and data[toff:toff + 5] == LOGIN_PROLOGUE:
                    candidates.add(tgt)
            if ins.mnemonic in ("ret", "retn"):
                break
    if len(candidates) == 1:
        va = candidates.pop()
        return va - base, "derived via DefaultServerLogin -> call (prologue verified)"
    if not candidates:
        return None, "no call to a 55 8B EC 80 3D prologue inside DefaultServerLogin"
    return None, "ambiguous: %d login-shaped call targets" % len(candidates)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def _int(v):
    """Accept an offset as an int or a '0x...'/'decimal' string."""
    if isinstance(v, int):
        return v
    return int(v, 16) if str(v).lower().startswith("0x") else int(v)


def load_profiles(d):
    out = []
    if d and os.path.isdir(d):
        for fn in os.listdir(d):
            if fn.lower().endswith(".json"):
                try:
                    out.append(json.load(open(os.path.join(d, fn), encoding="utf-8")))
                except Exception:
                    pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--ext", required=True)
    ap.add_argument("--profiles")
    ap.add_argument("--out")
    a = ap.parse_args()

    report = {"found": {}, "unknown": {}, "notes": []}
    exe_sha = sha256(a.exe)
    ext_sha = sha256(a.ext)
    report["exe_sha256"], report["ext_sha256"] = exe_sha, ext_sha

    profiles = load_profiles(a.profiles)
    exact = next((p for p in profiles if p.get("exe_sha256") == exe_sha
                  and p.get("ext_sha256") == ext_sha), None)
    ext_match = next((p for p in profiles if p.get("ext_sha256") == ext_sha), None)

    data, base, secs = load_pe(a.exe)

    # login_rva: prefer an exact-profile value, else derive from the exe by name.
    if exact:
        login_rva = _int(exact["login_rva"])
        report["notes"].append("exact known build: all offsets from profile")
    else:
        login_rva, note = derive_login_rva(data, base, secs)
        report["notes"].append("login_rva: " + note)

    # authobj_slot_rva + off_login_k: trusted ONLY from a matching extension hash.
    if ext_match:
        authobj_slot_rva = _int(ext_match["authobj_slot_rva"])
        off_login_k = _int(ext_match["off_login_k"])
        report["notes"].append("extension matches known profile: auth-object offsets trusted")
    else:
        authobj_slot_rva = off_login_k = None
        report["notes"].append(
            "extension SHA-256 not recognized: auth-object offsets UNKNOWN "
            "(cannot signature-scan the VMProtect'd extension) -> build unsupported")

    ok = login_rva is not None and authobj_slot_rva is not None and off_login_k is not None
    if login_rva is not None:
        report["found"]["login_rva"] = "0x%X" % login_rva
    else:
        report["unknown"]["login_rva"] = True
    if authobj_slot_rva is not None:
        report["found"]["authobj_slot_rva"] = "0x%X" % authobj_slot_rva
        report["found"]["off_login_k"] = "0x%X" % off_login_k
    else:
        report["unknown"]["authobj_slot_rva"] = True
        report["unknown"]["off_login_k"] = True

    if ok:
        profile = {
            "schema": 1,
            "exe_sha256": exe_sha,
            "ext_sha256": ext_sha,
            "login_rva": "0x%X" % login_rva,
            "authobj_slot_rva": "0x%X" % authobj_slot_rva,
            "off_login_k": "0x%X" % off_login_k,
            "source": "exact-profile" if exact else "derived",
        }
        js = json.dumps(profile, indent=2)
        if a.out:
            open(a.out, "w", encoding="utf-8").write(js + "\n")
        print("COMPATIBLE")
        print(js)
        return 0
    else:
        print("UNSUPPORTED")
        print(json.dumps(report, indent=2))
        return 2


if __name__ == "__main__":
    sys.exit(main())
