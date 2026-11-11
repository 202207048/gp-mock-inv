"""Ownership, validation and related records for post edits/deletion."""
from test_community_attachments import CommunityAttachmentsTests, PNG
from app.models.community import Comment, PostLike, PostVote


class CommunityEditTests(CommunityAttachmentsTests):
    def test_poll_title_optional_on_create_and_edit(self):
        poll = {'question': '전망?', 'options': ['상승', '하락']}
        response = self.client.post('/community/posts', json={'content': '본문', 'poll': poll})
        self.assertEqual(response.status_code, 200, response.text)
        url = f"/community/posts/{response.json()['post_id']}"
        self.assertEqual(self.client.get(url).json()['title'], '')
        self.assertEqual(self.client.put(url, json={'title': ' ', 'content': '수정'}).status_code, 200)
        self.assertEqual(self.client.put(url, json={'title': '', 'content': '수정', 'poll': None}).status_code, 422)
        self.assertEqual(self.client.put(url, json={'title': '일반 글', 'content': '수정', 'poll': None}).status_code, 200)
        self.assertEqual(self.client.post('/community/posts', json={'content': '본문'}).status_code, 422)

    def test_edit_all_attachments_and_reset_only_changed_poll(self):
        poll = {'question': 'Q', 'options': ['A', 'B']}
        pid = self.create(images=[PNG], symbol_code='005930', trade_id=1, poll=poll).json()['post_id']
        url = f'/community/posts/{pid}'
        self.client.put(url + '/vote', json={'option_index': 1})
        base = {'title': 'Edited', 'content': 'Body'}
        response = self.client.put(url, json={**base, 'poll': poll, 'images': []})
        self.assertEqual(response.status_code, 200, response.text)
        data = self.client.get(url).json()
        self.assertEqual(data['attachments']['poll']['counts'], [0, 1])
        self.assertEqual(data['attachments']['images'], [])
        self.assertIn('trade', data['attachments'])
        changed = {'question': 'New', 'options': ['X', 'Y', 'Z']}
        response = self.client.put(url, json={**base, 'poll': changed, 'trade_id': None, 'symbol_code': None, 'images': [PNG]})
        self.assertEqual(response.status_code, 200, response.text)
        data = self.client.get(url).json()
        self.assertEqual(data['attachments']['poll']['counts'], [0, 0, 0])
        self.assertEqual(data['attachments']['images'], [PNG])
        self.assertNotIn('trade', data['attachments'])
        self.assertNotIn('symbol_name', data['attachments'])
        self.assertIsNone(data['symbol_code'])
        response = self.client.put(url, json={**base, 'poll': None, 'trade_id': 1, 'symbol_code': '005930'})
        self.assertEqual(response.status_code, 200, response.text)
        data = self.client.get(url).json()
        self.assertNotIn('poll', data['attachments'])
        self.assertIn('trade', data['attachments'])
        self.assertEqual(data['attachments']['symbol_name'], '삼성전자')

    def test_invalid_attachment_edit_is_atomic(self):
        pid = self.create(images=[PNG], poll={'question': 'Q', 'options': ['A', 'B']}).json()['post_id']
        url = f'/community/posts/{pid}'
        before = self.client.get(url).json()
        for extra in ({'trade_id': 2}, {'trade_id': 3}, {'images': ['bad']},
                      {'poll': {'question': 'Q', 'options': ['A', 'A']}}):
            response = self.client.put(url, json={'title': 'Changed', 'content': 'Changed', **extra})
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(self.client.get(url).json(), before)

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
