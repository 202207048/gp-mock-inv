"""Profile persistence and existing community author synchronization."""
from fastapi import Depends
from test_community_attachments import CommunityAttachmentsTests, PNG
from app.database import get_db
from app.models import User
from app.routers.auth import router as auth_router
from app.utils.deps import get_current_user


class ProfileTests(CommunityAttachmentsTests):
    def setUp(self):
        super().setUp()
        self.client.app.include_router(auth_router, prefix='/users')

        def current_user(db=Depends(get_db)):
            return db.get(User, self.user_id)

        self.client.app.dependency_overrides[get_current_user] = current_user

    def test_profile_persists_and_updates_existing_posts_comments_and_quotes(self):
        pid = self.create().json()['post_id']
        self.client.post(f'/community/posts/{pid}/comments', json={'content': 'comment'})
        quote_id = self.create(quote_post_id=pid).json()['post_id']
        response = self.client.put('/users/me', json={'user_name': ' New name ', 'profile_image': PNG})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['user_name'], 'New name')
        self.assertNotIn('passwd', response.json())
        self.assertEqual(self.client.get('/users/me').json()['profile_image'], PNG)
        detail = self.client.get(f'/community/posts/{pid}').json()
        listing = self.client.get('/community/posts').json()['items']
        for author in [detail, detail['comments'][0], *listing]:
            self.assertEqual(author['author_name'], 'New name')
            self.assertEqual(author['author_profile_image'], PNG)
        quote = self.client.get(f'/community/posts/{quote_id}').json()['attachments']['quote']
        self.assertEqual(quote['author_name'], 'New name')
        with self.Session() as db:
            self.assertEqual(db.get(User, 2).user_name, 'User 2')
        self.client.put('/users/me', json={'user_name': 'Another'})
        self.assertEqual(self.client.get('/users/me').json()['profile_image'], PNG)
        self.client.put('/users/me', json={'user_name': 'Another', 'profile_image': None})
        self.assertIsNone(self.client.get(f'/community/posts/{pid}').json()['author_profile_image'])

    def test_invalid_profiles_do_not_change_saved_values(self):
        for fields in [{'user_name': ' '}, {'user_name': 'a' * 51},
                       {'user_name': 'Name', 'profile_image': 'data:image/svg+xml;base64,PHN2Zz4='},
                       {'user_name': 'Name', 'profile_image': 'data:image/png;base64,YWJj'},
                       {'user_name': 'Name', 'profile_image': PNG + 'A' * 1400000}]:
            self.assertEqual(self.client.put('/users/me', json=fields).status_code, 422)
        self.assertEqual(self.client.get('/users/me').json()['user_name'], 'User 1')

    def test_authentication_required(self):
        del self.client.app.dependency_overrides[get_current_user]
        self.assertIn(self.client.put('/users/me', json={'user_name': 'Name'}).status_code, (401, 403))
