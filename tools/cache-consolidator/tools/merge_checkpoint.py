"""Recoverable metadata commit. Appended payload packs are flushed before commit.

The prepared generation is immutable until all indexes and sources are installed.
A crash replays the whole generation; readers run after recovery under the publisher
lock. Unreferenced pack bytes from a pre-commit interruption are harmless.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil


def sync_directory(path):
    # Windows does not expose POSIX directory fsync through Python. The Windows
    # guarantee here is process-interruption recovery, not hardware power loss.
    if os.name != 'nt':
        fd = os.open(path, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)


def recover(store):
    store = Path(store).resolve()
    txn = store / '.merge-transaction'
    ready = txn / 'ready.json'
    if not ready.exists():
        return
    entries = json.loads(ready.read_text(encoding='utf-8'))
    # Verify every prepared file BEFORE installing any member of the generation.
    for entry in entries:
        target = (store / entry['path']).resolve()
        source = txn / entry['file']
        if not target.is_relative_to(store) or target.is_relative_to(txn):
            raise ValueError('Unsafe merge transaction path')
        if hashlib.sha256(source.read_bytes()).hexdigest() != entry['sha256']:
            raise RuntimeError('Damaged merge checkpoint; preserve transaction for recovery')
    for entry in entries:
        target = store / entry['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + '.recovering')
        with (txn / entry['file']).open('rb') as src, temp.open('wb') as dst:
            shutil.copyfileobj(src, dst)
            dst.flush()
            os.fsync(dst.fileno())
        os.replace(temp, target)
        sync_directory(target.parent)
    ready.unlink()
    sync_directory(txn)
    shutil.rmtree(txn)


def commit(store, tables, write_tsv):
    store = Path(store).resolve()
    recover(store)
    txn = store / '.merge-transaction'
    # No ready marker means no part of this abandoned preparation was installed.
    if txn.exists():
        if txn.is_symlink() or txn.is_junction():
            raise ValueError('Unsafe transaction directory')
        shutil.rmtree(txn)
    txn.mkdir()
    entries = []
    for i, (path, cols, rows) in enumerate(tables):
        target = Path(path).resolve()
        rel = target.relative_to(store).as_posix()
        prepared = txn / ('%d.tsv' % i)
        write_tsv(str(prepared), cols, rows)
        with prepared.open('r+b') as f:
            os.fsync(f.fileno())
        entries.append(dict(path=rel, file=prepared.name,
                            sha256=hashlib.sha256(prepared.read_bytes()).hexdigest()))
    temp = txn / 'ready.tmp'
    with temp.open('w', encoding='utf-8') as f:
        json.dump(entries, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, txn / 'ready.json')
    sync_directory(txn)
    sync_directory(store)
    recover(store)
