#!/usr/bin/env python3
"""Build a patched copy of the known stock WoW 3.3.5a (12340) executable.

The GlueXML and FrameXML callers still execute SSignature verification, preserving
its initialization work, but dispatch to their existing valid-result branches.
Do not stub the shared routine at 0x8165E0: that caused startup ERROR #132 at
0x426B06. The Blizzard addon caller and shared routine are left untouched.
The archive handle array is expanded from 64 to 128 entries; the original
starting archive priority of 64 is preserved. Large Address Aware is enabled
by default for Ascension's enlarged data files.

Usage: python patch_wow.py SOURCE [OUTPUT] [--no-sig] [--no-laa] [--no-archive-capacity] [--check-only]
SOURCE must match the recorded stock hash. OUTPUT must be a different file.
Writes use atomic replacement so an existing output hardlink is never modified
through to another client. --no-sig --no-archive-capacity produces the LAA-only
startup control. This is a bounded 128-entry fix, not an unlimited archive loader.
"""
import argparse
import hashlib
import os
import struct
import tempfile
from pathlib import Path

STOCK_SIZE = 7704216
STOCK_SHA256 = "aa63a5750d60ef16746c686b3d5e26876d98953eab08b1c026cd0faf78e88cb8"
SIG_VA = 0x008165E0
SIG_ORIG = bytes.fromhex("558BEC81EC1C010000")
# name, result dispatch VA, original complete instructions, table VA, valid target.
SIG_SITES = (
    ("GlueXML", 0x004DA7E5, bytes.fromhex("83F8037734FF2485B4A94D00"), 0x004DA9B4, 0x004DA835),
    ("FrameXML", 0x0052ABD9, bytes.fromhex("83F8037734FF2485B4AE5200"), 0x0052AEB4, 0x0052AC1C),
)


ARCHIVE_CAPACITY = 128
# 0x405DD0 initially reserves the global handle array, then appends patch/base
# handles before the late resize at 0x40624A. EBX initially doubles as reserve
# size and priority. This patch is validated for the current 75-MPQ candidate.
# Restore priority 64 before either branch, preserving TEST's flags with MOV.
# The 12-byte branch/alignment region ends immediately before the loop at 0x405E90.
ARCHIVE_SITES = (
    (0x00405E2E, bytes.fromhex("BB40000000"), bytes.fromhex("BB80000000")),
    (0x00405E84, bytes.fromhex("7654EB088DA4240000000090"), bytes.fromhex("BB40000000764FEB03909090")),
)


def sections(d):
    pe = struct.unpack_from("<I", d, 0x3C)[0]
    nsec = struct.unpack_from("<H", d, pe + 6)[0]
    optsz = struct.unpack_from("<H", d, pe + 20)[0]
    base = struct.unpack_from("<I", d, pe + 52)[0]
    off = pe + 24 + optsz
    secs = []
    for i in range(nsec):
        s = d[off + i * 40:off + (i + 1) * 40]
        vsize, va, rawsz, rawptr = struct.unpack_from("<IIII", s, 8)
        secs.append((base + va, vsize, rawptr, rawsz))
    return pe, secs


def va2off(secs, va):
    for sva, vsize, rawptr, rawsz in secs:
        if sva <= va < sva + min(vsize, rawsz):
            return rawptr + va - sva
    raise ValueError("VA 0x%08x has no file-backed section bytes" % va)


# Loose UI directories. At startup (called from 0x004DA7BD, right before the GlueXML
# signature dispatch) the routine at 0x005F4D90 walks {"Interface\GlueXML",
# "Interface\FrameXML"} and, whenever the directory exists on disk, renames it to
# "<dir>.old" (0x461B30 exists? -> "%s.old" -> 0x461D70 delete old -> 0x461C70 rename).
# That is what keeps a loose glue overlay from ever loading, independently of the
# signature verdict above. Turning the per-directory `je` (skip when absent) into an
# unconditional jump leaves both directories in place; the signature dispatch patch then
# accepts their files. Measured 2026-09-10: without this the client moved our
# Interface\GlueXML to GlueXML.old on the very next start.
LOOSE_UI_VA = 0x005F4DBF
LOOSE_UI_ORIG = b"\x74\x39"   # je 0x005F4DFA
LOOSE_UI_NEW = b"\xEB\x39"    # jmp 0x005F4DFA


