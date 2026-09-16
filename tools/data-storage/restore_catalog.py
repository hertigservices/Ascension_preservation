"""Restore a complete archived catalog to a fresh directory; never contacts R2."""
import argparse
import json
from pathlib import Path
import shutil
from dataset import atomic
from live_catalog import Archive,hashed,prefix,validate_report
from live_backup import ArchiveIndex


def restore(archive,index,snapshot,output):
    manifest=archive.root/'catalogs'/snapshot/'storage-manifest.json'
    if manifest.exists():report=json.loads(manifest.read_text())
    else:
        saved=index.get(prefix(snapshot)+'storage-manifest.json')
        if not saved:raise ValueError('No complete catalog manifest in this backup')
        archive.verify(saved);report=json.loads(archive.path(saved['sha256']).read_bytes())
    validate_report(report)
    if report['snapshot']!=snapshot:raise ValueError('Backup snapshot differs')
    output=Path(output).resolve()
    if archive.blobs.resolve() in output.parents:raise ValueError('Restore cannot write into the blob store')
    output.mkdir(parents=True,exist_ok=False)
    count=total=0
    for name,meta in report['files'].items():
        archive.verify(meta)
        destination=output/name;destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(archive.path(meta['sha256']),destination)
        sha,_,size=hashed(destination)
        if sha!=meta['sha256'] or size!=meta['bytes']:raise ValueError('Restored content differs')
        count+=1;total+=size
    receipt={'snapshot':snapshot,'objects':count,'bytes':total,'output':str(output),'all_hashes_matched':True}
    atomic(output.with_name(output.name+'-restore-receipt.json'),receipt)
    return receipt


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--archive',type=Path,required=True);ap.add_argument('--snapshot',required=True);ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
    archive=Archive(args.archive);index=ArchiveIndex(archive.root)
    try:print(json.dumps(restore(archive,index,args.snapshot,args.out),indent=2))
    finally:index.db.close()


if __name__=='__main__':main()
