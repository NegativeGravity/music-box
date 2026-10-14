import unittest
from app.recommendation import recommend


def tracks():
    return [dict(id=i+1, genre='Hip-Hop' if i<30 else 'Electronic', artist=f'artist-{i}', embedding=[1., i*.001, .1], embedding_model='fixture', playback_status='ready') for i in range(50)]


class RecommendationBehavior(unittest.TestCase):
    def test_balanced_has_exact_quota_when_inventory_sufficient(self):
        r=recommend(tracks(), genre='Hip-Hop', mode='balanced', limit=20, random_seed=42)
        self.assertEqual(len(r['items']),20)
        self.assertEqual(sum(t['genre']=='Hip-Hop' for t in r['items']),16)
        self.assertEqual(r['actual_exploration_rate'],.2)

    def test_focus_and_dislike_exclusions(self):
        r=recommend(tracks(), genre='Hip-Hop', mode='focus', limit=20, dislikes=[1,2], likes=[3], random_seed=42)
        self.assertTrue(all(t['genre']=='Hip-Hop' for t in r['items']))
        self.assertFalse({1,2,3}&{t['id'] for t in r['items']})

    def test_audio_similar_crosses_category(self):
        data=[dict(id=1,genre='Hip-Hop',artist='A',embedding=[1.,0.],embedding_model='fixture'),dict(id=2,genre='Electronic',artist='B',embedding=[.99,.01],embedding_model='fixture'),dict(id=3,genre='Hip-Hop',artist='C',embedding=[0.,1.],embedding_model='fixture')]
        r=recommend(data, surface='similar', query_id=1, limit=2)
        self.assertEqual(r['items'][0]['id'],2)
        self.assertNotIn(1,[t['id'] for t in r['items']])

    def test_shortage_report_and_cold_start_reason(self):
        r=recommend(tracks()[:1],genre='Hip-Hop',limit=20)
        self.assertEqual(r['returned_count'],1)
        self.assertIsNotNone(r['shortage_reason'])
        self.assertEqual(r['items'][0]['reason_code'],'preferred_genre')

    def test_unrelated_category_is_not_a_curated_neighbor(self):
        data=tracks()
        for t in data[30:]:t['genre']='Folk'
        r=recommend(data,genre='Hip-Hop',limit=20)
        self.assertTrue(all(t['genre']=='Hip-Hop' for t in r['items']))

    def test_similar_uses_cosine_without_genre_bonus(self):
        data=[dict(id=1,genre='Hip-Hop',artist='A',embedding=[1.,0.],embedding_model='fixture'),dict(id=2,genre='Electronic',artist='B',embedding=[1.,0.],embedding_model='fixture'),dict(id=3,genre='Hip-Hop',artist='C',embedding=[.8,.6],embedding_model='fixture')]
        r=recommend(data,surface='similar',query_id=1,limit=2)
        self.assertEqual(r['items'][0]['id'],2)

    def test_likes_change_ranking_with_fixed_seed(self):
        data=[dict(id=i+1,genre='Hip-Hop',artist=str(i),embedding=v,embedding_model='fixture') for i,v in enumerate([[1.,0.],[0.,1.],[.99,.01],[.01,.99]])]
        left=recommend(data,genre='Hip-Hop',mode='focus',likes=[1],random_seed=42,limit=2)
        right=recommend(data,genre='Hip-Hop',mode='focus',likes=[2],random_seed=42,limit=2)
        self.assertEqual(left['items'][0]['id'],3)
        self.assertEqual(right['items'][0]['id'],4)


if __name__=='__main__': unittest.main()
