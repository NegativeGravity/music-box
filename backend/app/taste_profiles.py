"""Build separate persistent/recent listening evidence from authorized tracks."""
import math
import time
from datetime import datetime

DAY = 86400


def build_profiles(tracks, *, likes=(), references=(), dislikes=(), events=(), session_id=None, now=None):
    now = time.time() if now is None else now
    blocked = set(dislikes)
    lookup = {t['id']: t for t in tracks if t['id'] not in blocked}
    long_term, recent = {}, {}
    liked = set(likes) & lookup.keys()
    explicit = set(references) & lookup.keys()
    for tid in liked: long_term[tid] = 2.
    for tid in explicit: long_term[tid] = long_term.get(tid, 0.) + 3.
    grouped = {'long_term': {}, 'session': {}}
    seen = set()
    accepted = 0
    for event in events:
        tid = event.get('track_id')
        payload = event.get('payload')
        if tid not in lookup or not isinstance(payload, dict): continue
        event_id = event.get('event_id')
        if event_id and event_id in seen: continue
        if event_id: seen.add(event_id)
        try:
            received, played = float(event['created_at']), float(payload.get('played_ms', 0)) / 1000
            if not math.isfinite(received) or received > now: continue
            at = received
            # Receipt can follow playback by days when the browser outbox was offline.
            # Legacy events have no occurrence timestamp; tolerate small clock skew only.
            if payload.get('occurred_at') is not None:
                stamp = datetime.fromisoformat(payload['occurred_at'].replace('Z', '+00:00'))
                if stamp.tzinfo is None: continue
                occurred = stamp.timestamp()
                if occurred > received + 300: continue
                at = min(occurred, received)
        except (KeyError, TypeError, ValueError, OverflowError, AttributeError, OSError): continue
        age = now-at
        if not math.isfinite(at) or not math.isfinite(played) or age < 0 or age > 90*DAY or played <= 0: continue
        duration = lookup[tid].get('duration', 0) or 0
        played = min(played, duration if duration > 0 else 600, 600)
        active = age <= 3600 and (session_id is None or payload.get('session_id') == session_id)
        bucket = grouped['session' if active else 'long_term']
        values = bucket.setdefault(tid, [0., 0., at])
        decay = .5 ** (age/(30*DAY)) if not active else .5 ** (age/1800)
        values[0] += played
        values[1] += played*decay
        values[2] = max(values[2], at)
        accepted += 1
    for name, bucket in grouped.items():
        target = recent if name == 'session' else long_term
        for tid, (seconds, decayed, _) in bucket.items():
            if seconds >= 30:
                weight = min(3., math.sqrt(seconds/30)) * decayed/seconds
                target[tid] = target.get(tid, 0.) + weight
    order=sorted(recent,key=lambda tid:(-grouped['session'][tid][2],tid))
    return dict(long_term=long_term, session=recent, recent_track_ids=order, evidence=dict(likes=len(liked), references=len(explicit), historical_tracks=sum(v[0]>=30 for v in grouped['long_term'].values()), session_tracks=len(recent), accepted_play_segments=accepted, session_scope='active_session' if session_id is not None else 'recent_hour'))
