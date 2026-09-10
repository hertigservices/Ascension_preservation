#!/usr/bin/env python3
"""Check an assembled addon pack without loading any UI code.

Checks TOC/XML include reachability, Lua 5.1 compilation, pack hashes and global
resolution. Global resolution is syntactic, including callbacks; it does not prove
load order, native widget methods, namespace members or runtime compatibility.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from lupa.lua51 import LuaRuntime
from analyze_ui_dependencies import scan_tree, index_records

ROOT = Path(__file__).resolve().parents[1]
# Only the Lua globals used by this shim, available in the stock UI environment.
# Lupa additionally exposes python/io/os/package; those are NOT client APIs.
LUA_GLOBALS = set('_G assert getmetatable ipairs math next pairs pcall print select setmetatable string table tonumber tostring type unpack xpcall'.split())


def check(pack, contracts):
    pack = Path(pack).resolve()
    contracts = json.loads(Path(contracts).read_text(encoding='utf-8-sig'))
    if contracts.get('format') != 1:
        raise ValueError('unsupported contracts format')
    records = scan_tree(pack)
    by_file = {r['file']: r for r in records}
    case_names = {name.lower(): name for name in by_file}
    if len(case_names) != len(by_file):
        raise ValueError('case-colliding pack filenames')
    tocs = sorted(p for p in by_file if p.lower().endswith('.toc') and '/' not in p)
    if len(tocs) != 1:
        raise ValueError('expected exactly one addon TOC at pack root')
    selected = set()
    errors = []
    includes = []
    def visit(name, parents):
        if name in parents:
            errors.append('include cycle: ' + ' -> '.join(parents + [name]))
            return
        if name in selected:
            return
        selected.add(name)
        row = by_file[name]
        errors.extend(name + ': ' + e['location'] + ': ' + e['error'] for e in row['errors'])
        for relative in row['includes']:
            target = (pack / name).parent / relative
            target = target.resolve()
            if not target.is_relative_to(pack):
                errors.append(name + ': include escapes pack: ' + relative)
                continue
            wanted = target.relative_to(pack).as_posix()
            actual = case_names.get(wanted.lower())
            if not actual:
                errors.append(name + ': missing include: ' + relative)
                continue
            includes.append({'from': name, 'to': actual})
            visit(actual, parents + [name])
    visit(tocs[0], [])
    idx = index_records([by_file[name] for name in sorted(selected)])
    runtime = LuaRuntime()
    stdlib = LUA_GLOBALS
    providers = set(idx['providers'])
    for name, provider in contracts.get('dynamicProviders', {}).items():
        if provider not in selected:
            errors.append('declared dynamic provider is not loaded: ' + name + ' in ' + provider)
        else:
            providers.add(name)
    allowed = stdlib | providers | set(contracts.get('nativeGlobals', {})) | set(contracts.get('frameXMLGlobals', {}))
    unresolved = {name: sorted(files) for name, files in sorted(idx['reads'].items()) if name not in allowed}
    if unresolved:
        errors.append('unresolved globals: ' + ', '.join(unresolved))
    missing_templates = {name: sorted({r['file'] for r in records if r['file'] in selected and name in r['inherits']}) for name in sorted({name for r in records if r['file'] in selected for name in r['inherits']}) if name not in idx['templates']}
    if missing_templates:
        errors.append('unresolved XML templates: ' + ', '.join(missing_templates))
    manifest_path = pack / 'PACK-MANIFEST.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    for relative, digest in manifest['files'].items():
        path = (pack / relative).resolve()
        if not path.is_relative_to(pack):
            errors.append('manifest path escapes pack: ' + relative)
        elif not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            errors.append('pack hash mismatch: ' + relative)
    uncovered = sorted(selected - set(manifest['files']))
    if uncovered:
        errors.append('loaded files absent from integrity manifest: ' + ', '.join(uncovered))
    return {'format': 1, 'passed': not errors, 'luaVersion': runtime.eval('_VERSION'),
            'scope': manifest['scope'], 'loadedFiles': sorted(selected), 'includeEdges': includes,
            'globalCount': len(idx['reads']), 'unresolvedGlobals': unresolved,
            'unresolvedTemplates': missing_templates, 'errors': errors,
            'limitations': ['No scanned UI code was executed.',
                'Potential global providers do not establish execution order or correct namespace members.',
                'Native widget methods and stock XML inheritance require an in-client check.',
                'Dynamic providers and native/FrameXML globals are explicit reviewed contracts, not binary-string heuristics.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pack', type=Path, required=True)
    parser.add_argument('--contracts', type=Path, default=ROOT/'client/API-CONTRACTS.json')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    result = check(args.pack, args.contracts)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2) + '\n',encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('passed','luaVersion','globalCount','unresolvedGlobals','errors')},indent=2))
    print('Loaded files:',len(result['loadedFiles']))
    return 0 if result['passed'] else 1


if __name__ == '__main__': sys.exit(main())