def patch_bytes(data, *, signature=True, laa=True, archive_capacity=True, loose_ui=True):
    """Validate the exact input and return a new buffer; never change the source."""
    if len(data) != STOCK_SIZE or hashlib.sha256(data).hexdigest() != STOCK_SHA256:
        raise ValueError("source is not the recorded stock 12340 binary; refusing")
    pe, secs = sections(data)
    loose_off = va2off(secs, LOOSE_UI_VA)
    if data[loose_off:loose_off + len(LOOSE_UI_ORIG)] != LOOSE_UI_ORIG:
        raise ValueError("loose UI directory check does not have stock bytes")
    routine = va2off(secs, SIG_VA)
    if data[routine:routine + len(SIG_ORIG)] != SIG_ORIG:
        raise ValueError("shared signature routine does not have stock bytes")
    out = bytearray(data)
    for name, va, original, table, target in SIG_SITES:
        off = va2off(secs, va)
        if data[off:off + len(original)] != original:
            raise ValueError("%s result dispatch does not have stock bytes" % name)
        recorded_target = struct.unpack_from("<I", data, va2off(secs, table) + 3 * 4)[0]
        if recorded_target != target:
            raise ValueError("%s valid-result jump target differs" % name)
        if signature:
            branch = b"\xe9" + struct.pack("<i", target - (va + 5))
            out[off:off + len(original)] = branch + b"\x90" * (len(original) - len(branch))
    for va, original, replacement in ARCHIVE_SITES:
        off = va2off(secs, va)
        if data[off:off + len(original)] != original or len(original) != len(replacement):
            raise ValueError("archive loader does not have stock bytes at 0x%08X" % va)
        if archive_capacity:
            out[off:off + len(original)] = replacement
    if laa:
        flags = struct.unpack_from("<H", out, pe + 22)[0]
        struct.pack_into("<H", out, pe + 22, flags | 0x20)
    if loose_ui:
        out[loose_off:loose_off + len(LOOSE_UI_NEW)] = LOOSE_UI_NEW
    return bytes(out)


def write_output(src, dst, data):
    src, dst = Path(src), Path(dst)
    if src.resolve() == dst.resolve() or (dst.exists() and os.path.samefile(src, dst)):
        raise ValueError("output must not be the source or an alias of the source")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=dst.name + ".", suffix=".tmp", dir=dst.parent, delete=False) as f:
            temporary = f.name
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, dst)
        temporary = None
    finally:
        if temporary is not None:
            os.unlink(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("output", nargs="?")
    parser.add_argument("--no-sig", action="store_true", help="leave signature checks unchanged")
    parser.add_argument("--no-laa", action="store_true")
    parser.add_argument("--no-archive-capacity", action="store_true", help="leave the stock 64-entry handle array unchanged")
    parser.add_argument("--no-loose-ui", action="store_true", help="keep renaming loose Interface\\GlueXML / FrameXML to .old at startup")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        data = Path(args.source).read_bytes()
        print("input: %s (%d bytes, sha256 %s)" % (args.source, len(data), hashlib.sha256(data).hexdigest()))
        out = patch_bytes(data, signature=not args.no_sig, laa=not args.no_laa, archive_capacity=not args.no_archive_capacity,
                          loose_ui=not args.no_loose_ui)
        for name, va, original, table, target in SIG_SITES:
            print("%s: verified dispatch 0x%08X -> valid target 0x%08X" % (name, va, target))
        print("shared verification routine and Blizzard addon caller: unchanged")
        if args.check_only or not args.output:
            return 0
        write_output(args.source, args.output, out)
        print("output: %s (sha256 %s)" % (args.output, hashlib.sha256(out).hexdigest()))
        print("caller dispatch patch=%s; LAA=%s; archive capacity=%d; loose UI directories kept=%s" % (
            not args.no_sig, not args.no_laa, 64 if args.no_archive_capacity else ARCHIVE_CAPACITY, not args.no_loose_ui))
        return 0
    except (OSError, ValueError, struct.error) as ex:
        print("!! %s" % ex)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
