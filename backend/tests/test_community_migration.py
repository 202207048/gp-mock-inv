"""Exercise the attachment migration against an existing posts table."""
import importlib.util
from pathlib import Path
import unittest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


class CommunityMigrationTests(unittest.TestCase):
    def test_upgrade_and_downgrade_preserve_legacy_post(self):
        path = Path(__file__).parents[1] / 'alembic/versions/f20261021_community_attachments.py'
        spec = importlib.util.spec_from_file_location('community_migration', path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        engine = create_engine('sqlite://')
        self.addCleanup(engine.dispose)
        with engine.begin() as conn:
            conn.execute(text('CREATE TABLE users (user_id INTEGER PRIMARY KEY)'))
            conn.execute(text('CREATE TABLE posts (post_id INTEGER PRIMARY KEY, content TEXT NOT NULL)'))
            conn.execute(text("INSERT INTO posts VALUES (1, 'legacy')"))
            migration.op = Operations(MigrationContext.configure(conn))
            migration.upgrade()
            self.assertIn('post_votes', inspect(conn).get_table_names())
            self.assertEqual(conn.execute(text('SELECT content, attachments FROM posts')).one(), ('legacy', None))
            migration.downgrade()
            self.assertNotIn('post_votes', inspect(conn).get_table_names())
            self.assertEqual(conn.execute(text('SELECT content FROM posts')).scalar(), 'legacy')
