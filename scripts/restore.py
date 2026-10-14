"""Restore to a new, empty directory. Existing runtime data is never overwritten."""
import argparse
from pathlib import Path
import zipfile

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--archive',type=Path,required=True)
parser.add_argument('--destination',type=Path,required=True)
args=parser.parse_args()
target=args.destination.resolve()
if target.exists() and any(target.iterdir()):raise SystemExit('Restore destination must be empty')
with zipfile.ZipFile(args.archive) as archive:
    names=archive.namelist()
    if 'musicbox.db' not in names or 'backup-manifest.json' not in names:raise SystemExit('Invalid Musicbox backup')
    for name in names:
        if not (target/name).resolve().is_relative_to(target):raise SystemExit('Unsafe backup path')
    target.mkdir(parents=True,exist_ok=True)
    for name in names:
        path=target/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(archive.read(name))
print('Restored to',target,'. Point MUSICBOX_DATA_DIR at this directory to start.')
