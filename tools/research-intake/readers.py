"""Bounded readers. Inputs are bytes, never executable SQL/Lua/HTML."""
import base64
import csv
import gzip
import html.parser
import io
import json
import re
import sqlite3
import struct
from pathlib import Path

VERSION = '1'
MAX_RECORD = 8 * 1024 * 1024


class Held(ValueError):
    pass


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def bounded_lines(f):
    while True:
        line = f.readline(MAX_RECORD + 1)
        if len(line) > MAX_RECORD:
            raise Held('Record exceeds 8 MiB; original retained, needs a specialized reader')
        if not line:
            return
        yield line


def object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Held('Duplicate JSON object key; original retained without overwriting fields')
        result[key] = value
    return result


def json_load(s):
    return json.loads(s, object_pairs_hook=object_pairs,
                      parse_constant=lambda x: (_ for _ in ()).throw(Held('Nonfinite JSON number')))


def json_array(f):
    """Incremental top-level arrays; any other JSON value has a bounded size."""
    decoder = json.JSONDecoder(object_pairs_hook=object_pairs,
                               parse_constant=lambda x: (_ for _ in ()).throw(Held('Nonfinite JSON')))
    buf = ''; eof = False
    def more():
        nonlocal buf, eof
        part = f.read(65536); eof = not part; buf += part
        if len(buf) > MAX_RECORD:
            raise Held('JSON value exceeds reader limit; retain for specialized reader')
    while not buf.strip() and not eof:
        more()
    buf = buf.lstrip('\ufeff \r\n\t')
    if not buf.startswith('['):
        while not eof:
            more()
        yield json_load(buf)
        return
    buf = buf[1:]; first = True
    while True:
        buf = buf.lstrip()
        while not buf and not eof:
            more(); buf = buf.lstrip()
        if buf.startswith(']'):
            buf = buf[1:]
            while not eof:
                more()
            if buf.strip(): raise Held('Trailing JSON data')
            return
        if not first:
            if not buf.startswith(','): raise Held('Missing JSON array separator')
            buf = buf[1:].lstrip()
            while not buf and not eof:
                more(); buf = buf.lstrip()
            if buf.startswith(']'): raise Held('Trailing JSON comma')
        while True:
            try:
                value, end = decoder.raw_decode(buf)
                # A number may end at the read boundary; fetch before accepting it.
                if end == len(buf) and not eof:
                    more(); continue
                break
            except json.JSONDecodeError:
                if eof: raise Held('Incomplete or invalid JSON')
                more()
        yield value
        buf = buf[end:]; first = False


