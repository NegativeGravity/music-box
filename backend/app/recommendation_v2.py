"""Full-catalog audio ranking with independent long-term and recent profiles."""
import hashlib
import math
import numpy as np


def _vector(track):
    if not track.get('embedding_model'): return None
    try:
        vector = np.asarray(track.get('embedding'), dtype=float)
        if vector.ndim != 1 or vector.size < 2 or not np.isfinite(vector).all(): return None
        norm = np.linalg.norm(vector)
        return vector/norm if math.isfinite(norm) and norm > 1e-10 else None
    except (TypeError, ValueError, OverflowError): return None


def _weights(values, lookup, blocked):
    """JSON snapshots stringify ID keys; restore only authorized, valid weights."""
    result = {}
    for tid, weight in values.items():
        try:
            tid, weight = int(tid), float(weight)
        except (TypeError, ValueError, OverflowError): continue
        if tid in lookup and tid not in blocked and math.isfinite(weight) and weight > 0:
            result[tid] = weight
    return result


def _profiles(weights, lookup, vectors):
    groups = {}
    for tid, weight in weights.items():
        vector = vectors.get(tid)
        if vector is None or tid not in lookup or not math.isfinite(weight) or weight <= 0: continue
        key = (lookup[tid].get('embedding_model'), vector.size)
        group = groups.setdefault(key, [])
        group.append((tid, vector, weight))
    profiles = {}
    for key, members in groups.items():
        center = sum(v*w for _, v, w in members)
        norm = np.linalg.norm(center)
        # Contradictory vectors can cancel. A weighted mixture still ranks them safely.
        profiles[key] = dict(center=center/norm if norm>1e-10 else None, members=members)
    return profiles


def _affinity(vector, profile):
    if profile['center'] is not None:
        cosine = float(np.clip(np.dot(vector, profile['center']), -1, 1))
    else:
        cosine = sum(float(np.dot(vector, v))*w for _, v, w in profile['members'])/sum(w for _, _, w in profile['members'])
    because = max(profile['members'], key=lambda x: float(np.dot(vector, x[1])))[0]
    return (cosine+1)/2, because


