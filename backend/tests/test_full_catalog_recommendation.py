import numpy as np
import json
from app.recommendation_v2 import recommend


def catalog(count=30):
    return [dict(id=i+1,title=f'Track {i+1}',artist=f'Artist {i%7}',genre='International' if i%2 else 'Pop',source='catalog',embedding_model='fixture-v1',embedding=[float(np.cos(i*.2)),float(np.sin(i*.2)),.3],playback_status='ready') for i in range(count)]


def test_entire_catalog_is_scored_even_when_selected_genre_is_absent():
    result=recommend(catalog(),genre='Electronic',mode='balanced',limit=20,random_seed=42)
    assert result['returned_count']==20
    assert result['candidate_count']==30
    assert result['scored_count']==30
    assert len({t['id'] for t in result['items']})==20


def test_long_and_recent_profiles_both_contribute():
    data=catalog(8)
    result=recommend(data,likes=[1],session=[5],mode='focus',limit=8)
    assert result['profile_weights']=={'long_term':.7,'session':.3}
    assert result['profile_counts']['long_term']==1
    assert result['profile_counts']['session']==1
    assert all(t['score_components']['long_term'] is not None and t['score_components']['session'] is not None for t in result['items'])
    changed=recommend(data,likes=[1],session=[8],mode='focus',limit=8)
    left={t['id']:t['score_components']['session'] for t in result['items']}
    right={t['id']:t['score_components']['session'] for t in changed['items']}
    assert left!=right


def test_familiar_tracks_and_single_artist_do_not_empty_the_slate():
    data=catalog(25)
    for t in data:t['artist']='Unknown artist';t['genre']=None
    result=recommend(data,likes=list(range(1,26)),mode='focus',limit=20)
    assert result['returned_count']==20
    assert result['candidate_count']==25


def test_cold_start_is_labeled_diverse_and_reproducible():
    result=recommend(catalog(),genre='missing',random_seed=12,limit=10)
    again=recommend(catalog(),genre='missing',random_seed=12,limit=10)
    assert result['strategy']=='cold_start_diverse_audio'
    assert [t['id'] for t in result['items']]==[t['id'] for t in again['items']]
    assert all(t['reason_code']=='cold_start_diversity' for t in result['items'])


def test_similar_is_direct_cosine_and_dislikes_are_excluded():
    data=[dict(id=i+1,title=str(i),artist=str(i),genre=g,source='catalog',embedding_model='fixture',embedding=v) for i,(g,v) in enumerate([('Pop',[1.,0.]),('International',[1.,0.]),('Pop',[.8,.6]),('Rock',[0.,1.])])]
    result=recommend(data,surface='similar',query_id=1,dislikes=[4],limit=3)
    assert [t['id'] for t in result['items']]==[2,3]
    assert result['items'][0]['score_components']['audio']==1.


def test_malformed_and_incompatible_vectors_are_not_blended():
    data=catalog(5)
    data[1]['embedding_model']='different-model'
    data[2]['embedding']=[float('nan'),1.,0.]
    data[3]['embedding']=[0.,0.,0.]
    data[4]['embedding']=[1.,0.]
    result=recommend(data,likes=[1],limit=5)
    assert result['candidate_count']==5
    assert result['invalid_embedding_count']==2
    by_id={t['id']:t for t in result['items']}
    assert by_id[2]['score_components']['long_term'] is None
    assert by_id[5]['score_components']['long_term'] is None
    assert all(np.isfinite(t['score']) for t in result['items'])


def test_numeric_overflow_in_one_vector_does_not_abort_the_catalog():
    data=catalog(5)
    data[2]['embedding']=[10**400,0.,1.]
    result=recommend(data,likes=[1],limit=5)
    assert result['scored_count']==5
    assert result['invalid_embedding_count']==1
    assert all(np.isfinite(t['score']) for t in result['items'])


def test_json_snapshot_profile_weights_replay_the_same_ranking():
    profiles={'long_weights':{1:2.},'session_weights':{5:1.}}
    result=recommend(catalog(8),**profiles)
    replay=recommend(catalog(8),**json.loads(json.dumps(profiles)))
    assert replay==result


def test_similar_without_query_audio_is_labeled_as_metadata_fallback():
    data=catalog(5)
    data[0]['embedding']=None
    result=recommend(data,surface='similar',query_id=1,limit=4)
    assert result['strategy']=='similar_metadata_fallback'
    assert result['engine']=='metadata'


def test_similar_with_only_incompatible_audio_uses_metadata_without_profile_weights():
    data=catalog(3)
    data[0]['embedding_model']='query-only-model'
    result=recommend(data,surface='similar',query_id=1,likes=[2],session=[3],limit=2)
    assert result['strategy']=='similar_metadata_fallback'
    assert result['profile_weights']=={'long_term':0.,'session':0.}
    assert [t['id'] for t in result['items']]==[3,2]
    assert result['items'][0]['score_components']['metadata_bonus']>0