class Page(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0; self.title = False; self.titles = []; self.text = []; self.links = []
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'): self.hidden += 1
        if tag == 'title': self.title = True
        if tag == 'a':
            href = dict(attrs).get('href', '')
            if href: self.links.append(href)
    def handle_endtag(self, tag):
        if tag in ('script', 'style'): self.hidden = max(0, self.hidden - 1)
        if tag == 'title': self.title = False
    def handle_data(self, data):
        if not self.hidden and data.strip():
            self.text.append(data.strip())
            if self.title: self.titles.append(data.strip())


def pg_value(text):
    if text == r'\N': return None
    def repl(m):
        s = m.group(1)
        return chr(int(s, 8)) if s[0].isdigit() else {'b':'\b','f':'\f','n':'\n','r':'\r','t':'\t','v':'\v','\\':'\\'}.get(s,s)
    return re.sub(r'\\([0-7]{1,3}|.)', repl, text)


def mysql_values(text):
    """Parse only literal INSERT values; never send donation SQL to a database."""
    i = 0
    def ws():
        nonlocal i
        while i < len(text) and text[i].isspace(): i += 1
    while True:
        ws()
        if i >= len(text) or text[i] != '(': raise Held('Unsupported SQL values')
        i += 1; row = []
        while True:
            ws()
            if i >= len(text): raise Held('Incomplete SQL literal')
            if text[i] == "'":
                i += 1; value = []; closed = False
                while i < len(text):
                    c = text[i]; i += 1
                    if c == "'":
                        if i < len(text) and text[i] == "'": value.append("'"); i += 1
                        else: closed = True; break
                    elif c == '\\':
                        if i >= len(text): raise Held('Incomplete SQL escape')
                        c = text[i]; i += 1
                        value.append({'0':'\0','n':'\n','r':'\r','t':'\t','b':'\b','Z':'\x1a'}.get(c,c))
                    else: value.append(c)
                if not closed: raise Held('Unclosed SQL string')
                row.append(''.join(value))
            else:
                start = i
                while i < len(text) and text[i] not in ',)': i += 1
                value = text[start:i].strip()
                if value.upper() == 'NULL': row.append(None)
                elif re.fullmatch(r'[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?', value): row.append(value)
                elif re.fullmatch(r'0x[0-9A-Fa-f]*', value): row.append({'sql_hex':value[2:]})
                else: raise Held('Nonliteral SQL value retained without evaluation')
            ws()
            if i >= len(text): raise Held('Incomplete SQL row')
            c = text[i]; i += 1
            if c == ')': break
            if c != ',': raise Held('Invalid SQL separator')
        yield row
        ws()
        if i < len(text) and text[i] == ',': i += 1; continue
        if text[i:].strip() != ';': raise Held('Unsupported SQL suffix')
        return


def read_sql(f,tick=lambda:None):
    copy = None; columns = None; found = False
    for line in bounded_lines(f):
        tick(); s = line.strip()
        if copy:
            if s == r'\.': copy = None; continue
            values = [pg_value(x) for x in line.rstrip('\r\n').split('\t')]
            if len(values) != len(columns): raise Held('COPY column mismatch')
            yield copy, dict(zip(columns,values)); found = True; continue
        match = re.match(r'^COPY\s+([\w."-]+)\s*\(([^)]+)\)\s+FROM stdin;$',s,re.I)
        if match:
            copy = match[1].replace('"',''); columns = [x.strip().strip('"') for x in match[2].split(',')]
            if len(set(columns)) != len(columns): raise Held('Duplicate SQL columns')
            continue
        if re.match(r'^INSERT\s', s, re.I):
            # Standard mysqldump uses single-line extended INSERTs. Larger/nonstandard
            # statements are held in full, never partially promoted.
            m = re.match(r'^INSERT\s+INTO\s+([`"\w.]+)\s*(?:\(([^)]+)\))?\s+VALUES\s*(.*)$',s,re.I)
            if not m: raise Held('SQL INSERT dialect needs a specialized reader')
            table = m[1].replace('`','').replace('"','')
            cols = [c.strip().strip('`"') for c in m[2].split(',')] if m[2] else None
            if cols and len(set(cols)) != len(cols): raise Held('Duplicate SQL columns')
            for values in mysql_values(m[3]):
                if cols and len(cols) != len(values): raise Held('INSERT column mismatch')
                yield table, dict(zip(cols,values)) if cols else {'values':values,'columns':'unspecified'}
                found = True
    if copy: raise Held('Truncated PostgreSQL COPY')
    if not found: raise Held('No supported SQL data section; original retained (DDL is never executed)')


def read_sqlite(path):
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        db.execute('PRAGMA trusted_schema=OFF'); db.execute('PRAGMA query_only=ON')
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_RECORD)
        # Views/triggers/functions from the donation are never executed.
        names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND sql NOT LIKE '%VIRTUAL TABLE%'")]
        db.set_authorizer(lambda action,*args: sqlite3.SQLITE_OK if action in (sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ) else sqlite3.SQLITE_DENY)
        for name in names:
            cursor = db.execute('SELECT * FROM "' + name.replace('"','""') + '"')
            cols = [x[0] for x in cursor.description]
            for values in cursor:
                yield name, {k: {'base64':base64.b64encode(v).decode()} if isinstance(v,bytes) else v for k,v in zip(cols,values)}
    finally: db.close()


