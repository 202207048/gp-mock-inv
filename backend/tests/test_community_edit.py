"""Ownership, validation and related records for post edits/deletion."""
from test_community_attachments import CommunityAttachmentsTests, PNG
from app.models.community import Comment, PostLike, PostVote


class CommunityEditTests(CommunityAttachmentsTests):
    def test_update_retains_attachments_and_engagement(self):
        pid = self.create(images=[PNG], poll={'question': 'Q', 'options': ['A', 'B']}).json()['post_id']
        self.client.post(f'/community/posts/{pid}/comments', json={'content': 'Keep'})
        self.client.post(f'/community/posts/{pid}/like')
        self.client.put(f'/community/posts/{pid}/vote', json={'option_index': 0})
        before = self.client.get(f'/community/posts/{pid}').json()
        response = self.client.put(f'/community/posts/{pid}', json={'title': ' Changed ', 'content': ' Updated '})
        self.assertEqual(response.status_code, 200, response.text)
        after = self.client.get(f'/community/posts/{pid}').json()
        self.assertEqual(after['title'], 'Changed')
        self.assertEqual(after['content'], 'Updated')
        for key in ('attachments', 'comments', 'like_count', 'created_at'):
            self.assertEqual(after[key], before[key])
        listing = self.client.get('/community/posts').json()['items'][0]
        self.assertEqual(listing['title'], 'Changed')
        self.assertEqual(listing['user_id'], 1)
        self.assertEqual(after['user_id'], 1)
        self.assertEqual(self.client.delete(f'/community/posts/{pid}').status_code, 200)
        self.assertEqual(self.client.get(f'/community/posts/{pid}').status_code, 404)
        with self.Session() as db:
            for model in (Comment, PostLike, PostVote):
                self.assertEqual(db.query(model).filter(model.post_id == pid).count(), 0)

    def test_foreign_user_cannot_edit_or_delete(self):
        pid = self.create().json()['post_id']
        self.user_id = 2
        self.assertEqual(self.client.put(f'/community/posts/{pid}', json={'title': 'Changed', 'content': 'Changed'}).status_code, 404)
        self.assertEqual(self.client.delete(f'/community/posts/{pid}').status_code, 404)
        self.assertEqual(self.client.get(f'/community/posts/{pid}').json()['user_id'], 1)

    def test_invalid_and_missing_posts(self):
        pid = self.create().json()['post_id']
        for payload in ({'title': ' ', 'content': 'OK'}, {'title': 'OK', 'content': ' '},
                        {'title': 'x' * 201, 'content': 'OK'}, {'title': 'OK', 'content': 'x' * 10001}):
            self.assertEqual(self.client.put(f'/community/posts/{pid}', json=payload).status_code, 422)
        self.assertEqual(self.client.put('/community/posts/999', json={'title': 'OK', 'content': 'OK'}).status_code, 404)
        self.assertEqual(self.client.delete('/community/posts/999').status_code, 404)
