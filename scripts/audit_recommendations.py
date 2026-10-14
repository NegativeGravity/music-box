"""Read-only real-catalog coverage and retrieval audit, without listener-quality claims."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import time
from datetime import datetime, timezone
import numpy as np

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'backend'))
from app.recommendation_v2 import recommend
from app.recommendation import recommend as baseline_recommend

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--data',type=Path,default=root/'.data')
parser.add_argument('--output',type=Path,default=root/'.data/recommendation-v2-audit.json')
args=parser.parse_args()
with sqlite3.connect((args.data.resolve()/'musicbox.db').as_uri()+'?mode=ro',uri=True) as db:
    db.row_factory=sqlite3.Row
    rows=[dict(r) for r in db.execute("SELECT id,title,artist,genre,source,embedding_model,embedding,playback_status,duration FROM tracks WHERE source='catalog' AND visibility='shared' AND deleted=0 AND can_play=1 AND can_recommend=1 AND playback_status='ready' ORDER BY id")]
if len(rows)<2:raise SystemExit('At least two real playable catalog tracks are required')
for row in rows:row['embedding']=json.loads(row['embedding']) if row['embedding'] else None
matrix=np.asarray([r['embedding'] for r in rows],dtype=float)
assert matrix.ndim==2 and matrix.shape[1]==512 and np.isfinite(matrix).all()
norms=np.linalg.norm(matrix,axis=1)
assert np.allclose(norms,1,atol=1e-4)
assert len({r['embedding_model'] for r in rows})==1
similarities=(matrix/norms[:,None])@(matrix/norms[:,None]).T
timings=[];examples=[]
for i,seed in enumerate(rows):
    start=time.perf_counter()
    result=recommend(rows,surface='similar',query_id=seed['id'],limit=min(10,len(rows)-1))
    timings.append((time.perf_counter()-start)*1000)
    assert result['candidate_count']==len(rows)-1 and result['scored_count']==len(rows)-1
    scores=similarities[i].copy();scores[i]=-2
    best={rows[j]['id'] for j in range(len(rows)) if abs(scores[j]-scores.max())<=1e-8}
    assert result['items'][0]['id'] in best
    assert len({t['id'] for t in result['items']})==result['returned_count']
    start=time.perf_counter()
    personal=recommend(rows,genre='Electronic',likes=[seed['id']],session=[rows[(i+len(rows)//2)%len(rows)]['id']],limit=20,random_seed=42)
    timings.append((time.perf_counter()-start)*1000)
    assert personal['candidate_count']==len(rows) and personal['scored_count']==len(rows)
    assert personal['returned_count']==min(20,len(rows))
    assert personal['profile_weights']=={'long_term':.7,'session':.3}
    if len(examples)<4:examples.append(dict(seed=seed['title'],nearest=[t['title'] for t in result['items'][:3]]))
baseline=baseline_recommend(rows,genre='Electronic',limit=20,random_seed=42)
current=recommend(rows,genre='Electronic',limit=20,random_seed=42)
report=dict(generated_at=datetime.now(timezone.utc).isoformat(),policy_version='full-catalog-v2',embedding_model=rows[0]['embedding_model'],catalog_tracks=len(rows),vector_dimensions=matrix.shape[1],finite_normalized_embeddings=True,query_cases_passed=len(rows),full_catalog_hybrid_cases_passed=len(rows),ranker_calls=len(timings),ranker_latency_ms=dict(p50=round(float(np.percentile(timings,50)),2),p95=round(float(np.percentile(timings,95)),2)),baseline_comparison=dict(preferred_genre='Electronic',baseline_returned=baseline['returned_count'],baseline_shortage=baseline['shortage_reason'],current_returned=current['returned_count'],current_scored=current['scored_count']),examples=examples,limitations='Local pure-ranker coverage and exact-cosine validation, not a production load test or proof of listener satisfaction.')
args.output.parent.mkdir(parents=True,exist_ok=True)
args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=True,indent=2))
