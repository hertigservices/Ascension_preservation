"""Exact-content comparison against an existing ascension-data checkout, on disk.
A match is a byte-equivalent decoded payload, not proof of independent witnesses.
"""
import argparse
import csv
import gzip
import json
from pathlib import Path
import subprocess
import intake
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'data-storage'))
import dataset
import readers


def index(engine,data):
    data=Path(data).resolve()
    run=lambda *args: subprocess.check_output(['git','-C',str(data),*args])
    if run('status','--porcelain','--untracked-files=no').strip():raise ValueError('Baseline requires a clean data checkout')
    revision=run('rev-parse','HEAD').decode().strip()
    engine.db.executescript('''CREATE TABLE IF NOT EXISTS baseline(hash TEXT NOT NULL,path TEXT NOT NULL,ordinal INTEGER NOT NULL,PRIMARY KEY(hash,path,ordinal));
    CREATE TABLE IF NOT EXISTS baseline_files(path TEXT PRIMARY KEY,blob TEXT NOT NULL,rows INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS baseline_meta(revision TEXT NOT NULL);''')
    count=0;reused=0;held=[];present=[]
    files={}
    for entry in run('ls-tree','-rz','HEAD').split(b'\0'):
        if not entry:continue
        meta,path=entry.split(b'\t',1);path=path.decode('utf-8');blob=meta.split()[2].decode()
        files[path]=(data/path,blob)
    for path,(local,_) in list(files.items()):
        if path.startswith('datasets/') and path.endswith('.json'):
            manifest=dataset.validate(json.loads(local.read_text(encoding='utf-8-sig')))
            root=engine.root/'baseline-downloads'/manifest['snapshot']
            dataset.restore(manifest,root,engine.root/'baseline-downloads'/'packs')
            for name,entry in manifest['files'].items():files[name]=(root/name,'sha256:'+entry['sha256'])
    for path,(local,blob) in files.items():
        present.append(path)
        if not path.endswith(('.jsonl.gz','.jsonl','.tsv.gz','.tsv')):continue
        cached=engine.db.execute('SELECT blob,rows FROM baseline_files WHERE path=?',(path,)).fetchone()
        if cached and cached[0]==blob:count+=cached[1];reused+=1;continue
        engine.db.execute('DELETE FROM baseline WHERE path=?',(path,));engine.db.execute('DELETE FROM baseline_files WHERE path=?',(path,))
        engine.db.commit();engine.db.execute('SAVEPOINT baseline_file')
        try:
            open_file=gzip.open if path.endswith('.gz') else open
            with open_file(local,'rt',encoding='utf-8',newline='') as f:
                lines=readers.bounded_lines(f)
                if '.tsv' in path:
                    csv.field_size_limit(readers.MAX_RECORD)
                    records=csv.DictReader(lines,delimiter='\t',quoting=csv.QUOTE_NONE if '/catalogue/' in path or '/lootcollector/' in path else csv.QUOTE_MINIMAL)
                else: records=(readers.json_load(line) for line in lines if line.strip())
                n=0
                for n,value in enumerate(records,1):
                    if isinstance(value,dict) and None in value:raise ValueError('Malformed TSV')
                    payload=value.get('record',value) if isinstance(value,dict) else value
                    engine.db.execute('INSERT OR IGNORE INTO baseline VALUES(?,?,?)',(intake.digest(intake.encoded(payload)),path,n-1))
                engine.db.execute('INSERT INTO baseline_files VALUES(?,?,?)',(path,blob,n));count+=n
            engine.db.execute('RELEASE baseline_file');engine.db.commit()
        except (ValueError,OSError) as e:
            engine.db.execute('ROLLBACK TO baseline_file');engine.db.execute('RELEASE baseline_file');engine.db.commit();held.append({'path':path,'reason':str(e)})
    for row in engine.db.execute('SELECT path FROM baseline_files').fetchall():
        if row[0] not in present:
            engine.db.execute('DELETE FROM baseline WHERE path=?',(row[0],));engine.db.execute('DELETE FROM baseline_files WHERE path=?',(row[0],))
    engine.db.execute('DELETE FROM baseline_meta');engine.db.execute('INSERT INTO baseline_meta VALUES(?)',(revision,));engine.db.commit()
    return {'revision':revision,'indexed_occurrences':count,'reused_files':reused,'held_files':held,'coverage':'Tracked and manifest-backed TSV and JSONL only; unsupported published files are not claimed as compared'}


def compare(engine,source):
    if not engine.db.execute("SELECT name FROM sqlite_master WHERE name='baseline'").fetchone():raise ValueError('Index a baseline first')
    same=0;new=0;examples=[]
    for row in engine.db.execute('SELECT DISTINCT x.hash,x.payload FROM records x JOIN occurrences o ON o.record=x.hash JOIN receipts r ON r.document=o.document WHERE r.source=?',(source,)):
        value=json.loads(row['payload']);value=value.get('record',value) if isinstance(value,dict) else value
        match=engine.db.execute('SELECT path,ordinal FROM baseline WHERE hash=? LIMIT 1',(intake.digest(intake.encoded(value)),)).fetchone()
        if match:
            same+=1
            if len(examples)<20:examples.append({'record_sha256':row['hash'],'path':match[0],'ordinal':match[1]})
        else:new+=1
    return {'source':source,'exact_payloads_already_published':same,'not_exactly_matched':new,'examples':examples,'interpretation':'Unmatched does not mean new game content. No records or source receipts are discarded.'}


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,default=intake.DEFAULT_ROOT);ap.add_argument('--data',type=Path);ap.add_argument('--source');a=ap.parse_args()
    with intake.Intake(a.root,seconds=24*3600) as e:
        if a.data:print(json.dumps(index(e,a.data),indent=2))
        if a.source:print(json.dumps(compare(e,a.source),indent=2))
if __name__=='__main__':main()
