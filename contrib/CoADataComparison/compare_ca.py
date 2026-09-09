"""Read-only, stdlib-only CA/community comparison. Outputs IDs, not source text.

Community dependencies are hypotheses, never authoritative runtime additions.
The supplied revision is a caller assertion, not verified against a checkout.
Only the documented JSON role files are opened; no imported corpus code runs.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys


class InvalidInput(ValueError):
    """A deliberately content-free validation failure."""


def require(condition):
    if not condition:
        raise InvalidInput('invalid schema')


def integer(value):
    require(type(value) is int and value >= 0)
    return value


def node_id(value):
    require(type(value) is str and re.fullmatch(r'(?:0|[1-9][0-9]*)-(?:0|[1-9][0-9]*)-(?:0|[1-9][0-9]*)-(?:0|[1-9][0-9]*)[a-z]?', value) is not None)
    return value


def records(value, key, validator=integer):
    require(type(value) is list)
    seen = set()
    for item in value:
        require(type(item) is dict and key in item)
        identity = validator(item[key])
        require(identity not in seen)
        seen.add(identity)
    return value


def text_fields(item, keys):
    for key in keys:
        if key in item:
            require(item[key] is None or type(item[key]) is str)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def reject_constant(value):
    raise InvalidInput('invalid JSON constant')


def compare(ca_root, hub_root, revision):
    """Return a deterministic report; never write to the input directories."""
    require(type(revision) is str and re.fullmatch(r'[0-9a-fA-F]{40}', revision) is not None)
    ca_root, hub_root = Path(ca_root).resolve(strict=True), Path(hub_root).resolve(strict=True)
    sources = []

    def load(root, role, relative):
        path = root / relative
        # Resolve redirects before reading, so allowlisted names cannot escape.
        require(path.resolve(strict=True).is_relative_to(root))
        raw = path.read_bytes()
        sources.append(dict(role=role, path=relative, bytes=len(raw),
                            sha256=hashlib.sha256(raw).hexdigest()))
        return json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)

    entries = records(load(ca_root, 'ca', 'entries.json'), 'ID')
    for entry in entries:
        for key in ('SpellID', 'SpellID2', 'SpellID3', 'SpellID4', 'SpellID5', 'ClassTypeID', 'TabTypeID'):
            integer(entry[key])
        text_fields(entry, ('Description', 'Description2'))
    runtime = load(ca_root, 'ca', 'edges.json')
    require(type(runtime) is dict)
    for key, values in runtime.items():
        require(re.fullmatch(r'0|[1-9][0-9]*', key) is not None)
        require(type(values) is list)
        for value in values:
            integer(value)
        require(len(values) == len(set(values)))
    classes = records(load(hub_root, 'hub', 'classes.json'), 'id')
    slugs = set()
    for cls in classes:
        slug = cls['slug']
        require(type(slug) is str and re.fullmatch(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*', slug) is not None)
        require(slug not in slugs)
        slugs.add(slug)
    master = records(load(hub_root, 'hub', 'spells/master_index.json'), 'id')
    for item in master:
        text_fields(item, ('description',))
    nodes = []
    skills = []
    for cls in classes:
        skill = load(hub_root, 'hub', 'skills/' + cls['slug'] + '.json')
        talent = load(hub_root, 'hub', 'talents/' + cls['slug'] + '.json')
        require(type(skill) is dict and type(talent) is dict)
        require(integer(skill['classId']) == cls['id'])
        skill_rows = records(skill['skills'], 'id')
        require(integer(skill['count']) == len(skill_rows))
        trees = records(talent['trees'], 'tabId')
        class_nodes = []
        for tree in trees:
            tree_nodes = records(tree['nodes'], 'id', node_id)
            require(integer(tree['nodeCount']) == len(tree_nodes))
            for node in tree_nodes:
                integer(node['spellId'])
                deps = node.get('dependencies', [])
                require(type(deps) is list)
                for dep in deps:
                    node_id(dep)
                require(len(deps) == len(set(deps)))
            class_nodes.extend(tree_nodes)
        require(integer(talent['nodeCount']) == len(class_nodes))
        skills.extend(skill_rows)
        nodes.extend(class_nodes)
    records(nodes, 'id', node_id)
    primary = defaultdict(list)
    ranks = defaultdict(list)
    for entry in entries:
        primary[entry['SpellID']].append(entry)
        for rank in range(1, 6):
            spell = entry['SpellID' + (str(rank) if rank > 1 else '')]
            if spell:
                ranks[spell].append(dict(ca_id=entry['ID'], rank=rank))
    mids = {m['id'] for m in master}
    descriptions = {m['id'] for m in master if m.get('description')}
    enrich = sorted((dict(ca_id=e['ID'], spell_id=e['SpellID']) for e in entries
                     if not e.get('Description') and not e.get('Description2')
                     and e['SpellID'] in descriptions), key=lambda e: e['ca_id'])
    pairs = {(int(a), b) for a, bs in runtime.items() for b in bs}
    node_map = {n['id']: n for n in nodes}
    counts = dict(present_runtime_edge=0, candidate_not_runtime_edge=0,
                  candidates_same_ca_class_tab=0, unmapped_or_ambiguous=0,
                  dangling_community_node=0)
    hypotheses = []
    for node in sorted(nodes, key=lambda n: n['id']):
        for dep in sorted(node.get('dependencies', [])):
            a = primary[node['spellId']]
            target = node_map.get(dep)
            b = primary[target['spellId']] if target else []
            item = dict(source_node_id=node['id'], target_node_id=dep,
                        source_spell_id=node['spellId'],
                        target_spell_id=target['spellId'] if target else None,
                        source_ca_ids=sorted(e['ID'] for e in a),
                        target_ca_ids=sorted(e['ID'] for e in b),
                        same_ca_class_tab=None)
            if target is None:
                status = 'dangling_community_node'
            elif len(a) != 1 or len(b) != 1:
                status = 'unmapped_or_ambiguous'
            else:
                same = (a[0]['ClassTypeID'], a[0]['TabTypeID']) == (b[0]['ClassTypeID'], b[0]['TabTypeID'])
                item['same_ca_class_tab'] = same
                status = 'present_runtime_edge' if (a[0]['ID'], b[0]['ID']) in pairs else 'candidate_not_runtime_edge'
                if status == 'candidate_not_runtime_edge' and same:
                    counts['candidates_same_ca_class_tab'] += 1
            counts[status] += 1
            item['status'] = status
            hypotheses.append(item)
    entry_ids = {e['ID'] for e in entries}
    return dict(schema_version=1,
                upstream_revision=dict(value=revision, status='asserted_not_verified'),
                dependency_interpretation='community_hypotheses_not_runtime_authority',
                sources=sorted(sources, key=lambda s: (s['role'], s['path'])),
                summary=dict(ca_entries=len(entries), runtime_edges=len(pairs),
                             master_ids=len(mids), master_any_rank_overlap=len(mids & ranks.keys()),
                             master_absent_from_ca=len(mids - ranks.keys()),
                             description_enrichment_rows=len(enrich),
                             skill_files=len(classes), talent_files=len(classes),
                             skill_rows=len(skills), talent_nodes=len(nodes),
                             community_edges=counts),
                master_absent_ids=sorted(mids - ranks.keys()),
                numeric_crosswalk=[dict(spell_id=i, ca_rank_matches=sorted(ranks[i], key=lambda r: (r['ca_id'], r['rank']))) for i in sorted(mids)],
                description_candidates=enrich, edge_hypotheses=hypotheses,
                runtime_dangling_source_ids=sorted({int(a) for a in runtime} - entry_ids),
                runtime_dangling_target_ids=sorted({b for a, b in pairs} - entry_ids))


class PrivateArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, 'comparison failed: invalid arguments\n')


def main(argv=None):
    parser = PrivateArgumentParser(prog='compare_ca.py', description=__doc__, allow_abbrev=False)
    parser.add_argument('--ca-root', required=True)
    parser.add_argument('--hub-root', required=True)
    parser.add_argument('--upstream-revision', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try:
        ca_root = Path(args.ca_root).resolve(strict=True)
        hub_root = Path(args.hub_root).resolve(strict=True)
        output = Path(args.output)
        resolved_output = output.resolve()
        require(not resolved_output.is_relative_to(ca_root) and not resolved_output.is_relative_to(hub_root))
        require(not output.exists() and not output.is_symlink())
        report = compare(ca_root, hub_root, args.upstream_revision)
        payload = json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + '\n'
        with output.open('x', encoding='utf-8', newline='\n') as handle:
            handle.write(payload)
        return 0
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        print('comparison failed: invalid input or output', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
