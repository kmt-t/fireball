import unittest
import tempfile
from pathlib import Path
from tools.common.db import DocAuditDB

class TestCommonDB(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_audit.db"
        self.db = DocAuditDB(db_path=self.db_path)

    def tearDown(self):
        self.db.close() if hasattr(self.db, 'close') else None
        self.temp_dir.cleanup()

    def test_database_initialization(self):
        # Verify database tables exist
        cursor = self.db.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        self.assertIn("keywords", tables)
        self.assertIn("glossary", tables)
        self.assertIn("sections", tables)

    def test_insert_and_query_keyword(self):
        with self.db.conn:
            self.db.conn.execute(
                "INSERT INTO keywords (keyword, description, priority, category) VALUES (?, ?, ?, ?)",
                ("REQ_TEST_001", "Test keyword description", "High", "System")
            )
        cursor = self.db.conn.cursor()
        cursor.execute("SELECT keyword, description FROM keywords WHERE keyword = ?", ("REQ_TEST_001",))
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "REQ_TEST_001")
        self.assertEqual(row[1], "Test keyword description")

if __name__ == "__main__":
    unittest.main()
