"""Write worldmaparea_bounds.tsv from a client's WorldMapArea.dbc and AreaTable.dbc.

    python -B tools/make_worldmaparea_bounds.py WorldMapArea.dbc AreaTable.dbc

ingest_lootcollector.py uses these bounds to turn a LootCollector pin (a 0..1
position inside one WorldMapArea) into server coordinates. They must be the
bounds the client projected with. That means Ascension's own table, which adds
zones stock lacks and moves stock's bounds, so take both files out of the
Ascension client's MPQs (see docs/WORLDFORGED.md). Never use a stock client or a
server's dbc folder: the latter can be an older extraction. The copy under
server-ascension held 285 rows where the client's holds 310.
"""
import hashlib, io, os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "worldmaparea_bounds.tsv")
WMA_SHAPE = (11, 44)      # 3.3.5a: fields, bytes per record
AREA_SHAPE = (36, 144)
AREA_NAME = 11            # the enUS name field of AreaTable


def read_dbc(path, shape):
    """(records, text, md5) for a WDBC file, refusing any layout but `shape`."""
    with open(path, "rb") as f:
        b = f.read()
    magic, n, fields, size, strsize = struct.unpack("<4s4I", b[:20])
    if magic != b"WDBC" or (fields, size) != shape:
        raise ValueError(f"{os.path.basename(path)}: expected WDBC with {shape[0]} fields of "
                         f"{shape[1]} bytes, found {magic!r} {fields} fields of {size}")
    body = b[20:20 + n * size]
    strings = b[20 + n * size:20 + n * size + strsize]
    if len(body) != n * size or len(strings) != strsize:
        raise ValueError(f"{os.path.basename(path)}: truncated")

    def text(offset):
        return strings[offset:strings.index(b"\0", offset)].decode("utf-8")
    return [body[i * size:(i + 1) * size] for i in range(n)], text, hashlib.md5(b).hexdigest()


def rows(wma_path, area_path):
    wrecs, wtext, wmd5 = read_dbc(wma_path, WMA_SHAPE)
    arecs, atext, amd5 = read_dbc(area_path, AREA_SHAPE)
    zone = {}
    for r in arecs:
        v = struct.unpack("<36I", r)
        zone[v[0]] = atext(v[AREA_NAME])
    out = []
    for r in wrecs:
        wid, mp, aid, name = struct.unpack("<4I", r[:16])
        left, right, top, bottom = struct.unpack("<4f", r[16:32])
        out.append((wid, mp, aid, zone.get(aid, ""), wtext(name), left, right, top, bottom))
    for row in out:
        if any(ch in str(v) for v in row for ch in "\t\r\n"):
            raise ValueError(f"WorldMapArea {row[0]}: a name would break its row")
    return sorted(out), (wmd5, len(wrecs), amd5, len(arecs))


def num(v):
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s == "-0" else s


def write(out, meta, path=OUT):
    wmd5, wn, amd5, an = meta
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("# WorldMapArea bounds with each area's zone name, used to turn LootCollector\n")
        f.write("# map positions into server coordinates.\n")
        f.write("# Written by make_worldmaparea_bounds.py; do not edit by hand, regenerate.\n")
        f.write(f"# WorldMapArea.dbc md5 {wmd5} ({wn} rows); AreaTable.dbc md5 {amd5} ({an} rows)\n")
        f.write("wma_id\tmap\tarea_id\tzone\twma_name\tleft\tright\ttop\tbottom\n")
        for wid, mp, aid, zone, name, left, right, top, bottom in out:
            f.write("\t".join([str(wid), str(mp), str(aid), zone, name,
                               num(left), num(right), num(top), num(bottom)]) + "\n")


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    out, meta = rows(argv[1], argv[2])
    write(out, meta)
    check = next((r for r in out if r[0] == 4), None)
    print(f"wrote {len(out)} rows -> {os.path.basename(OUT)}; WorldMapArea md5 {meta[0]}")
    if check:
        print(f"  check: WorldMapArea 4 = {check[4]} in {check[3]!r}, map {check[1]}, "
              f"top {num(check[7])} bottom {num(check[8])} left {num(check[5])} right {num(check[6])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
