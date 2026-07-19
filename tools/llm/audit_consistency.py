import csv
import json
import re
import textwrap
from pathlib import Path
from tools.common.db import db
from tools.common.llm import call_llm, parse_llm_markdown_response
from tools.common.parser import parse_sections

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REQUIREMENT_FILE = REPO_ROOT / "docs" / "requires" / "requirement_list.md"
CONFIG_DIR = REPO_ROOT / "tools" / "config"
MATRIX_CSV = CONFIG_DIR / "review_matrix.csv"

def get_section_content(file_path_str: str, heading_str: str, file_cache: dict) -> str:
    """Helper to parse files and extract target section body content."""
    if file_path_str not in file_cache:
        file_path = REPO_ROOT / file_path_str
        if not file_path.exists():
            return ""
        try:
            file_cache[file_path_str] = {sec.heading: sec for sec in parse_sections(file_path)}
        except Exception:
            return ""
    
    sections_map = file_cache[file_path_str]
    if heading_str in sections_map:
        return sections_map[heading_str].body
    return ""

def audit_cell(aspect: str, file_path: str, heading: str, content: str, keywords: list[str], 
               req_dict: dict, backend: str, model: str, max_tokens: int) -> dict:
    """Runs LLM audit for a specific aspect of a section."""
    
    # 1. Build prompt based on aspect
    if aspect == "policy_P01":
        prompt = textwrap.dedent(f"""\
        あなたはFireballプロジェクトのリードアーキテクトです。
        以下の設計書セクションが、プロジェクトのメモリ制約ポリシー「動的メモリ確保（malloc/new/std::vector等の動的コンテナ）の禁止、静的アロケーションのみの使用」に適合しているか検証してください。
        
        【検証対象セクション】
        ファイル: {file_path}
        見出し: {heading}
        本文:
        ---
        {content}
        ---
        
        【判定基準】
        - 明示的または暗示的に動的メモリ確保（malloc, new, delete, heap等）を使用・推奨している記述がある場合は FAIL。
        - C++ 標準ライブラリの動的コンテナ（std::vector, std::list, std::stringなど）を静的確保以外の用途で使おうとしている記述がある場合は FAIL。
        - 静的メモリ確保（static配列、配置newによる事前確保領域など）を正しく推奨・使用している場合は PASS。
        - 判断の記述が薄い、または判断に迷う箇所（例：「メモリを確保する」とだけ書かれており静的か動的か不明な場合）は WARN。
        - メモリ確保に関連する記述そのものが本質的にない場合は PASS（例外的な扱いをする必要はありません）。
        """)
        
    elif aspect == "policy_P02":
        prompt = textwrap.dedent(f"""\
        あなたはFireballプロジェクトのリードアーキテクトです。
        以下の設計書セクションが、プロジェクトの例外制限ポリシー「例外処理（try/catch/throw）の禁止、RTTI（dynamic_cast/typeid）の禁止、および std::expected 等の値返しによるエラーハンドリング推奨」に適合しているか検証してください。
        
        【検証対象セクション】
        ファイル: {file_path}
        見出し: {heading}
        本文:
        ---
        {content}
        ---
        
        【判定基準】
        - try/catch/throw や例外オブジェクトの使用・言及がある場合は FAIL。
        - RTTI（dynamic_cast, typeid）の使用・言及がある場合は FAIL。
        - エラー処理を例外ではなく、値返し（std::expected, std::optional, エラーコード等）で実装・規定している記述がある場合は PASS。
        - どちらとも言えない記述（例：「エラーをスローする」という記述があるが、文脈上例外なのか単なるエラー返却なのか曖昧な場合）は WARN。
        """)
        
    elif aspect == "review_traceability":
        # Look up corresponding requirements and other sections sharing the same keywords (Cross-Cutting & Group Audit)
        req_texts = []
        shared_sections_text = []
        cursor = db.conn.cursor()
        
        for kw in keywords:
            if kw in req_dict:
                req_texts.append(f"- {{{kw}}}: {req_dict[kw]}")
            
            # Query other sections sharing the same keyword to audit mutual consistency
            cursor.execute("""
                SELECT s.file_path, s.heading, s.body_content
                FROM keyword_sections k
                JOIN sections s ON s.file_path = k.file_path AND s.heading = k.heading
                WHERE k.keyword = ?
            """, (kw,))
            
            shared = cursor.fetchall()
            if shared:
                has_others = False
                temp_texts = []
                for s_file, s_heading, s_body in shared:
                    if s_file == file_path and s_heading == heading:
                        continue
                    has_others = True
                    snippet = s_body
                    if len(snippet) > 800:
                        snippet = snippet[:800] + "\n...(以下省略)"
                    temp_texts.append(f"  - 【ファイル】: {s_file} | 【見出し】: {s_heading}\n  - 【本文】:\n{textwrap.indent(snippet, '    ')}\n")
                
                if has_others:
                    shared_sections_text.append(f"■ キーワード {{{kw}}} を共有する他の設計セクション群:")
                    shared_sections_text.extend(temp_texts)
        
        req_context = "\n".join(req_texts)
        group_context = "\n".join(shared_sections_text) if shared_sections_text else "（他に関連する下位セクションはありません）"
        
        prompt = textwrap.dedent(f"""\
        あなたはFireballプロジェクトの整合性検証チェッカーです。
        以下の設計書セクション（下位仕様）と、そこに紐付く最上位の要求仕様（要求キーワード定義）、および同じキーワードを共有する他の下位セクションの設計記述を比較し、システム全体での論理的な整合性（横串の一貫性・グループ内の一貫性）を検証してください。
        
        【最上位要求（キーワード定義）】
        {req_context}
        
        【検証対象の下位仕様セクション】
        ファイル: {file_path}
        見出し: {heading}
        本文:
        ---
        {content}
        ---
        
        【同一キーワードを共有する他の設計セクション群（相互一貫性検証対象）】
        {group_context}
        
        【判定基準】
        1. **最上位要求との整合性**:
           - 最上位要求の定義（制約や前提）と、検証対象セクションの間で論理的な矛盾や直接の衝突がある場合は FAIL。
        2. **他コンポーネントとの相互整合性 (横串一貫性)**:
           - 「同一キーワードを共有する他の設計セクション」と「検証対象セクション」を比較し、論理的な矛盾や相反する設計（例：片方ではメモリを静的に切り出すと言いながら、もう片方では動的バッファに追記する等、ポリシーが分裂しているケース）がないか確認してください。
           - 明確なポリシー分裂や設計上の衝突が検出された場合は FAIL と判定してください。
        3. **その他**:
           - 単に詳細度の差、実装バリアントの違い、または追加の設計詳細があるだけの場合は PASS としてください。
           - 記述が薄く、一貫性や矛盾の有無が判断しづらい場合は WARN と判定してください（出力フォーマットに従い status は WARN または UNCERTAIN にしてください）。
        """)
        
    elif aspect == "review_quality":
        prompt = textwrap.dedent(f"""\
        あなたは設計書の記述品質をチェックするシニア査読者です。
        以下の設計書セクションについて、意味の曖昧さ、自己矛盾、Todoなどのプレースホルダーの放置、カプセル化の破綻などの記述品質リスクを監査してください。
        
        【検証対象セクション】
        ファイル: {file_path}
        見出し: {heading}
        本文:
        ---
        {content}
        ---
        
        【判定基準】
        - セクション内に明らかな自己矛盾（前後の説明の食い違い）や、未決事項（TBD, TODO, ［要検討］等の表記）が放置されている場合は FAIL。
        - 実装詳細がインターフェース境界を超えて漏れ出している（カプセル化の破綻）場合は FAIL。
        - 表現が著しく曖昧で、開発者が誤解するリスクが高い記述は WARN。
        - 整理されており、論理的に破綻がない場合は PASS。
        """)
        
    elif aspect == "review_api":
        prompt = textwrap.dedent(f"""\
        あなたはAPIコーディネーターです。
        以下の設計書セクションに記述されているAPI定義（関数、クラス、構造体等）が、プロジェクトの命名・設計規則「公開APIは fireball 名前空間に配置し、C++ APIは 2スペースインデント、snake_case 基本」に適合しているか検証してください。
        ※ただし、C++のマクロ定義（#define）や定数（constexpr等）は SCREAMING_SNAKE_CASE で記述されることが正しく、例外として適合（PASS）と判定してください。
        
        【検証対象セクション】
        ファイル: {file_path}
        見出し: {heading}
        本文:
        ---
        {content}
        ---
        
        【判定基準】
        - 公開API名が camelCase であったり、`fireball` 名前空間の外に定義されている場合は FAIL（ただし、マクロや定数が SCREAMING_SNAKE_CASE である場合は適合とみなし、FAIL判定にしてはなりません）。
        - インデントが著しく崩れている、または命名規則に矛盾がある場合は FAIL。
        - コードスニペットがない、またはAPI定義そのものに言及がない場合は PASS。
        - 規則に適合している場合は PASS。
        """)
    else:
        return {"status": "PASS", "reason": "Unknown aspect"}

    # 2. Call LLM
    raw_response = call_llm(prompt, backend=backend, model=model, max_tokens=max_tokens)
    res = parse_llm_markdown_response(raw_response)
    
    if not res or "status" not in res:
        # Fallback parsing
        status = "FAIL"
        if "STATUS: PASS" in raw_response: status = "PASS"
        elif "STATUS: WARN" in raw_response or "STATUS: UNCERTAIN" in raw_response: status = "UNCERTAIN"
        res = {"status": status, "reason": "Failed to parse standard response layout."}
        
    return res

