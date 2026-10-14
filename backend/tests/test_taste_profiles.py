from app.taste_profiles import build_profiles
from datetime import datetime, timezone
import math


def song(tid):return dict(id=tid,duration=100.)
def event(eid,tid,at,session,played):return dict(event_id=eid,track_id=tid,created_at=at,payload={'session_id':session,'played_ms':played})


def test_active_session_and_historical_taste_are_separate_and_replays_do_not_inflate():
    now=1000000.
    events=[event('a',1,now-10,'active',20000),event('b',1,now-5,'active',20000),event('c',2,now-9000,'old',60000)]
    result=build_profiles([song(1),song(2)],events=events,session_id='active',now=now)
    assert set(result['session'])=={1}
    assert set(result['long_term'])=={2}
    replay=build_profiles([song(1),song(2)],events=events+[events[0]],session_id='active',now=now)
    assert replay==result


def test_likes_references_and_history_combine_with_time_decay():
    now=10000000.
    rows=[song(i) for i in range(1,5)]
    result=build_profiles(rows,likes=[1],references=[2],events=[event('a',3,now-86400,'old',60000),event('b',4,now-31*86400,'old',60000)],session_id='active',now=now)
    assert set(result['long_term'])=={1,2,3,4}
    assert result['long_term'][2]>result['long_term'][1]
    assert abs(result['long_term'][4]/result['long_term'][3]-.5)<1e-8


def test_unavailable_disliked_stale_and_invalid_events_do_not_enter_profiles():
    now=10000000.
    events=[event('a',1,now+1,'active',60000),event('b',2,now-91*86400,'old',60000),event('c',99,now-1,'active',60000),event('d',3,now-1,'active',60000),event('e',2,now-1,'active',float('nan'))]
    result=build_profiles([song(1),song(2),song(3)],likes=[99,3],references=[3],dislikes=[3],events=events,session_id='active',now=now)
    assert result['long_term']=={}
    assert result['session']=={}


def test_offline_events_use_occurrence_time_for_session_and_history_decay():
    now=10000000.
    rows=[song(i) for i in range(1,4)]
    events=[]
    for tid, age in [(1,7200),(2,30*86400),(3,91*86400)]:
        item=event(str(tid),tid,now,'active',60000)
        item['payload']['occurred_at']=datetime.fromtimestamp(now-age,timezone.utc).isoformat()
        events.append(item)
    result=build_profiles(rows,events=events,session_id='active',now=now)
    assert result['session']=={}
    assert set(result['long_term'])=={1,2}
    assert abs(result['long_term'][2]-math.sqrt(2)*.5)<1e-8


def test_invalid_occurrence_times_are_ignored_and_small_clock_skew_is_bounded():
    now=10000000.
    events=[]
    for tid, stamp in [(1,'invalid'),(2,'1970-01-01T00:00:00'),(3,datetime.fromtimestamp(now+3600,timezone.utc).isoformat()),(4,datetime.fromtimestamp(now+30,timezone.utc).isoformat())]:
        item=event(str(tid),tid,now,'active',60000)
        item['payload']['occurred_at']=stamp
        events.append(item)
    result=build_profiles([song(i) for i in range(1,5)],events=events,session_id='active',now=now)
    assert result['long_term']=={}
    assert result['session']=={4:math.sqrt(2)}


def test_meaningful_recent_order_uses_playback_time_not_receipt_order():
    now=10000000.
    events=[event('a',1,now,'active',60000),event('b',2,now-10,'active',60000),event('c',3,now,'active',10000)]
    events[0]['payload']['occurred_at']=datetime.fromtimestamp(now-100,timezone.utc).isoformat()
    result=build_profiles([song(i) for i in range(1,4)],events=events,session_id='active',now=now)
    assert result['recent_track_ids']==[2,1]
