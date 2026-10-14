"""Auditable recommendation: eligibility is supplied by the browser-scoped service."""
import hashlib
import numpy as np
import pandas as pd
from .category_decider import CategoryDecider, CategoryPolicy

GENRES = ['Hip-Hop', 'Electronic', 'Pop', 'Rock', 'Folk', 'Instrumental', 'Experimental', 'International']
GENRE_LABELS = dict(zip(GENRES, ['Hip-Hop', 'Electronic', 'Pop', 'Rock', 'Folk', 'Instrumental', 'Experimental', 'International']))
# Explicit taxonomy baseline, not a learned claim about genre proximity.
NEIGHBORS = {
    'Hip-Hop': ['Electronic', 'Pop'], 'Electronic': ['Hip-Hop', 'Pop', 'Experimental'],
    'Pop': ['Electronic', 'Rock', 'Hip-Hop'], 'Rock': ['Pop', 'Folk'],
    'Folk': ['Rock', 'Instrumental'], 'Instrumental': ['Folk', 'International'],
    'Experimental': ['Electronic', 'Instrumental'], 'International': ['Instrumental', 'Folk'],
}


def _cosine(left, right):
    left, right = np.asarray(left, dtype=float), np.asarray(right, dtype=float)
    if left.shape != right.shape or not np.isfinite(left).all() or not np.isfinite(right).all():
        return None
    norm = np.linalg.norm(left) * np.linalg.norm(right)
    return float(np.dot(left, right) / norm) if norm > 1e-10 else None


def recommend(tracks, genre='Hip-Hop', mode='balanced', likes=(), dislikes=(), session=(),
              limit=20, random_seed=42, surface='for_you', query_id=None):
    lookup = {t['id']: t for t in tracks}
    liked = [lookup[i] for i in likes if i in lookup and i not in dislikes]
    recent = [lookup[i] for i in session if i in lookup and i not in dislikes]
    query = lookup.get(query_id)
    seeds = [query] if query else (liked or recent)
    exclude = set(dislikes) | ({query_id} if query else set(likes) | set(session))
    available = [t for t in tracks if t['id'] not in exclude]
    result = dict(surface=surface, requested_limit=limit, returned_count=0, items=[],
                  target_exploration_rate=0 if mode == 'focus' or surface == 'similar' else .2,
                  actual_exploration_rate=0., shortage_reason=None,
                  policy_version='category-v1', ranker_version='hybrid-baseline-v1',
                  graph_version='curated-taxonomy-v1', random_seed=random_seed,
                  engine='metadata', category_plan=[])
    if not available:
        result['shortage_reason'] = 'no_eligible_tracks'
        return result
    decisions = None
    if surface == 'for_you':
        if genre not in {t['genre'] for t in available}:
            result['shortage_reason'] = 'primary_category_unavailable'
            return result
        frame = pd.DataFrame([dict(track_id=t['id'], genre=t['genre'], artist_id=t['artist']) for t in tracks])
        vectors = np.eye(len(GENRES))[[GENRES.index(t['genre']) for t in tracks]]
        decider = CategoryDecider(frame, vectors)
        # Keep original planner's quotas/availability behavior; inject a versioned taxonomy.
        decider.similarity = np.full((len(decider.genres),len(decider.genres)),-1.)
        np.fill_diagonal(decider.similarity,1.)
        for i, source in enumerate(decider.genres):
            for j, target in enumerate(decider.genres):
                if target in NEIGHBORS[source]:
                    decider.similarity[i, j] = .8 - .15 * NEIGHBORS[source].index(target)
        policy = CategoryPolicy(exploration_rate=result['target_exploration_rate'], seed=random_seed)
        plan = decider.decide(genre, limit, policy, eligible_track_ids=[t['id'] for t in available], max_per_artist=2)
        decisions = list(plan.decisions)
        result['category_plan'] = [vars(d) for d in decisions]

    scored = []
    for track in available:
        audio = []
        if track.get('embedding'):
            for seed in seeds:
                if seed and seed.get('embedding') and seed.get('embedding_model') == track.get('embedding_model'):
                    cosine = _cosine(track['embedding'], seed['embedding'])
                    if cosine is not None:
                        audio.append((cosine, seed['id']))
        best = max(audio, default=None)
        metadata = max([1. if track['genre'] == s['genre'] else 0. for s in seeds if s] or [1. if track['genre'] == genre else 0.])
        artist = float(any(track['artist'] == s['artist'] for s in liked))
        if best is not None:
            score = best[0] if surface=='similar' else .8 * ((best[0] + 1) / 2) + .2 * metadata + .05 * artist
            reason = 'similar_to_seed'
            because = best[1]
            engine = track.get('embedding_model', 'audio')
        else:
            tie = int(hashlib.sha256(f'{random_seed}:{track["id"]}'.encode()).hexdigest()[:8], 16) / 16**8
            score = metadata + .1 * artist + .025 * tie
            reason = 'preferred_genre' if track['genre'] == genre else 'neighbor_genre'
            because, engine = None, 'metadata'
        scored.append({**track, 'score': score, 'reason_code': reason, 'because_track_id': because, 'engine': engine})
    chosen, counts = [], {}
    slots = decisions if decisions is not None else [None] * limit
    for position, decision in enumerate(slots, 1):
        candidates = [t for t in scored if t['id'] not in {x['id'] for x in chosen}
                      and (decision is None or t['genre'] == decision.category)
                      and (surface == 'similar' or counts.get(t['artist'], 0) < 2)]
        if not candidates:
            continue
        candidates.sort(key=lambda t: (-t['score'], t['id']))
        selected = candidates[0]
        selected = {**selected, 'position': len(chosen)+1, 'decision_slot': position,
                    'decision_category': decision.category if decision else None,
                    'mode': decision.mode if decision else 'similar'}
        selected.pop('embedding', None)
        chosen.append(selected)
        counts[selected['artist']] = counts.get(selected['artist'], 0) + 1
    result['items'] = chosen
    result['returned_count'] = len(chosen)
    result['engine'] = next((x['engine'] for x in chosen if x['engine'] != 'metadata'), 'metadata')
    if chosen and surface == 'for_you':
        result['actual_exploration_rate'] = sum(t['genre'] != genre for t in chosen) / len(chosen)
    if len(chosen) < limit:
        result['shortage_reason'] = 'insufficient_category_or_artist_capacity'
    return result
