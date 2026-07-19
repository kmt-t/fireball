import sqlite3
import hashlib
import datetime
from pathlib import Path

# Paths
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DB_FILE = REPO_ROOT / "temp" / "doc_audit.db"

class DocAuditDB:
    def __init__(self, db_path: Path = DB_FILE):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.create_tables()

    def create_tables(self):
        cursor = self.conn.cursor()
        try:
            cursor.execute("PRAGMA table_info(audit_results)")
            columns = [row[1] for row in cursor.fetchall()]
            if columns and "rule_code" not in columns:
                with self.conn:
                    self.conn.execute("DROP TABLE audit_results")
        except Exception:
            pass

        try:
            cursor.execute("PRAGMA table_info(keywords)")
            columns = [row[1] for row in cursor.fetchall()]
            if columns and "is_global" not in columns:
                with self.conn:
                    self.conn.execute("DROP TABLE keywords")
        except Exception:
            pass

        with self.conn:
            # 1. keywords table
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS keywords (
                    keyword TEXT PRIMARY KEY,
                    description TEXT,
                    priority TEXT,
                    verification_method TEXT,
                    category TEXT,
                    is_meta INTEGER DEFAULT 0,
                    is_global INTEGER DEFAULT 0
                )
            """)
            # 2. glossary table
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS glossary (
                    term TEXT PRIMARY KEY,
                    definition TEXT
                )
            """)
            # 3. sections table
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS sections (
                    file_path TEXT,
                    heading TEXT,
                    level INTEGER,
                    keywords TEXT,
                    body_content TEXT,
                    content_hash TEXT,
                    PRIMARY KEY (file_path, heading)
                )
            """)
            # 4. spec_matrix table
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS spec_matrix (
                    file_path TEXT,
                    keyword TEXT,
                    is_present INTEGER,
                    PRIMARY KEY (file_path, keyword)
                )
            """)
            # 5. traceability_matrix table
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS traceability_matrix (
                    file_path TEXT,
                    heading TEXT,
                    keyword TEXT,
                    is_satisfied INTEGER,
                    PRIMARY KEY (file_path, heading, keyword)
                )
            """)

            # 7. audit_results cache table (New schema with rule_code)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_results (
                    hash_key TEXT PRIMARY KEY,
                    rule_code TEXT,
                    target_type TEXT,
                    file_path TEXT,
                    heading TEXT,
                    status TEXT,
                    reason TEXT,
                    suggestions TEXT,
                    input_hash TEXT,
                    updated_at TEXT
                )
            """)
            # 8. heading_dictionary table
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS heading_dictionary (
                    identifier TEXT PRIMARY KEY,
                    translation TEXT
                )
            """)
            # 9. complex_patterns table
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS complex_patterns (
                    pattern TEXT PRIMARY KEY,
                    replacement TEXT
                )
            """)

            # 11. document_tiers table
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS document_tiers (
                    file_path TEXT PRIMARY KEY,
                    tier INTEGER,
                    parent_file TEXT
                )
            """)
            # 12. keyword_sections table
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS keyword_sections (
                    keyword TEXT,
                    file_path TEXT,
                    heading TEXT,
                    line_start INTEGER,
                    PRIMARY KEY (keyword, file_path, heading)
                )
            """)
            # 13. review_matrix table
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS review_matrix (
                    file_path TEXT,
                    heading TEXT,
                    keywords TEXT,
                    policy_P01 TEXT DEFAULT 'PENDING',
                    policy_P02 TEXT DEFAULT 'PENDING',
                    review_traceability TEXT DEFAULT 'PENDING',
                    review_quality TEXT DEFAULT 'PENDING',
                    review_api TEXT DEFAULT 'PENDING',
                    llm_checked INTEGER DEFAULT 0,
                    PRIMARY KEY (file_path, heading)
                )
            """)


    def get_cache(self, hash_key: str) -> dict | None:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT status, reason, suggestions FROM audit_results WHERE hash_key = ?",
                (hash_key,)
            )
            row = cursor.fetchone()
            if row:
                return {
                    "status": row[0],
                    "reason": row[1],
                    "suggestions": row[2]
                }
        except Exception:
            pass
        return None

    def set_cache(self, hash_key: str, rule_code: str, target_type: str, file_path: str, heading: str,
                  status: str, reason: str, suggestions: str, input_hash: str):
        # Defensively convert lists/dicts to strings to prevent SQLite binding errors
        if isinstance(suggestions, list):
            suggestions = "\n".join(str(s) for s in suggestions)
        elif not isinstance(suggestions, str):
            suggestions = str(suggestions)

        if isinstance(reason, list):
            reason = "\n".join(str(r) for r in reason)
        elif not isinstance(reason, str):
            reason = str(reason)

        rule_code = str(rule_code)
        target_type = str(target_type)
        file_path = str(file_path)
        heading = str(heading)
        status = str(status)
        input_hash = str(input_hash)

        now = datetime.datetime.now().isoformat()
        with self.conn:
            self.conn.execute("""
                INSERT OR REPLACE INTO audit_results 
                (hash_key, rule_code, target_type, file_path, heading, status, reason, suggestions, input_hash, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (hash_key, rule_code, target_type, file_path, heading, status, reason, suggestions, input_hash, now))

    def make_hash_key(self, *args) -> str:
        hasher = hashlib.sha256()
        for arg in args:
            hasher.update(str(arg).encode("utf-8"))
        return hasher.hexdigest()

    def sync_keywords(self, keywords_data: list[dict]):
        with self.conn:
            for kw in keywords_data:
                self.conn.execute("""
                    INSERT OR REPLACE INTO keywords (keyword, description, priority, verification_method, category, is_meta, is_global)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (kw["keyword"], kw["description"], kw.get("priority", ""), kw.get("verification_method", ""), kw.get("category", ""), kw.get("is_meta", 0), kw.get("is_global", 0)))

    def load_global_keywords(self) -> set[str]:
        cursor = self.conn.cursor()
        try:
            cursor.execute("SELECT keyword FROM keywords WHERE is_global = 1")
            return {row[0] for row in cursor.fetchall()}
        except Exception:
            return set()

    def sync_glossary(self, glossary_data: list[dict]):
        with self.conn:
            for g in glossary_data:
                self.conn.execute("""
                    INSERT OR REPLACE INTO glossary (term, definition)
                    VALUES (?, ?)
                """, (g["term"], g["definition"]))

    def load_defined_keywords(self) -> set[str]:
        cursor = self.conn.cursor()
        try:
            cursor.execute("SELECT keyword FROM keywords")
            return {row[0] for row in cursor.fetchall()}
        except Exception:
            return set()

    def load_requirement_keywords_dict(self) -> dict[str, str]:
        """Loads non-meta keywords (including global): keyword -> description"""
        cursor = self.conn.cursor()
        try:
            cursor.execute("SELECT keyword, description FROM keywords WHERE is_meta = 0")
            return {row[0]: row[1] for row in cursor.fetchall()}
        except Exception:
            return {}

    def load_meta_keywords(self) -> set[str]:
        cursor = self.conn.cursor()
        try:
            cursor.execute("SELECT keyword FROM keywords WHERE is_meta = 1")
            return {row[0] for row in cursor.fetchall()}
        except Exception:
            return set()

    def update_spec_matrix(self, all_kw: list[str], all_files: list[str], file_kw_map: dict[str, set[str]]):
        with self.conn:
            self.conn.execute("DELETE FROM spec_matrix")
            for fp in all_files:
                kws = file_kw_map.get(fp, set())
                for k in all_kw:
                    self.conn.execute(
                        "INSERT INTO spec_matrix (file_path, keyword, is_present) VALUES (?, ?, ?)",
                        (fp, k, 1 if k in kws else 0)
                    )

    def update_traceability_matrix(self, sections: list, l1_issues_map: dict = None, existing_satisfied: dict = None):
        l1_map = l1_issues_map or {}
        existing = existing_satisfied or {}
        with self.conn:
            self.conn.execute("DELETE FROM traceability_matrix")
            for sec in sections:
                file_path = getattr(sec, 'file_path', getattr(sec, 'file', None))
                if not file_path:
                    continue
                if hasattr(file_path, 'relative_to'):
                    rel_file = str(file_path.relative_to(REPO_ROOT))
                else:
                    rel_file = str(file_path)

                heading = getattr(sec, 'heading', '')
                keywords = getattr(sec, 'keywords', [])
                
                for kw in keywords:
                    is_sat = 1
                    if (rel_file, heading) in l1_map:
                        is_sat = 1 if l1_map[(rel_file, heading)] == "PASS" else 0 if l1_map[(rel_file, heading)] == "FAIL" else 2
                    elif (rel_file, heading, kw) in existing:
                        is_sat = existing[(rel_file, heading, kw)]
                    
                    self.conn.execute("""
                        INSERT OR REPLACE INTO traceability_matrix (file_path, heading, keyword, is_satisfied)
                        VALUES (?, ?, ?, ?)
                    """, (rel_file, heading, kw, is_sat))

    def load_traceability_matrix_satisfied(self) -> dict:
        cursor = self.conn.cursor()
        try:
            cursor.execute("SELECT file_path, heading, keyword, is_satisfied FROM traceability_matrix")
            return {(row[0], row[1], row[2]): row[3] for row in cursor.fetchall()}
        except Exception:
            return {}



    def sync_heading_dictionary(self, dictionary_data: list[dict]):
        with self.conn:
            self.conn.execute("DELETE FROM heading_dictionary")
            for entry in dictionary_data:
                self.conn.execute("""
                    INSERT OR REPLACE INTO heading_dictionary (identifier, translation)
                    VALUES (?, ?)
                """, (entry["identifier"], entry["translation"]))

    def load_heading_dictionary(self) -> dict[str, str]:
        cursor = self.conn.cursor()
        try:
            cursor.execute("SELECT identifier, translation FROM heading_dictionary")
            return {row[0]: row[1] for row in cursor.fetchall()}
        except Exception:
            return {}

    def sync_complex_patterns(self, patterns_data: list[dict]):
        with self.conn:
            self.conn.execute("DELETE FROM complex_patterns")
            for entry in patterns_data:
                self.conn.execute("""
                    INSERT OR REPLACE INTO complex_patterns (pattern, replacement)
                    VALUES (?, ?)
                """, (entry["pattern"], entry["replacement"]))

    def load_complex_patterns(self) -> dict[str, str]:
        cursor = self.conn.cursor()
        try:
            cursor.execute("SELECT pattern, replacement FROM complex_patterns")
            return {row[0]: row[1] for row in cursor.fetchall()}
        except Exception:
            return {}



    def sync_document_tiers(self, tiers_data: list[dict]):
        with self.conn:
            self.conn.execute("DELETE FROM document_tiers")
            for t in tiers_data:
                self.conn.execute("""
                    INSERT OR REPLACE INTO document_tiers (file_path, tier, parent_file)
                    VALUES (?, ?, ?)
                """, (t["file_path"], t["tier"], t.get("parent_file", "")))

    def sync_keyword_sections(self, kw_sec_data: list[dict]):
        with self.conn:
            self.conn.execute("DELETE FROM keyword_sections")
            for ks in kw_sec_data:
                self.conn.execute("""
                    INSERT OR REPLACE INTO keyword_sections (keyword, file_path, heading, line_start)
                    VALUES (?, ?, ?, ?)
                """, (ks["keyword"], ks["file_path"], ks["heading"], ks.get("line_start", 0)))

    def sync_review_matrix(self, matrix_data: list[dict]):
        with self.conn:
            self.conn.execute("DELETE FROM review_matrix")
            for m in matrix_data:
                self.conn.execute("""
                    INSERT OR REPLACE INTO review_matrix 
                    (file_path, heading, keywords, policy_P01, policy_P02, review_traceability, review_quality, review_api, llm_checked)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    m["file_path"],
                    m["heading"],
                    m.get("keywords", ""),
                    m.get("policy_P01", "PENDING"),
                    m.get("policy_P02", "PENDING"),
                    m.get("review_traceability", "PENDING"),
                    m.get("review_quality", "PENDING"),
                    m.get("review_api", "PENDING"),
                    m.get("llm_checked", 0)
                ))

    def load_review_matrix(self) -> list[dict]:
        cursor = self.conn.cursor()
        try:
            cursor.execute("SELECT file_path, heading, keywords, policy_P01, policy_P02, review_traceability, review_quality, review_api, llm_checked FROM review_matrix")
            rows = cursor.fetchall()
            return [
                {
                    "file_path": r[0],
                    "heading": r[1],
                    "keywords": r[2],
                    "policy_P01": r[3],
                    "policy_P02": r[4],
                    "review_traceability": r[5],
                    "review_quality": r[6],
                    "review_api": r[7],
                    "llm_checked": r[8]
                } for r in rows
            ]
        except Exception:
            return []



    def close(self):
        self.conn.close()

# Global database instance
db = DocAuditDB()
