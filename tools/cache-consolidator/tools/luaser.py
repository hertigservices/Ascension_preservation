"""Parser and writer for WoW SavedVariables (.lua) files.

These files are not hand-written Lua -- they are emitted by the client's own
serializer, so the grammar is tiny and completely regular:

    NAME = {
        ["key"] = value,
        [123] = value,
        value, -- [1]
    }

and `value` is a string, a number, true/false/nil, or another table. That means a
real parser is only ~100 lines and is far safer than regex-scraping: we have to
*understand* the tree to know which branches are server data and which are the
submitter's character config, and a regex cannot tell those apart.

Nothing here executes Lua. The input is untrusted (it arrives from strangers),
so it is only ever tokenised.

Writing is deliberately not a faithful echo of the input: keys are emitted in
sorted order so that merging the same data twice produces byte-identical output
and git sees no diff. The client does not care about key order.
"""
import re


class LuaError(ValueError):
    pass


class Table:
    """A Lua table: an ordered array part plus keyed entries.

    Kept separate because the client's serializer writes array items as bare
    values (`value, -- [1]`) and turning those into keys 1..n would change the
    file's shape on the way back out.
    """

    __slots__ = ("array", "hash")

    def __init__(self):
        self.array = []   # list of values
        self.hash = {}    # key (int|float|str) -> value

    def __repr__(self):
        return f"<Table array={len(self.array)} hash={len(self.hash)}>"

    def get(self, k, default=None):
        return self.hash.get(k, default)

    def __contains__(self, k):
        return k in self.hash

    def __len__(self):
        return len(self.array) + len(self.hash)


# ---------------------------------------------------------------- tokenising

# Order matters: longer literals first, and the comment rule must beat `-`.
_TOKEN = re.compile(r"""
      (?P<ws>\s+)
    | (?P<comment>--\[\[.*?\]\]|--[^\n]*)
    | (?P<str>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')
    | (?P<num>-?(?:0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?))
    | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
    | (?P<punct>[\[\]{}=,;])
""", re.VERBOSE | re.DOTALL)

# WoW writes \ddd decimal escapes for bytes outside ASCII, plus the usual set.
_ESCAPES = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r",
            "t": "\t", "v": "\v", "\\": "\\", '"': '"', "'": "'", "\n": "\n"}


def _unescape(raw):
    """Decode a quoted Lua string literal body (quotes already stripped)."""
    out, i, n = bytearray(), 0, len(raw)
    while i < n:
        c = raw[i]
        if c != "\\":
            out.extend(c.encode("utf-8"))
            i += 1
            continue
        i += 1
        if i >= n:
            break
        e = raw[i]
        if e.isdigit():                       # \ddd, up to three digits
            j = i
            while j < n and j - i < 3 and raw[j].isdigit():
                j += 1
            value = int(raw[i:j])
            if value > 255:
                raise LuaError("decimal escape is not a byte")
            out.append(value)
            i = j
        elif e == "x":          # \xHH
            if i + 2 >= n or not re.fullmatch(r"[0-9a-fA-F]{2}", raw[i + 1:i + 3]):
                raise LuaError("invalid hex byte escape")
            out.append(int(raw[i + 1:i + 3], 16))
            i += 3
        else:
            out.extend(_ESCAPES.get(e, e).encode("utf-8"))
            i += 1
    try:
        return out.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LuaError("string contains non-UTF-8 bytes; retained for review") from exc


def _tokens(src):
    pos, n = 0, len(src)
    while pos < n:
        m = _TOKEN.match(src, pos)
        if not m:
            ctx = src[pos:pos + 40].replace("\n", "\\n")
            raise LuaError(f"unexpected input at offset {pos}: {ctx!r}")
        pos = m.end()
        kind = m.lastgroup
        if kind in ("ws", "comment"):
            continue
        yield kind, m.group()
    yield "eof", ""


class Redacted(str):
    """A placeholder a submitter left behind when they scrubbed a file by hand.

    `{redacted}` is not valid Lua, so a strict parser rejects the whole file --
    and the files people redact are exactly the ones they were careful with, so
    refusing them is the wrong failure. It is kept as a distinct type rather
    than an empty string so a caller can never mistake it for real data.
    """
    __slots__ = ()

    def __repr__(self):
        return "<redacted>"


