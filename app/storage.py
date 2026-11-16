"""Atomic local workspace files, with one-time migration from legacy SQLite."""
import fcntl
import json
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException

DATA_DIR = Path(os.getenv('NETWORKBENCH_DATA_DIR', 'data'))
THREAD_LOCK = threading.RLock()


@contextmanager
def locked():
    DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    with THREAD_LOCK, (DATA_DIR / '.storage.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def folder(workspace_id):
    if not re.fullmatch(r'[0-9a-f]{32}', workspace_id):
        raise HTTPException(404, 'Workspace not found')
    path = DATA_DIR / 'workspaces' / workspace_id
    if path.is_symlink():
        raise HTTPException(404, 'Workspace not found')
    return path


def read(workspace_id):
    path = folder(workspace_id) / 'workspace.json'
    if path.is_symlink() or not path.is_file():
        raise HTTPException(404, 'Workspace not found')
    try:
        value = json.loads(path.read_text())
        if value['id'] != workspace_id:
            raise ValueError('Mismatched ID')
        return value
    except (ValueError, KeyError, OSError) as error:
        raise HTTPException(500, 'Unable to read saved workspace') from error


def write(value):
    directory = folder(value['id'])
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=directory, prefix='.save-', delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=True, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, directory / 'workspace.json')
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def initialize():
    with locked():
        root = DATA_DIR / 'workspaces'
        root.mkdir(exist_ok=True, mode=0o700)
        # Finish interrupted deletions before serving requests.
        for path in root.glob('.deleted-*'):
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
        legacy = DATA_DIR / 'racksmith.sqlite3'
        if not legacy.exists():
            return
        with sqlite3.connect(legacy) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute('SELECT * FROM workspaces ORDER BY id').fetchall()
            for row in rows:
                value = dict(row)
                value.pop('user_id', None)
                value['options'] = json.loads(value['options'])
                target = folder(value['id']) / 'workspace.json'
                if target.exists():
                    if read(value['id']) != value:
                        raise RuntimeError('Migration conflict: existing workspace differs from SQLite')
                else:
                    write(value)
                if read(value['id']) != value:
                    raise RuntimeError('Workspace migration verification failed')
            archive = DATA_DIR / 'archive'
            archive.mkdir(exist_ok=True, mode=0o700)
            backup = archive / 'before-file-storage.sqlite3'
            # A consistent backup includes any SQLite WAL data.
            if not backup.exists():
                temporary_backup = archive / '.migration.sqlite3'
                temporary_backup.unlink(missing_ok=True)
                with sqlite3.connect(temporary_backup) as destination:
                    db.backup(destination)
                    if destination.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise RuntimeError('Migration backup integrity check failed')
                destination.close()
                temporary_backup.chmod(0o600)
                os.replace(temporary_backup, backup)
        db.close()
        legacy.unlink()
        for suffix in ('-wal', '-shm'):
            Path(str(legacy) + suffix).unlink(missing_ok=True)
        # Preserve earlier manual backups outside the active workspace store.
        previous = DATA_DIR / 'racksmith-before-rebuild.sqlite3'
        if previous.exists() and not (archive / previous.name).exists():
            previous.rename(archive / previous.name)


def list_all(tool=None):
    with locked():
        items = [read(path.name) for path in (DATA_DIR / 'workspaces').iterdir()
                 if path.is_dir() and not path.is_symlink() and re.fullmatch(r'[0-9a-f]{32}', path.name)]
        return sorted((item for item in items if tool is None or item['tool'] == tool),
                      key=lambda item: (-item['updated_at'], item['id']))


def get(workspace_id):
    with locked():
        return read(workspace_id)


def create(payload):
    with locked():
        value = {**payload, 'id': secrets.token_hex(16), 'revision': 1, 'updated_at': int(time.time())}
        write(value)
        return value


def save(workspace_id, payload):
    with locked():
        current = read(workspace_id)
        if current['revision'] != payload['revision']:
            raise HTTPException(409, 'Workspace changed; reload before saving')
        value = {**payload, 'id': workspace_id, 'revision': current['revision'] + 1, 'updated_at': int(time.time())}
        write(value)
        return value


def delete(workspace_id):
    with locked():
        read(workspace_id)
        directory = folder(workspace_id)
        deleted = directory.with_name('.deleted-' + workspace_id)
        directory.rename(deleted)
        try:
            shutil.rmtree(deleted)
        except OSError as error:
            # Keep it hidden from the UI; initialization retries cleanup.
            raise HTTPException(500, 'File deletion incomplete; restart NetworkBench to retry cleanup') from error


def delete_all():
    """Remove every workspace entry without following links outside the store."""
    with locked():
        root = DATA_DIR / 'workspaces'
        if root.is_symlink():
            raise HTTPException(500, 'Workspace storage cannot be a symbolic link')
        try:
            for path in root.iterdir():
                if path.is_symlink() or not path.is_dir():
                    path.unlink()
                else:
                    shutil.rmtree(path)
        except OSError as error:
            raise HTTPException(500, 'Cleanup incomplete; retry to remove remaining files') from error