def read_records(path, name, tick=lambda:None):
    low = name.lower(); suffix = Path(low).suffix
    with path.open('rb') as f: magic = f.read(20)
    if magic.startswith(b'SQLite format 3\0'):
        if magic[18:20] != b'\x01\x01':
            raise Held('SQLite WAL-mode input requires a verified consistent single-file backup in DELETE journal mode')
        for row in read_sqlite(path): tick(); yield row
        return
    if suffix == '.loc':
        with path.open('rb') as f:
            head=f.read(4)
            if len(head)!=4: raise Held('Truncated localisation header')
            for _ in range(struct.unpack('<I',head)[0]):
                tick(); head=f.read(8)
                if len(head)!=8: raise Held('Truncated localisation record')
                identifier,size=struct.unpack('<II',head)
                if size>MAX_RECORD: raise Held('Oversized localisation value')
                raw=f.read(size)
                if len(raw)!=size: raise Held('Truncated localisation value')
                yield 'localisation', {'id':str(identifier),'text':raw.decode('utf-8')}
            if f.read(1): raise Held('Trailing localisation bytes')
        return
    if magic[:4] == b'WDBC':
        count,fields,size,strings=struct.unpack('<4I',magic[4:])
        if size != fields*4 or size>MAX_RECORD or 20+count*size+strings != path.stat().st_size:
            raise Held('Invalid DBC layout')
        with path.open('rb') as f:
            f.seek(20)
            for index in range(count):
                tick(); row=f.read(size)
                yield 'dbc-raw',{'row':index,'u32':list(struct.unpack('<'+'I'*fields,row)), 'schema':'uninterpreted; values may be floats or string offsets'}
            offset=0
            while offset<strings:
                tick(); raw=bytearray()
                while offset+len(raw)<strings:
                    c=f.read(1); raw.extend(c)
                    if c==b'\0': break
                    if len(raw)>MAX_RECORD: raise Held('Oversized DBC string')
                yield 'dbc-strings',{'offset':offset,'base64':base64.b64encode(raw).decode()}
                offset+=len(raw)
        return
    if suffix not in ('.json','.jsonl','.ndjson','.csv','.tsv','.sql','.html','.htm','.log','.txt'):
        raise Held('No reader for this format; original retained for future interpretation')
    with path.open('r',encoding='utf-8-sig',newline='') as f:
        if suffix in ('.jsonl','.ndjson'):
            for line in bounded_lines(f):
                tick()
                if line.strip(): yield 'records',json_load(line)
        elif suffix == '.json':
            for value in json_array(f): tick(); yield 'records',value
        elif suffix in ('.csv','.tsv'):
            sample=f.read(65536); f.seek(0)
            delimiter='\t' if suffix=='.tsv' else csv.Sniffer().sniff(sample,delimiters=',;\t').delimiter
            csv.field_size_limit(MAX_RECORD)
            reader=csv.DictReader(bounded_lines(f),delimiter=delimiter)
            if not reader.fieldnames or len(set(reader.fieldnames))!=len(reader.fieldnames): raise Held('Missing/duplicate column names')
            for row in reader:
                tick()
                if None in row or None in row.values(): raise Held('Delimited column mismatch')
                yield 'records',row
        elif suffix == '.sql':
            for row in read_sql(f,tick): tick(); yield row
        elif suffix in ('.html','.htm'):
            text=f.read(MAX_RECORD+1)
            if len(text)>MAX_RECORD: raise Held('HTML page exceeds reader limit')
            page=Page(); page.feed(text)
            yield 'pages',{'title':' '.join(page.titles),'text':'\n'.join(page.text),'links':page.links}
        else:
            for index,line in enumerate(bounded_lines(f),1):
                tick(); record={'line':index,'text':line.rstrip('\r\n')}
                # Timestamp has no invented year/timezone. Raw fields remain available.
                m=re.match(r'^(\d+/\d+\s+\d+:\d+:\d+\.\d+)\s+([A-Z_]+),(.*)$',record['text'])
                if m:
                    parts=next(csv.reader([m[3]])); record.update(timestamp=m[1],event=m[2],fields=parts)
                    if len(parts)>=6: record.update(source_guid=parts[0],source_name=parts[1],target_guid=parts[3],target_name=parts[4])
                yield 'combat-events' if m else 'text-lines',record
