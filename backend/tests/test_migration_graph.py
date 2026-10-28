"""실제 DB 없이 통합 migration graph와 schema 변경 없는 merge를 검증한다."""
import ast
from pathlib import Path
import unittest

from alembic.script import ScriptDirectory


class MigrationGraphTests(unittest.TestCase):
    def setUp(self):
        self.scripts = ScriptDirectory(str(Path(__file__).resolve().parents[1] / "alembic"))

    def test_exactly_one_head(self):
        self.assertEqual(self.scripts.get_heads(), ["h20261028_user_profile"])

    def test_merge_preserves_both_sibling_parents(self):
        merge = self.scripts.get_revision("g20261021_ai_community_merge")
        self.assertEqual(set(merge.down_revision), {
            "f20261021_order_filled_at", "f20261021_community",
        })
        for revision in merge.down_revision:
            self.assertEqual(self.scripts.get_revision(revision).down_revision, "e20261007_misu")

    def test_both_features_reachable_from_head(self):
        history = {revision.revision for revision in self.scripts.walk_revisions()}
        self.assertTrue({"f20261021_order_filled_at", "f20261021_community",
                         "e20261007_misu"}.issubset(history))

    def test_merge_upgrade_and_downgrade_are_noops(self):
        revision = self.scripts.get_revision("g20261021_ai_community_merge")
        tree = ast.parse(Path(revision.path).read_text(encoding="utf-8"))
        for name in ("upgrade", "downgrade"):
            function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
            self.assertEqual(len(function.body), 1)
            self.assertIsInstance(function.body[0], ast.Pass)
