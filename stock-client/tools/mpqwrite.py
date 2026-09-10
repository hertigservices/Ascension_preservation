#!/usr/bin/env python3
"""Minimal MPQ (format 0, WoW 3.3.5a-compatible) archive WRITER.

Stores every file as an unencrypted, uncompressed single unit, so the client's
loader does exactly what it does for stock archives with no compression at all.
Hash and block tables are encrypted with the standard keys.  A "(listfile)" is
added so mpqfind-style tools can enumerate the archive.

    python mpqwrite.py <out.MPQ> <internal path>=<file> [<internal path>=<file> ...]
    python mpqwrite.py <out.MPQ> --from-dir <dir> [--prefix DBFilesClient\\]

Read-only with respect to inputs.  Verified by mpqprobe.py (membership) and
mpqfind.exe --from (extraction) after writing.
"""
import os, struct, sys, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("mpqprobe", os.path.join(HERE, "mpqprobe.py"))
mp = importlib.util.module_from_spec(spec); spec.loader.exec_module(mp)

MPQ_FILE_EXISTS = 0x80000000
MPQ_FILE_SINGLE_UNIT = 0x01000000
HEADER_SIZE = 0x20
BLOCK_SHIFT = 3          # sector size 512 << 3 = 4096 (unused for single-unit files, but must be sane)


def encrypt(data, key):
    tbl = mp.crypt_table()
    seed = 0xEEEEEEEE
    n = len(data) // 4
    vals = struct.unpack_from("<%dI" % n, data)
    out = []
    for v in vals:
        seed = (seed + tbl[0x400 + (key & 0xFF)]) & 0xFFFFFFFF
        ch = v ^ ((key + seed) & 0xFFFFFFFF)
        key = (((~key << 0x15) + 0x11111111) | (key >> 0x0B)) & 0xFFFFFFFF
        seed = (v + seed + (seed << 5) + 3) & 0xFFFFFFFF
        out.append(ch & 0xFFFFFFFF)
    return struct.pack("<%dI" % n, *out)


def build(files):
    """files: list of (internal_name, bytes). Returns the archive bytes."""
    names = [n.replace("/", "\\") for n, _ in files]
    listfile = ("\r\n".join(names + ["(listfile)"]) + "\r\n").encode("latin1")
    entries = list(zip(names, [d for _, d in files])) + [("(listfile)", listfile)]
    count = len(entries)
    ht_size = 16
    while ht_size < count * 2:
        ht_size *= 2
    # file data follows the header
    pos = HEADER_SIZE
    block = []
    body = bytearray()
    for name, data in entries:
        block.append((pos, len(data), len(data), MPQ_FILE_EXISTS | MPQ_FILE_SINGLE_UNIT))
        body += data
        pos += len(data)
    # hash table
    ht = [(0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF)] * ht_size
    for i, (name, _) in enumerate(entries):
        h0 = mp.hash_string(name, 0) & (ht_size - 1)
        ha = mp.hash_string(name, 1)
        hb = mp.hash_string(name, 2)
        j = h0
        while ht[j][3] != 0xFFFFFFFF:
            j = (j + 1) & (ht_size - 1)
        ht[j] = (ha, hb, 0, i)   # locale 0 (neutral), platform 0 packed in the third dword
    ht_raw = b"".join(struct.pack("<IIHHI", a, b, loc & 0xFFFF, 0, idx) for a, b, loc, idx in ht)
    bt_raw = b"".join(struct.pack("<IIII", *e) for e in block)
    ht_off = pos
    bt_off = ht_off + len(ht_raw)
    archive_size = bt_off + len(bt_raw)
    header = struct.pack("<4sIIHHIIII", b"MPQ\x1a", HEADER_SIZE, archive_size, 0, BLOCK_SHIFT,
                         ht_off, bt_off, ht_size, len(block))
    return header + bytes(body) + encrypt(ht_raw, mp.hash_string("(hash table)", 3)) + \
        encrypt(bt_raw, mp.hash_string("(block table)", 3))


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    out = sys.argv[1]
    files = []
    if sys.argv[2] == "--from-dir":
        d = sys.argv[3]
        prefix = sys.argv[sys.argv.index("--prefix") + 1] if "--prefix" in sys.argv else ""
        for root, _, fs in os.walk(d):
            for f in sorted(fs):
                p = os.path.join(root, f)
                rel = os.path.relpath(p, d).replace("/", "\\")
                files.append((prefix + rel, open(p, "rb").read()))
    else:
        for spec_ in sys.argv[2:]:
            internal, path = spec_.split("=", 1)
            files.append((internal, open(path, "rb").read()))
    data = build(files)
    with open(out, "wb") as f:
        f.write(data)
    print("wrote %s: %d files, %d bytes" % (out, len(files), len(data)))
    a = mp.Archive(out)
    for name, _ in files:
        print("  %-40s %s" % (name, "OK" if a.has(name) else "MISSING"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
