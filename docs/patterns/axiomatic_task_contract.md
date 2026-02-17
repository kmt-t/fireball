# Axiomatic Task Contract (ATC) Pattern

エージェントの認知負荷を最小化し、内部状態をプロジェクト固有の論理空間へ強制的に収束（Collapse）させるための、**一階述語論理（First-Order Logic）**ベースのアサーション・プロトコル。

## 背景

AIエージェントの「自由な推論」は、時としてプロジェクトの重要な制約（例：組み込み環境での仮想関数禁止）を「一般的な正解」として上書き（Drift）してしまう。
準自然言語による指示（例：`@inv: virtualを使わない`）は、エージェントにとって解釈の余地を残すが、記号的な述語（例：`∀f ∈ Fun : ¬virtual(f)`）はエージェントのトークン生成確率を物理的に一点に限定する。

---

## ATC-DSL v2 (Predicate Logic) 記法

タスク開始前に、以下の `atc` ブロックを宣言しなければならない。

### 1. タスク記述の基本論理
```atc
@pre:  <初期状態 $S₀$ において真であるべき命題>
@inv:  <すべての状態 $Sₙ$ において維持されるべき不変条件 P>
@post: <完了状態 $S_final$ において達成されるべき命題 Q>
```

### 2. 標準述語（Project-Specific Predicates）
- `tier(component, N)` : コンポーネントの階層。
- `is_pure_static(comp)` : vtableを一切含まないこと。
- `matches(impl, wit)` : 実装がWITの契約に従っていること。
- `contains(target, pattern)` : 特定のシンボル/パターンの包含。
- `derives_from(spec, req_id)` : 要求キーワード `{req_id}` からの導出。

### 3. 量化子と論理演算子
- `∀` (ALL), `∃` (EXISTS)
- `∧` (AND), `∨` (OR), `¬` (NOT), `⇒` (IMPLIES)

---

## 記述例：Tier 2 インターフェースのリファクタリング

```atc
@pre:  ∃w ∈ WIT : matches(w, docs/components/scheduler.md)
@pre:  is_tier(scheduler, 2)
@inv:  ∀f ∈ funs(scheduler) : ¬virtual(f) ∧ ¬override(f)
@inv:  ∀d ∈ deps(scheduler) : is_template_injected(d)
@post: build_status == SUCCESS ∧ wit_check(scheduler) == PASS
@post: ∀m ∈ modified_funs : derives_from(m, {StaticDI})
```

---

## 理論的背景：なぜ「記号」が効果的なのか

1.  **認知スーパーポジションの崩壊**: 自然言語トークン（"気をつけて" など）は複数の埋め込み空間（Embedding Space）に重なりを持つが、特殊記号（`∀`, `¬`）はエージェントのAttentionを「論理計算モード」へと引き込み、確率的な揺らぎを抑制する。
2.  **自己監査の厳密化**: エージェントが中間思考（CoT）でアサーションを確認する際、述語論理形式であれば、自然言語よりもはるかに容易に「真偽の不一致（矛盾）」を検出できる。
3.  **コンテキスト圧縮率**: 論理式は自然言語に比べ、トークン数あたりの情報密度が極めて高く、ロングコンテキストにおける「情報の薄まり」を防ぐ。

---

## 運用プロトコル

1.  **Brain Sync**: 永続メモリ [project_context.atc](/.agent/brain/project_context.atc) から全域的な不変条件をロードする。
2.  **Declare**: タスク開始時に、そのタスク固有の ATC を FOL 形式で記述する。
3.  **Verify**: `walkthrough.md` において、`@post` が論理的に「真」であることを証明（記述）する。