def run_matrix_audit(backend: str = None, model: str = None, max_tokens: int = 1024) -> int:
    """Main runner for review-matrix-based LLM checks."""
    print("Loading review matrix from Database...")
    matrix = db.load_review_matrix()
    if not matrix:
        print("Warning: Review matrix is empty in database.")
        return 0

    req_dict = db.load_requirement_keywords_dict()
    file_cache = {}
    
    # Identify target fields to audit
    aspect_fields = ["policy_P01", "policy_P02", "review_traceability", "review_quality", "review_api"]
    
    total_audits = 0
    failures = 0
    updates_count = 0
    
    # Gather all PENDING cells
    pending_tasks = []
    for row in matrix:
        for aspect in aspect_fields:
            if row.get(aspect) == "PENDING":
                pending_tasks.append((row, aspect))
                
    total_tasks = len(pending_tasks)
    print(f"Found {total_tasks} pending audit tasks in review matrix.")
    if total_tasks == 0:
        print("All checks are up-to-date.")
        return 0
        
    for idx, (row, aspect) in enumerate(pending_tasks, 1):
        file_path = row["file_path"]
        heading = row["heading"]
        kws_str = row.get("keywords", "")
        keywords = [k.strip() for k in kws_str.split(",") if k.strip()]
        
        print(f"[{idx}/{total_tasks}] Auditing {aspect} for {file_path} #{heading}...")
        
        # Get section content
        content = get_section_content(file_path, heading, file_cache)
        if not content.strip():
            print("  -> Skipped: Section body is empty.")
            row[aspect] = "N/A"
            updates_count += 1
            continue
            
        # Calculate cache hash key
        content_hash = db.make_hash_key(content, kws_str)
        hash_key = db.make_hash_key("MATRIX-AUDIT", file_path, heading, aspect, content_hash)
        
        # Cache check
        cached = db.get_cache(hash_key)
        if cached is not None:
            status = cached["status"]
            print(f"  -> Cached ({status})")
            row[aspect] = status
            if status == "FAIL":
                failures += 1
            updates_count += 1
            continue
            
        # Perform LLM call
        try:
            res = audit_cell(
                aspect=aspect,
                file_path=file_path,
                heading=heading,
                content=content,
                keywords=keywords,
                req_dict=req_dict,
                backend=backend,
                model=model,
                max_tokens=max_tokens
            )
            status = res.get("status", "FAIL")
            if status not in ["PASS", "FAIL", "UNCERTAIN"]:
                status = "FAIL"
                
            reason = res.get("reason", "")
            suggestions = res.get("suggestions", "")
            
            print(f"  -> {status}: {reason[:100]}...")
            
            row[aspect] = status
            if status == "FAIL":
                failures += 1
                
            # Set cache
            db.set_cache(
                hash_key=hash_key,
                rule_code=aspect.upper(),
                target_type="matrix_cell",
                file_path=file_path,
                heading=heading,
                status=status,
                reason=reason,
                suggestions=suggestions,
                input_hash=content_hash
            )
            updates_count += 1
            total_audits += 1
            
        except Exception as e:
            print(f"  -> ERROR during audit: {e}")
            row[aspect] = "ERROR"
            
    # Sync matrix back to DB & CSV
    if updates_count > 0:
        print("Synchronizing updated review matrix back to Database...")
        
        # Mark row as llm_checked if all aspect columns are non-PENDING
        for row in matrix:
            is_checked = 1
            for aspect in aspect_fields:
                if row.get(aspect) == "PENDING":
                    is_checked = 0
                    break
            row["llm_checked"] = is_checked
            
        db.sync_review_matrix(matrix)
        
        # Rewrite CSV
        try:
            MATRIX_CSV.parent.mkdir(parents=True, exist_ok=True)
            with open(MATRIX_CSV, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=[
                    "file_path", "heading", "keywords", 
                    "policy_P01", "policy_P02", 
                    "review_traceability", "review_quality", "review_api", 
                    "llm_checked"
                ])
                writer.writeheader()
                writer.writerows(matrix)
            print(f"Updated CSV: {MATRIX_CSV}")
        except Exception as e:
            print(f"Warning: Failed to rewrite review_matrix.csv: {e}")
            
    print(f"Audit completed: {total_audits} API calls, {failures} failures detected.")
    return failures
