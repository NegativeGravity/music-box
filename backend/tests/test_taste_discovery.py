import numpy as np
from app.taste_discovery import discover


def catalog():
    vectors=[[1.,0.],[.99,.1],[-1.,0.],[-.99,.1],[.95,.2],[-.95,.2],[0.,1.]]
    return [dict(id=i+1,title=f'Track {i+1}',artist=f'Artist {i}',genre=None,source='catalog',embedding_model='fixture',embedding=v,duration=200,playback_status='ready') for i,v in enumerate(vectors)]


def profiles(long=None, recent=None, order=None):
    return dict(long_term=long or {},session=recent or {},recent_track_ids=order or [],evidence={})


def test_opposing_interests_stay_separate_and_mixes_partition_the_catalog():
    result=discover(catalog(),profiles({1:2.,2:2.,3:2.,4:2.}))
    groups=result['interest_groups']
    assert len(groups)==2
    assert {frozenset(g['seed_track_ids']) for g in groups}=={frozenset([1,2]),frozenset([3,4])}
    ids=[t['id'] for g in groups for t in g['items']]
    assert len(ids)==len(set(ids))==7
    assert result['candidate_count']==result['group_scored_count']==7
    assert all('embedding' not in t for t in result['items'])


def test_next_track_follows_recent_context_despite_a_dominant_opposing_history():
    data=catalog()
    result=discover(data,profiles({3:30.,4:30.},{1:1.},[1]),current_id=1)
    assert result['next_items'][0]['id']==2
    assert result['next_scored_count']==6
    assert 1 not in {t['id'] for t in result['next_items']}
    changed=discover(data,profiles({1:30.},{3:1.},[3]),current_id=3)
    assert changed['next_items'][0]['id']==4


def test_cold_start_has_no_invented_interests_and_dislikes_and_demos_are_excluded():
    data=catalog()+[{**catalog()[0],'id':99,'source':'demo'}]
    result=discover(data,profiles(),dislikes=[2])
    assert result['interest_groups']==[]
    assert result['next_strategy']=='cold_start'
    assert result['candidate_count']==6
    assert not {2,99} & {t['id'] for t in result['items']}
    assert result==discover(data,profiles(),dislikes=[2])


def test_incompatible_and_corrupt_audio_are_safe_and_do_not_enter_an_interest():
    data=catalog()
    data[1]['embedding_model']='other'
    data[2]['embedding']=[10**400,1]
    data[3]['embedding']=[0.,1.,0.]
    result=discover(data,profiles({1:2.,2:2.,3:2.,4:2.}),current_id=1)
    assert result['invalid_embedding_count']==1
    assert len(result['interest_groups'])==3
    assert all(len({data[i-1]['embedding_model'] for i in g['seed_track_ids']})==1 for g in result['interest_groups'])
    assert all(np.isfinite(t['score']) for t in result['next_items'])


def test_recent_cooldown_avoids_repeats_and_relaxes_for_a_tiny_catalog():
    result=discover(catalog(),profiles({}, {1:1.,2:1.,5:1.},[1,2,5]),current_id=1)
    assert result['next_items'][0]['id'] not in {1,2,5}
    tiny=discover(catalog()[:2],profiles({}, {1:1.,2:1.},[1,2]),current_id=1)
    assert tiny['next_items'][0]['id']==2
    empty=discover(catalog()[:1],profiles(),current_id=1)
    assert empty['next_items']==[]


def test_cold_start_without_audio_still_respects_meaningful_recent_cooldown():
    data=catalog()[:5]
    for track in data:track['embedding']=None
    result=discover(data,profiles({}, {1:1.,2:1.,3:1.},[1,2,3]),current_id=1)
    assert {t['id'] for t in result['next_items']}=={4,5}


def test_cold_start_with_audio_preserves_audio_diversity_after_cooldown_filtering():
    result=discover(catalog(),profiles())
    assert all(t['engine']=='fixture' for t in result['next_items'])
    assert any(t['score_components']['diversity_penalty']>0 for t in result['next_items'])
