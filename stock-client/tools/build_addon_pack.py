#!/usr/bin/env python3
"""Build an isolated !AscensionShim core-preview pack; never installs into a client.

The output is confined to stock-client/build/. Choose a recovered dataset explicitly.
Collections/SharedXML integration remains a separate P2 layer, not claimed by this pack.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import gen_ca_data as gen

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'client/Interface/AddOns/!AscensionShim'


def build(data_root,dataset,class_byte,level,output,catalogue=None,enchants=None):
    data_root=Path(data_root).resolve()
    allowed=(ROOT/'build').resolve()
    output=Path(output).resolve()
    if not output.is_relative_to(allowed) or output==allowed:
        raise ValueError('pack output must be a named directory inside stock-client/build')
    manifest=json.loads((data_root/'generation.json').read_text(encoding='utf-8-sig'))
    selected=next((d for d in manifest['datasets'] if d['key']==dataset),None)
    if selected is None:raise ValueError('unknown dataset')
    base=data_root/'datasets'/dataset
    tables=json.loads((base/'tables.json').read_text(encoding='utf-8-sig'))
    identity=next((r for r in tables['classIdentities'] if r['ID']==class_byte),None)
    if not identity or 'CAClassName' not in identity:raise ValueError('unresolved preview class')
    if not 1<=level<=80:raise ValueError('preview level must be 1..80')
    config={'dataset':dataset,'classByte':class_byte,'level':level,'readOnly':True}
    texts={}; source_hashes={}
    for name in ('core/Namespaces.lua','core/EventBus.lua','core/CustomEvents.lua','core/Data.lua','core/BuildData.lua','core/Enchants.lua','api/C_ClassInfo.lua','api/C_CharacterAdvancement.lua','api/C_BuildCreator.lua','api/C_MysticEnchant.lua','core/Transport.lua','core/Live.lua','Bootstrap.lua'):
        raw=(SOURCE/name).read_bytes()
        source_hashes[name]=hashlib.sha256(raw).hexdigest()
        texts[name]=raw.decode('utf-8-sig')
    texts['Config.lua']='-- Generated explicit offline-preview selection.\nASC.Config='+gen.lua(config)+'\n'
    order=['core/Namespaces.lua','Config.lua','core/EventBus.lua','core/CustomEvents.lua','core/Data.lua','core/BuildData.lua','core/Enchants.lua']
    for relative in (base/'load-order.txt').read_text(encoding='utf-8-sig').splitlines():
        path=(base/relative).resolve()
        if not path.is_relative_to(base):raise ValueError('dataset path escapes source')
        raw=path.read_bytes()
        source_name=str(path.relative_to(data_root)).replace('\\','/')
        if hashlib.sha256(raw).hexdigest()!=manifest['outputs'].get(source_name):
            raise ValueError('generated source hash differs: '+relative)
        target='data/'+relative.removeprefix('lua/')
        texts[target]=raw.decode('utf-8-sig');order.append(target)
    if catalogue is not None:
        catalogue=Path(catalogue).resolve()
        catalogue_manifest=json.loads((catalogue/'generation.json').read_text(encoding='utf-8-sig'))
        config['buildCatalogue']=catalogue_manifest['metadata']['key']
        config['buildCatalogueRealm']=catalogue_manifest['metadata']['realm']
        config['buildCatalogueCapture']=catalogue_manifest['metadata']['capturedDate']
        for relative in (catalogue/'load-order.txt').read_text(encoding='utf-8-sig').splitlines():
            path=(catalogue/relative).resolve()
            if not path.is_relative_to(catalogue):raise ValueError('catalogue path escapes source')
            raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=catalogue_manifest['outputs'].get(relative):
                raise ValueError('catalogue source hash differs: '+relative)
            target='build-data/'+relative.removeprefix('lua/')
            texts[target]=raw.decode('utf-8-sig');order.append(target)
        source_hashes['catalogue-generation.json']=hashlib.sha256((catalogue/'generation.json').read_bytes()).hexdigest()
    if enchants is not None:
        enchants=Path(enchants).resolve()
        enchant_manifest=json.loads((enchants/'generation.json').read_text(encoding='utf-8-sig'))
        config['enchantCatalogue']=enchant_manifest['metadata']['key']
        config['enchantCount']=enchant_manifest['metadata']['enchantCount']
        for relative in (enchants/'load-order.txt').read_text(encoding='utf-8-sig').splitlines():
            path=(enchants/relative).resolve()
            if not path.is_relative_to(enchants):raise ValueError('enchant path escapes source')
            raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=enchant_manifest['outputs'].get(relative):
                raise ValueError('enchant source hash differs: '+relative)
            target='enchant-data/'+relative.removeprefix('lua/')
            texts[target]=raw.decode('utf-8-sig');order.append(target)
        source_hashes['enchants-generation.json']=hashlib.sha256((enchants/'generation.json').read_bytes()).hexdigest()
    texts['Config.lua']='-- Generated explicit offline-preview selection.\nASC.Config='+gen.lua(config)+'\n'
    order.extend(('api/C_ClassInfo.lua','api/C_CharacterAdvancement.lua','api/C_BuildCreator.lua','api/C_MysticEnchant.lua','core/Transport.lua','core/Live.lua','Bootstrap.lua'))
    texts['!AscensionShim.toc']='''## Interface: 30300
## Title: Ascension Stock Client Preview
## Notes: Data and API core preview; server learning is disabled.
## Author: Ascension preservation contributors
## Version: 0.1.0-dev

'''+ '\n'.join(name.replace('/','\\') for name in order)+'\n'
    texts['PACK-MANIFEST.json']=json.dumps({'format':1,'scope':'P2 core preview; original panel integration pending','config':config,'generationSha256':hashlib.sha256((data_root/'generation.json').read_bytes()).hexdigest(),'sourceHashes':source_hashes,'files':{name:hashlib.sha256(text.encode()).hexdigest() for name,text in sorted(texts.items())}},indent=2)+'\n'
    gen.write_outputs(output/'Interface/AddOns/!AscensionShim',texts)
    return output/'Interface/AddOns/!AscensionShim'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,default=ROOT/'data')
    parser.add_argument('--dataset',required=True)
    parser.add_argument('--class-byte',type=int,required=True)
    parser.add_argument('--level',type=int,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--catalogue',type=Path,help='Explicit generated community catalogue; no activation is enabled')
    parser.add_argument('--enchants',type=Path,help='Generated Mystic Enchant catalogue (tools/gen_enchant_data.py output)')
    args=parser.parse_args()
    print('Built:',build(args.data,args.dataset,args.class_byte,args.level,args.out,args.catalogue,args.enchants))

if __name__=='__main__':main()
