"""Import an operator-supplied CSV catalog; audio files stay inside --folder."""
import argparse
import csv
import hashlib
import os
from pathlib import Path
import sys
from sqlalchemy import select

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'backend'))
from app.db import Track
from app.recommendation import GENRES
from app.service import Service

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--folder',type=Path,required=True)
parser.add_argument('--manifest',type=Path,required=True)
args=parser.parse_args()
folder=args.folder.resolve()
service=Service(os.getenv('MUSICBOX_DATA_DIR',str(root/'.data')),os.getenv('DATABASE_URL'))
added=skipped=0
with args.manifest.open(encoding='utf-8-sig',newline='') as stream:
    reader=csv.DictReader(stream)
    required={'file','title','artist','genre','license','can_play','can_analyze','can_recommend'}
    if not required.issubset(reader.fieldnames or []):
        raise SystemExit('Manifest missing columns: '+','.join(sorted(required-set(reader.fieldnames or []))))
    rows=list(reader)
    # Validate the entire manifest before committing any import.
    for row in rows:
        path=(folder/row['file']).resolve()
        if not path.is_relative_to(folder) or not path.is_file() or path.suffix.lower() not in {'.mp3','.wav'}:
            raise SystemExit('Invalid audio path: '+row['file'])
        if path.stat().st_size>100*1024*1024 or row['genre'] not in GENRES or not row['license'].strip():
            raise SystemExit('Invalid size, genre or provenance: '+row['file'])
        if any(row[field].lower().strip() not in {'1','true','yes'} for field in ['can_play','can_analyze','can_recommend']):
            raise SystemExit('Catalog entry must permit playback, analysis and recommendation: '+row['file'])
    for row in rows:
        data=(folder/row['file']).read_bytes()
        digest=hashlib.sha256(data).hexdigest()
        with service.Session() as db:
            existing=db.scalar(select(Track).where(Track.visibility=='shared',Track.source=='catalog',Track.sha256==digest,Track.deleted.is_(False)))
        if existing:
            skipped+=1
            continue
        service.add_shared_track(row['title'],row['artist'],row['genre'],data,source='catalog',provenance=row['license'])
        added+=1
print(f'Imported {added}; skipped {skipped} existing audio files. Worker processes queued files.')