class _Parser:
    def __init__(self, src):
        self._it = _tokens(src)
        # Both must tolerate running out immediately: an empty file yields only
        # the "eof" token, and priming a two-token buffer from it would raise.
        self._tok = next(self._it, ("eof", ""))
        self._ahead = next(self._it, ("eof", ""))

    def _next(self):
        t = self._tok
        # The stream yields one "eof"; with a lookahead buffer we can be asked
        # for the token after it, so eof has to repeat rather than raise.
        self._tok, self._ahead = self._ahead, next(self._it, ("eof", ""))
        return t

    def _expect(self, kind, text=None):
        k, v = self._next()
        if k != kind or (text is not None and v != text):
            raise LuaError(f"expected {text or kind}, got {v!r}")
        return v

    def value(self):
        kind, text = self._tok
        if kind == "punct" and text == "{":
            return self.table()
        self._next()
        if kind == "str":
            return _unescape(text[1:-1])
        if kind == "num":
            if text.lower().startswith(("0x", "-0x")):
                return int(text, 16)
            if any(c in text for c in ".eE"):
                return float(text)
            return int(text)
        if kind == "name":
            if text == "true":
                return True
            if text == "false":
                return False
            if text == "nil":
                return None
            if text.lower() in ("redacted", "scrubbed", "removed"):
                return Redacted(text.lower())
            raise LuaError(f"bare identifier {text!r} is not a value")
        raise LuaError(f"not a value: {text!r}")

    def table(self):
        self._expect("punct", "{")
        t = Table()
        while True:
            kind, text = self._tok
            if kind == "punct" and text == "}":
                self._next()
                return t
            if kind == "punct" and text == "[":
                self._next()
                key = self.value()
                self._expect("punct", "]")
                self._expect("punct", "=")
                t.hash[key] = self.value()
            elif kind == "name" and self._peek_is_assign():
                key = self._next()[1]
                self._expect("punct", "=")
                t.hash[key] = self.value()
            else:
                t.array.append(self.value())
            k, v = self._tok
            if k == "punct" and v in (",", ";"):
                self._next()

    def _peek_is_assign(self):
        """True when the identifier under the cursor is a key (`name = v`).

        Without this lookahead a bare identifier -- which the client never
        writes, but a hand-redacted file does -- is read as a key, and the
        parser then fails on the `}` where it wanted `=`.
        """
        return self._ahead[0] == "punct" and self._ahead[1] == "="


def loads(src):
    """Parse a whole SavedVariables file -> {global_name: value}."""
    p = _Parser(src)
    out = {}
    while p._tok[0] != "eof":
        kind, text = p._tok
        if kind == "punct" and text == ";":
            p._next()
            continue
        name = p._expect("name")
        p._expect("punct", "=")
        out[name] = p.value()
    return out


def load(path):
    with open(path, encoding="utf-8", errors="strict") as f:
        return loads(f.read())


# ------------------------------------------------------------------- writing

_SAFE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _q(s):
    out = ['"']
    for ch in s:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ord(ch) < 32:
            out.append("\\%03d" % ord(ch))
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _num(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    # repr keeps full float precision, which matters: these are measured values
    # and rounding them would silently change the data.
    return repr(v)


def _key_order(k):
    """Numbers before strings, each sorted within itself -- a total order, so
    output is deterministic no matter what order the merge produced."""
    return (0, k, "") if isinstance(k, (int, float)) and not isinstance(k, bool) \
        else (1, 0, str(k))


def _write(v, out, depth):
    pad = "\t" * depth
    if isinstance(v, Table):
        if not len(v):
            out.append("{\n" + pad + "}")
            return
        out.append("{\n")
        inner = "\t" * (depth + 1)
        for i, item in enumerate(v.array, 1):
            out.append(inner)
            _write(item, out, depth + 1)
            out.append(f", -- [{i}]\n")
        for k in sorted(v.hash, key=_key_order):
            out.append(inner + "[")
            out.append(_q(k) if isinstance(k, str) else _num(k))
            out.append("] = ")
            _write(v.hash[k], out, depth + 1)
            out.append(",\n")
        out.append(pad + "}")
    elif isinstance(v, Redacted):
        out.append("{" + str(v) + "}")
    elif isinstance(v, str):
        out.append(_q(v))
    elif v is None:
        out.append("nil")
    else:
        out.append(_num(v))


def dumps(globals_dict):
    """Render {global_name: value} back into SavedVariables text.

    Globals and keys come out sorted, so re-merging unchanged data yields a
    byte-identical file.
    """
    out = []
    for name in sorted(globals_dict):
        out.append(name + " = ")
        _write(globals_dict[name], out, 0)
        out.append("\n")
    return "".join(out)


def dump(globals_dict, path):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(globals_dict))


# -------------------------------------------------------------------- helpers

def to_py(v):
    """Plain-Python view of a parsed tree: dicts, lists and scalars.

    A table with only an array part becomes a list; otherwise it becomes a dict
    with the array items folded in under keys 1..n. That is Lua's own view of a
    table, and it is the shape the database-ingest path consumes.

    The structural `Table` is what the *writer* needs (it has to know which
    entries were bare array values to put them back that way), so callers that
    republish should stay on `load`/`loads` and only reach for this when they
    want data rather than a document.
    """
    if isinstance(v, Table):
        if v.array and not v.hash:
            return [to_py(x) for x in v.array]
        out = {k: to_py(x) for k, x in v.hash.items()}
        for i, x in enumerate(v.array, 1):
            out.setdefault(i, to_py(x))
        return out
    return v


# Names kept from the parser this one replaced, so its callers did not have to
# change shape when the two were folded together.
LuaSVError = LuaError


def parse(text):
    """-> {globalName: plain python} for every top-level assignment."""
    return {k: to_py(v) for k, v in loads(text).items()}


def parse_file(path):
    return {k: to_py(v) for k, v in load(path).items()}


def walk(v, path=()):
    """Yield (path, value) for every leaf, for auditing what a tree contains."""
    if isinstance(v, Table):
        for i, item in enumerate(v.array, 1):
            yield from walk(item, path + (i,))
        for k in sorted(v.hash, key=_key_order):
            yield from walk(v.hash[k], path + (k,))
    else:
        yield path, v


def table_from(mapping=None, array=None):
    t = Table()
    if mapping:
        t.hash.update(mapping)
    if array:
        t.array.extend(array)
    return t
