#!/usr/bin/env python3
"""Compile-only Lua 5.1 / XML dependency census. Never executes scanned UI code.

Uses Lua 5.1's actual global opcodes, including nested functions; strings, comments,
locals and table fields are not mistaken for globals. Requires lupa.lua51.
Reference: https://www.lua.org/source/5.1/lundump.c.html and lopcodes.h.html.
This is a syntactic inventory, not proof of load order or native API availability.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from lupa.lua51 import LuaRuntime


class ChunkReader:
    def __init__(self, data):
        self.data = data
        self.pos = 0
        header = self.take(12)
        if header[:6] != b'\x1bLua\x51\x00':
            raise ValueError('expected official Lua 5.1 bytecode')
        if header[6] not in (0, 1):
            raise ValueError('invalid byte order')
        self.endian = 'little' if header[6] else 'big'
        self.int_size, self.size_t, self.instruction_size, self.number_size = header[7:11]
        if self.int_size not in (4, 8) or self.size_t not in (4, 8) or self.instruction_size != 4 or self.number_size not in (4, 8):
            raise ValueError('unsupported bytecode scalar sizes')

    def take(self, size):
        if size < 0 or self.pos + size > len(self.data):
            raise ValueError('truncated bytecode')
        result = self.data[self.pos:self.pos + size]
        self.pos += size
        return result

    def uint(self, size=None):
        return int.from_bytes(self.take(size or self.int_size), self.endian)

    def count(self):
        value = self.uint()
        if value > len(self.data):
            raise ValueError('impossible bytecode count')
        return value

    def string(self):
        length = self.uint(self.size_t)
        if length == 0:
            return None
        value = self.take(length)
        if value[-1:] != b'\0':
            raise ValueError('unterminated bytecode string')
        return value[:-1].decode('utf-8', errors='replace')

    def prototype(self):
        self.string()
        first_line, last_line = self.uint(), self.uint()
        self.take(4)  # nups, parameters, vararg, maxstack
        code = [self.uint(4) for _ in range(self.count())]
        constants = []
        for _ in range(self.count()):
            tag = self.uint(1)
            if tag == 0:
                constants.append(None)
            elif tag == 1:
                constants.append(bool(self.uint(1)))
            elif tag == 3:
                self.take(self.number_size)
                constants.append(None)  # numeric values cannot name globals
            elif tag == 4:
                constants.append(self.string())
            else:
                raise ValueError('invalid constant type')
        children = [self.prototype() for _ in range(self.count())]
        lines = [self.uint() for _ in range(self.count())]
        for _ in range(self.count()):
            self.string(); self.uint(); self.uint()
        for _ in range(self.count()):
            self.string()
        if lines and len(lines) != len(code):
            raise ValueError('instruction/line count mismatch')
        reads, writes, members = [], [], []
        registers = {}
        skip_data_word = False
        for pc, word in enumerate(code):
            if skip_data_word:
                skip_data_word = False
                continue
            op = word & 63
            a = (word >> 6) & 255
            b = (word >> 23) & 511
            c = (word >> 14) & 511
            bx = word >> 14
            line = lines[pc] if lines else first_line
            if op == 34 and c == 0:  # SETLIST extended array block index is data, not an opcode
                skip_data_word = True
            if op in (5, 7):  # GETGLOBAL, SETGLOBAL
                if bx >= len(constants) or not isinstance(constants[bx], str):
                    raise ValueError('invalid global name constant')
                name = constants[bx]
                (reads if op == 5 else writes).append({'name': name, 'line': line})
                if op == 5:
                    registers[a] = name
            elif op in (6, 11):  # GETTABLE / SELF: bounded same-basic-block provenance
                base = registers.get(b)
                key = constants[c & 255] if c & 256 and (c & 255) < len(constants) else None
                if base and isinstance(key, str):
                    path = base + '.' + key
                    members.append({'name': path, 'line': line})
                else:
                    path = None
                registers.pop(a, None)
                if path:
                    registers[a] = path
                if op == 11:
                    registers.pop(a + 1, None)
            elif op == 0:  # MOVE
                value = registers.get(b)
                registers.pop(a, None)
                if value:
                    registers[a] = value
            elif op in (2, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 36):
                registers.clear()  # jumps, conditional skips, calls, closures
            elif op == 3:  # LOADNIL writes a range
                for register in range(a, b + 1):
                    registers.pop(register, None)
            elif op == 37:  # VARARG writes variable range
                registers.clear()
            elif op not in (7, 8, 9, 34, 35):
                registers.pop(a, None)
        for child in children:
            reads.extend(child['reads']); writes.extend(child['writes']); members.extend(child['members'])
        return {'reads': reads, 'writes': writes, 'members': members}

    def scan(self):
        result = self.prototype()
        if self.pos != len(self.data):
            raise ValueError('trailing bytecode bytes')
        return result


class Compiler:
    def __init__(self):
        self.runtime = LuaRuntime(encoding=None, unpack_returned_tuples=True)
        self.dump = self.runtime.eval(b'function(s) local f,e=loadstring(s); if not f then return nil,e end; return string.dump(f) end')

    def scan(self, source):
        result = self.dump(source.encode('utf-8'))
        if isinstance(result, tuple):
            raise ValueError(result[1].decode('utf-8', errors='replace'))
        return ChunkReader(result).scan()


def tag(element):
    return element.tag.rsplit('}', 1)[-1]


def relative(path, root):
    return path.relative_to(root).as_posix()


def scan_tree(root):
    root = Path(root).resolve()
    compiler = Compiler()
    records = []
    for path in sorted(root.rglob('*')):
        if path.suffix.lower() not in ('.lua', '.xml', '.toc') or not path.is_file():
            continue
        raw = path.read_bytes()
        record = {'file': relative(path, root), 'sha256': hashlib.sha256(raw).hexdigest(),
                  'reads': [], 'writes': [], 'members': [], 'templates': [], 'inherits': [],
                  'frames': [], 'mixins': [], 'includes': [], 'errors': []}
        records.append(record)
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = raw.decode('cp1252')
            record['encoding'] = 'cp1252'
        def compile_source(source, location, offset=0):
            try:
                found = compiler.scan(source)
                for key in ('reads', 'writes', 'members'):
                    for value in found[key]:
                        value['location'] = location
                        value['line'] = max(1, value['line'] - offset)
                    record[key].extend(found[key])
            except ValueError as exc:
                record['errors'].append({'location': location, 'error': str(exc)})
        if path.suffix.lower() == '.lua':
            compile_source(text, 'lua')
        elif path.suffix.lower() == '.toc':
            for line in text.splitlines():
                if line.strip() and not line.lstrip().startswith('#'):
                    record['includes'].append(line.strip().replace('\\', '/'))
        else:
            try:
                document = ET.fromstring(text)
            except ET.ParseError as exc:
                record['errors'].append({'location': 'xml', 'error': str(exc)})
                continue
            for ordinal, element in enumerate(document.iter()):
                kind = tag(element)
                attributes = element.attrib
                location = kind + '[' + str(ordinal) + ']'
                name = attributes.get('name')
                if name and '$' not in name:
                    if attributes.get('virtual', '').lower() in ('true', '1'):
                        record['templates'].append(name)
                    elif kind not in ('Attribute', 'KeyValue'):
                        record['frames'].append(name)
                for key, destination in (('inherits', 'inherits'), ('mixin', 'mixins')):
                    record[destination].extend(v.strip() for v in attributes.get(key, '').split(',') if v.strip())
                if kind in ('Script', 'Include') and attributes.get('file'):
                    record['includes'].append(attributes['file'].replace('\\', '/'))
                if kind == 'Script' and not attributes.get('file') and (element.text or '').strip():
                    compile_source(element.text, location)
                elif kind.startswith('On') and (element.text or '').strip():
                    # XML handlers supply these implicit arguments. Wrapping prevents false globals.
                    wrapper = 'return function(self, ...)\n' + element.text + '\nend'
                    record.setdefault('handlerArgumentCaveat', True)
                    compile_source(wrapper, location, 1)
                if attributes.get('function'):
                    compile_source(attributes['function'] + '()', location + '/@function')
                for value in attributes.values():
                    if value.startswith('global:'):
                        compile_source('return ' + value[len('global:'):], location + '/@global')
    return records


def index_records(records):
    providers = defaultdict(set)
    templates = defaultdict(set)
    reads = defaultdict(set)
    members = defaultdict(set)
    for record in records:
        path = record['file']
        for entry in record['writes']:
            providers[entry['name']].add(path)
        for name in record['frames']:
            providers[name].add(path)
        for name in record['templates']:
            templates[name].add(path)
        for entry in record['reads']:
            reads[entry['name']].add(path)
        for entry in record['members']:
            members[entry['name']].add(path)
    return {'providers': providers, 'templates': templates, 'reads': reads, 'members': members}


def closure(records, targets, stock_index):
    by_file = {r['file']: r for r in records}
    idx = index_records(records)
    selected = {p for p in by_file if any(p == t or p.startswith(t.rstrip('/') + '/') for t in targets)}
    if not selected:
        raise ValueError('no target files found')
    why = {p: ['target'] for p in selected}
    unresolved, ambiguous, stock = {}, {}, {}
    visited = set()
    # Providers are potential definitions, not a load-order assertion. Ambiguous candidates
    # are listed for review instead of importing unrelated files and global overwrites.
    while selected - visited:
        path = sorted(selected - visited)[0]
        visited.add(path)
        record = by_file[path]
        needs = [('global', v['name']) for v in record['reads']]
        needs += [('template', name) for name in record['inherits']]
        needs += [('global', name) for name in record['mixins']]
        for kind, name in sorted(set(needs)):
            key = kind + ':' + name
            lookup = 'providers' if kind == 'global' else 'templates'
            providers = idx[lookup].get(name, set())
            if providers & selected:
                continue
            if stock_index[lookup].get(name):
                stock.setdefault(key, set()).add(path)
                continue
            if len(providers) == 1:
                provider = next(iter(providers))
                selected.add(provider)
                why.setdefault(provider, []).append(path + ' requires ' + key)
            elif providers:
                ambiguous.setdefault(key, {'users': set(), 'candidates': sorted(providers)})['users'].add(path)
            else:
                unresolved.setdefault(key, set()).add(path)
        for include in record['includes']:
            candidate = str((Path(path).parent / include)).replace('\\', '/')
            # Normalize without touching filesystem; includes may use ../SharedXML.
            parts = []
            for part in candidate.split('/'):
                if part == '..':
                    if parts: parts.pop()
                elif part != '.': parts.append(part)
            candidate = '/'.join(parts)
            # MPQ filesystem is case insensitive.
            match = next((p for p in by_file if p.lower() == candidate.lower()), None)
            if match:
                selected.add(match)
                why.setdefault(match, []).append(path + ' includes ' + include)
            else:
                unresolved.setdefault('include:' + candidate, set()).add(path)
    # Remove earlier unresolved/ambiguous entries satisfied by later selection.
    for pending in (unresolved, ambiguous):
        for key in list(pending):
            kind, name = key.split(':', 1)
            lookup = 'providers' if kind == 'global' else 'templates'
            if kind in ('global', 'template') and idx[lookup].get(name, set()) & selected:
                del pending[key]
    return {'targets': targets, 'files': sorted(selected), 'reasons': {k: sorted(set(v)) for k, v in sorted(why.items())},
            'unresolved': {k: sorted(v) for k, v in sorted(unresolved.items())},
            'ambiguous': {k: {'users': sorted(v['users']), 'candidates': v['candidates']} for k, v in sorted(ambiguous.items())},
            'stockDefined': {k: sorted(v) for k, v in sorted(stock.items())}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ascension', type=Path, required=True)
    parser.add_argument('--stock', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--target', action='append', default=[])
    args = parser.parse_args()
    out = args.out.resolve()
    for source in (args.ascension.resolve(), args.stock.resolve()):
        if out.is_relative_to(source) or source.is_relative_to(out):
            raise ValueError('output must not overlap either input tree')
    records = scan_tree(args.ascension)
    stock = scan_tree(args.stock)
    report = {'format': 1, 'method': 'Lua 5.1 compile-only global opcodes and XML references',
              'limitations': ['Potential definitions do not prove execution or load order.',
                  'Absent stock Lua definitions may be native globals; not automatically missing APIs.',
                  'Namespace member paths use conservative same-basic-block register tracking; dynamic keys and upvalue aliases are incomplete.',
                  'XML handlers are compiled with self and varargs only; event, button, elapsed and other handler arguments may appear as unresolved globals until native handler signatures are supplied.',
                  'XML handler line numbers are relative to handler text, not XML source lines.',
                  'Template presence does not prove widget method compatibility. No native API is inferred from binary strings.'],
              'ascension': records, 'stock': stock}
    if args.target:
        report['closure'] = closure(records, args.target, index_records(stock))
    summary = {'files': {'ascension': len(records), 'stock': len(stock)},
               'errors': [{'tree': tree, 'file': r['file'], **error} for tree, rows in (('ascension', records), ('stock', stock)) for r in rows for error in r['errors']]}
    if 'closure' in report:
        summary['closure'] = {key: len(report['closure'][key]) for key in ('files', 'unresolved', 'ambiguous', 'stockDefined')}
    report['summary'] = summary
    out.mkdir(parents=True, exist_ok=True)
    (out / 'ui-dependencies.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    (out / 'summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
