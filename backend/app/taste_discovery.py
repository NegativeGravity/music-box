"""Distinct audio interests, their catalog mixes, and recent-context next tracks."""
import hashlib
import numpy as np
from .recommendation_v2 import _vector, _weights, recommend

MERGE_COSINE = .80
MAX_INTERESTS = 6


def _center(members, vectors):
    value=sum(vectors[tid]*weight for tid,weight in members.items())
    norm=np.linalg.norm(value)
    return value/norm if norm>1e-10 else None


def _space(track, vector):
    return (track.get('embedding_model'),vector.size) if vector is not None else None


def _clusters(lookup, vectors, weights):
    groups=[dict(members={tid:w},center=vectors[tid],space=_space(lookup[tid],vectors[tid])) for tid,w in sorted(weights.items()) if vectors[tid] is not None]
    while True:
        best=None
        for i,left in enumerate(groups):
            for j in range(i+1,len(groups)):
                right=groups[j]
                if left['space']!=right['space']:continue
                cosine=float(np.dot(left['center'],right['center']))
                if cosine>=MERGE_COSINE and (best is None or cosine>best[0]):best=(cosine,i,j)
        if best is None:break
        _,i,j=best
        groups[i]['members'].update(groups[j]['members'])
        groups[i]['center']=_center(groups[i]['members'],vectors)
        groups.pop(j)
    return sorted(groups,key=lambda g:(-sum(g['members'].values()),min(g['members'])))


def _public(track, score, reason, because=None):
    return {k:v for k,v in dict(track,score=float(score),reason_code=reason,because_track_id=because).items() if k!='embedding'}


def discover(tracks, profiles, dislikes=(), current_id=None, random_seed=42):
    blocked=set(dislikes)
    lookup={t['id']:t for t in tracks if t['id'] not in blocked}
    candidates=[t for t in lookup.values() if t.get('source')!='demo']
    if not candidates:candidates=list(lookup.values())
    vectors={tid:_vector(t) for tid,t in lookup.items()}
    long=_weights(profiles.get('long_term',{}),lookup,blocked)
    recent=_weights(profiles.get('session',{}),lookup,blocked)
    evidence={tid:long.get(tid,0.)+recent.get(tid,0.) for tid in long.keys()|recent.keys()}
    all_groups=_clusters(lookup,vectors,evidence)
    groups=all_groups[:MAX_INTERESTS]
    assigned=[[] for _ in groups]
    unassigned=0
    for track in candidates:
        vector=vectors[track['id']]
        compatible=[(float(np.dot(vector,g['center'])),i) for i,g in enumerate(groups) if vector is not None and _space(track,vector)==g['space']]
        if not compatible:unassigned+=1;continue
        cosine,index=max(compatible,key=lambda pair:(pair[0],-pair[1]))
        assigned[index].append((track,cosine))
    interests=[]
    for group,pool in zip(groups,assigned):
        members=group['members']
        representative=max(members,key=lambda tid:(float(np.dot(vectors[tid],group['center'])),members[tid],-tid))
        gid=hashlib.sha256(f"{group['space']}:{sorted(members)}".encode()).hexdigest()[:16]
        ordered=[];artists={}
        while pool and len(ordered)<12:
            def selection(pair):
                track,score=pair;artist=track.get('artist','')
                penalty=.03*artists.get(artist,0) if artist.lower() not in {'','unknown artist','artist'} else 0.
                return score-penalty,-track['id']
            track,score=max(pool,key=selection);pool.remove((track,score))
            ordered.append(_public(track,score,'interest_mix',representative))
            artist=track.get('artist','');artists[artist]=artists.get(artist,0)+1
        interests.append(dict(id=gid,title=f"Inspired by {lookup[representative]['title']}"[:100],seed_track_ids=sorted(members),signal_count=len(members),support_weight=sum(members.values()),embedding_model=group['space'][0],dimensions=group['space'][1],items=ordered))

    order=[tid for tid in profiles.get('recent_track_ids',[]) if tid in recent][:6]
    recent_weights={tid:recent[tid] for tid in order} if order else recent
    spaces={}
    for tid,w in recent_weights.items():
        if vectors[tid] is not None:spaces.setdefault(_space(lookup[tid],vectors[tid]),{})[tid]=w
    centers={key:_center(values,vectors) for key,values in spaces.items()}
    current=lookup.get(current_id);current_vector=vectors.get(current_id)
    current_space=_space(current,current_vector) if current is not None else None
    next_pool=[t for t in candidates if t['id']!=current_id]
    scored=[]
    for track in next_pool:
        vector=vectors[track['id']];space=_space(track,vector)
        components={};weighted=[]
        if vector is not None:
            if current_vector is not None and space==current_space:
                components['current']=(float(np.dot(vector,current_vector))+1)/2;weighted.append((.7,components['current']))
            if centers.get(space) is not None:
                components['recent']=(float(np.dot(vector,centers[space]))+1)/2;weighted.append((.2,components['recent']))
            compatible=[g for g in groups if g['space']==space]
            if compatible:
                components['interest']=max((float(np.dot(vector,g['center']))+1)/2 for g in compatible);weighted.append((.1,components['interest']))
        tie=int(hashlib.sha256(f'{random_seed}:{track["id"]}'.encode()).hexdigest()[:8],16)/16**8
        score=sum(w*s for w,s in weighted)/sum(w for w,_ in weighted) if weighted else -.1+.01*tie
        reason='recent_match' if 'current' in components or 'recent' in components else 'interest_match' if weighted else 'cold_start'
        row=_public(track,score,reason,current_id if 'current' in components else order[0] if order and 'recent' in components else None)
        row['score_components']=components
        scored.append(row)
    cooldown=set(order[:3])
    available=[t for t in scored if t['id'] not in cooldown] or scored
    strategy='recent_context' if current_vector is not None or centers else 'lasting_interests' if groups else 'cold_start'
    if strategy=='cold_start':
        allowed={t['id'] for t in available}
        starting=recommend([t for t in next_pool if t['id'] in allowed],limit=5,random_seed=random_seed)
        next_items=starting['items']
    else:next_items=sorted(available,key=lambda t:(-t['score'],t['id']))[:5]
    flattened={t['id']:t for g in interests for t in g['items']}
    flattened.update({t['id']:t for t in next_items})
    return dict(surface='discovery',interest_groups=interests,next_items=next_items,items=list(flattened.values()),candidate_count=len(candidates),group_scored_count=len(candidates),next_scored_count=len(scored),invalid_embedding_count=sum(vectors[t['id']] is None for t in candidates),unassigned_count=unassigned,additional_interest_count=max(0,len(all_groups)-MAX_INTERESTS),next_strategy=strategy,profile_evidence=profiles.get('evidence',{}),recent_track_ids=order,policy_version='taste-mixes-v1',merge_cosine=MERGE_COSINE,random_seed=random_seed)