def recommend(tracks, genre=None, mode='balanced', likes=(), dislikes=(), session=(), limit=20, random_seed=42, surface='for_you', query_id=None, long_weights=None, session_weights=None, evidence=None):
    lookup = {t['id']:t for t in tracks}
    blocked = set(dislikes)
    vectors = {tid:_vector(t) for tid,t in lookup.items()}
    query = lookup.get(query_id) if surface=='similar' else None
    real_available = any(t.get('source')!='demo' for t in lookup.values() if t['id'] not in blocked and (surface!='similar' or t['id']!=query_id))
    candidates = [t for t in lookup.values() if t['id'] not in blocked and (surface!='similar' or t['id']!=query_id) and (not real_available or t.get('source')!='demo')]
    positive_long = long_weights if long_weights is not None else {tid:2. for tid in set(likes)}
    positive_short = session_weights if session_weights is not None else {tid:1. for tid in set(session)}
    positive_long = _weights(positive_long, lookup, blocked)
    positive_short = _weights(positive_short, lookup, blocked)
    long_profiles = _profiles(positive_long, lookup, vectors)
    short_profiles = _profiles(positive_short, lookup, vectors)
    weights = {'long_term':.7 if short_profiles else 1., 'session':.3} if long_profiles else {'long_term':0.,'session':1. if short_profiles else 0.}
    if not short_profiles: weights['session']=0.
    if surface=='similar': weights={'long_term':0.,'session':0.}
    profile_counts = {'long_term':sum(len(p['members']) for p in long_profiles.values()),'session':sum(len(p['members']) for p in short_profiles.values())}
    query_vector=vectors.get(query_id)
    similar_audio_available=query_vector is not None and any(vectors[t['id']] is not None and vectors[t['id']].size==query_vector.size and t.get('embedding_model')==query.get('embedding_model') for t in candidates)
    strategy = ('similar_audio' if similar_audio_available else 'similar_metadata_fallback') if surface=='similar' else 'hybrid_audio_profiles' if long_profiles or short_profiles else 'cold_start_diverse_audio' if any(vectors[t['id']] is not None for t in candidates) else 'cold_start_metadata'
    response = dict(surface=surface, requested_limit=limit, returned_count=0, items=[], candidate_count=len(candidates), scored_count=0,
                    embedding_count=sum(vectors[t['id']] is not None for t in candidates), invalid_embedding_count=sum(vectors[t['id']] is None for t in candidates),
                    profile_weights=weights, profile_counts=profile_counts, profile_evidence=evidence or {}, strategy=strategy,
                    policy_version='full-catalog-v2',ranker_version='audio-profile-v2',graph_version='audio-space-v1',random_seed=random_seed,
                    target_exploration_rate=0.,actual_exploration_rate=0.,shortage_reason=None,engine='metadata',category_plan=[])
    if surface=='similar' and (query is None or query_id in blocked):
        response['shortage_reason']='query_unavailable'
        return response
    scored=[]
    for track in candidates:
        tid=track['id']; vector=vectors[tid]
        key=(track.get('embedding_model'),vector.size) if vector is not None else None
        long_score=short_score=audio=None
        metadata_bonus=0.
        because=None
        if surface=='similar' and vector is not None and vectors.get(query_id) is not None and key==(query.get('embedding_model'),vectors[query_id].size):
            audio=float(np.clip(np.dot(vector,vectors[query_id]),-1,1));score=audio;reason='similar_to_seed';because=query_id
            used={'long_term':0.,'session':0.}
        else:
            if key in long_profiles: long_score,because=_affinity(vector,long_profiles[key])
            if key in short_profiles:
                short_score,short_because=_affinity(vector,short_profiles[key])
                if because is None:because=short_because
            if long_score is not None and short_score is not None: used={'long_term':.7,'session':.3}
            elif long_score is not None:used={'long_term':1.,'session':0.}
            elif short_score is not None:used={'long_term':0.,'session':1.}
            else:used={'long_term':0.,'session':0.}
            tie=int(hashlib.sha256(f'{random_seed}:{tid}'.encode()).hexdigest()[:8],16)/16**8
            score=used['long_term']*(long_score or 0)+used['session']*(short_score or 0) if long_score is not None or short_score is not None else .45+.05*tie
            reason='hybrid_taste' if long_score is not None and short_score is not None else 'long_term_taste' if long_score is not None else 'session_taste' if short_score is not None else 'cold_start_diversity'
            if surface=='similar':
                metadata_bonus=(.10 if query.get('genre') and track.get('genre')==query.get('genre') else 0.)
                artist=query.get('artist','')
                if artist.lower() not in {'','unknown artist','artist'} and track.get('artist')==artist:metadata_bonus+=.05
                score=-2.+metadata_bonus+.01*tie;reason='metadata_fallback';used={'long_term':0.,'session':0.}
        genre_bonus=.025 if surface!='similar' and track.get('genre') and track.get('genre')==genre else 0.
        repeat_penalty=(.10 if tid in positive_long else 0.)+(.15 if tid in positive_short else 0.) if surface!='similar' else 0.
        score+=genre_bonus-repeat_penalty
        components=dict(long_term=long_score,session=short_score,audio=audio,profile_weights=used,genre_bonus=genre_bonus,metadata_bonus=metadata_bonus,repeat_penalty=repeat_penalty)
        engine=track.get('embedding_model') if vector is not None and (surface!='similar' or audio is not None) else 'metadata'
        scored.append({**track,'score':float(score),'score_components':components,'reason_code':reason,'because_track_id':because,'engine':engine})
    response['scored_count']=len(scored)
    chosen=[];artist_counts={};remaining=list(scored)
    while remaining and len(chosen)<limit:
        for track in remaining:
            penalty=0.
            if surface!='similar' and (mode!='focus' or strategy=='cold_start_diverse_audio'):
                vector=vectors[track['id']]
                similarities=[max(0.,float(np.dot(vector,vectors[t['id']]))) for t in chosen if vector is not None and vectors[t['id']] is not None and vector.size==vectors[t['id']].size and track.get('embedding_model')==t.get('embedding_model')]
                penalty=.15*max(similarities,default=0.)
                artist=track.get('artist','')
                if artist.lower() not in {'','unknown artist','artist'}:penalty+=.04*artist_counts.get(artist,0)
            track['selection_score']=track['score']-penalty
            track['score_components']['diversity_penalty']=penalty
        selected=max(remaining,key=lambda t:(t['selection_score'],-t['id']))
        remaining.remove(selected)
        chosen.append(selected)
        artist=selected.get('artist','');artist_counts[artist]=artist_counts.get(artist,0)+1
    for i,track in enumerate(chosen,1):
        track.update(position=i,decision_slot=i,decision_category=track.get('genre'),mode=mode if surface!='similar' else 'similar')
        track.pop('embedding',None)
    response.update(items=chosen,returned_count=len(chosen),engine=next((t['engine'] for t in chosen if t['engine'] and t['engine']!='metadata'),'metadata'))
    if len(chosen)<limit:response['shortage_reason']='insufficient_eligible_tracks'
    return response
