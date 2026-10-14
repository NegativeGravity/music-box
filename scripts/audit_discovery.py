"""Read-only coverage of taste discovery on the real shared audio catalog."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import time
import numpy as np

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'backend'))
from app.taste_discovery import discover

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--data',type=Path,default=root/'.data')
parser.add_argument('--output',type=Path,default=root/'.data/discovery-audit.json')
args=parser.parse_args()
with sqlite3.connect((args.data.resolve()/'musicbox.db').as_uri()+'?mode=ro',uri=True) as db:
    db.row_factory=sqlite3.Row
    rows=[dict(row) for row in db.execute("SELECT id,title,artist,genre,source,embedding_model,embedding,playback_status,duration FROM tracks WHERE source='catalog' AND visibility='shared' AND deleted=0 AND can_play=1 AND can_recommend=1 AND playback_status='ready' ORDER BY id")]
if len(rows)<2:raise SystemExit('At least two real playable catalog tracks are required')
for row in rows:row['embedding']=json.loads(row['embedding'])
timings=[]
for i,current in enumerate(rows):
    old=rows[(i+len(rows)//2)%len(rows)]['id']
    profiles=dict(long_term={old:2.},session={current['id']:1.},recent_track_ids=[current['id']],evidence={})
    started=time.perf_counter()
    result=discover(rows,profiles,current_id=current['id'])
    timings.append((time.perf_counter()-started)*1000)
    assert result['candidate_count']==len(rows) and result['next_scored_count']==len(rows)-1
    assert result['next_items'] and current['id'] not in {t['id'] for t in result['next_items']}
    assert all(np.isfinite(t['score']) for t in result['next_items'])
    assigned=[t['id'] for g in result['interest_groups'] for t in g['items']]
    assert len(assigned)==len(set(assigned))
    assert all('embedding' not in t for t in result['items'])
all_tastes=discover(rows,dict(long_term={r['id']:2. for r in rows},session={},recent_track_ids=[]))
report=dict(generated_at=datetime.now(timezone.utc).isoformat(),catalog_tracks=len(rows),recent_context_cases_passed=len(rows),next_candidates_per_case=len(rows)-1,finite_scores=True,overlapping_mix_tracks=False,all_tracks_evidence_case=dict(displayed_interests=len(all_tastes['interest_groups']),additional_interests=all_tastes['additional_interest_count'],unassigned_tracks=all_tastes['unassigned_count']),pure_discovery_latency_ms=dict(p50=round(float(np.percentile(timings,50)),2),p95=round(float(np.percentile(timings,95)),2)),limitations='Synthetic profile coverage on real audio; no listener quality or production capacity claim.')
args.output.parent.mkdir(parents=True,exist_ok=True)
args.output.write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))
