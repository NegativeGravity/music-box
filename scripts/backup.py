"""Snapshot SQLite and its referenced audio. Stop writers first for a full asset backup."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import zipfile

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--data',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
source=args.data.resolve()
output=args.output.resolve()
if output.exists():raise SystemExit('Backup output already exists')
output.parent.mkdir(parents=True,exist_ok=True)
partial=output.with_name(output.name+'.partial')
with tempfile.TemporaryDirectory() as temp:
    snapshot=Path(temp)/'musicbox.db'
    with closing(sqlite3.connect((source / 'musicbox.db').as_uri()+'?mode=ro',uri=True)) as original,closing(sqlite3.connect(snapshot)) as copy:
        original.backup(copy)
    with closing(sqlite3.connect(snapshot)) as db:
        keys={key for row in db.execute('SELECT original_key,stream_key FROM tracks WHERE deleted=0') for key in row if key}
    with zipfile.ZipFile(partial,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.write(snapshot,'musicbox.db')
        for key in sorted(keys):
            path=(source/'assets'/key).resolve()
            if not path.is_relative_to(source/'assets') or not path.is_file():
                raise SystemExit('Referenced asset missing; stop writers and retry')
            archive.write(path,'assets/'+key)
        if (source/'clap.enabled').exists():archive.write(source/'clap.enabled','clap.enabled')
        archive.writestr('backup-manifest.json',json.dumps({'schema':1,'assets':len(keys)}))
partial.rename(output)
print('Backup complete:',output,'audio files:',len(keys))
