# Axiomatic Task Contract (ATC) Pattern

エージェントの認知負荷を最小化し、内部状態をプロジェクト固有の論理空間へ強制的に収束（Collapse）させるための、**様相論理（Modal Logic）**ベースのアサーション・プロトコル。

## 背景

AIエージェントの「自由な推論」は、時としてプロジェクトの重要な制約（例：組み込み環境での仮想関数禁止）を「一般的な正解」として上書き（Drift）してしまう。
記号的な述語（例：`□(∀f ∈ Fun : ¬virtual(f))`）はエージェントのトークン生成確率を物理的に一点に限定する。
TLA+が時相論理で検証を行うため、ATCの記法も様相論理に統一することで、ATC → TLA+ への導出パスが自然になる。

---

## ATC-DSL v3 (Modal Logic) 記法

### 1. 様相演算子
| 記号 | 意味 | TLA+対応 | 用途 |
| :--- | :--- | :--- | :--- |
| `□P` | **Necessarily** — Pはすべての状態で成立する | `[]P` | 不変条件 |
| `◇P` | **Possibly** — Pはいずれかの到達可能な状態で成立する | `<>P` | 到達可能性・活性 |
| `P ⊳ Q` | **Leads-to** — Pが成立すればいずれQが成立する | `P ~> Q` | 因果関係 |

### 2. 状態遷移の記述
```atc
@pre:  <現在の状態 s において真であるべき命題>
@post: <アクション実行後の状態 s' において達成されるべき命題>
□inv:  <すべての到達可能な状態で維持される不変条件>  (= TLA+ の []Inv)
◇goal: <最終的に到達すべき状態>                     (= TLA+ の <>Goal)
```

### 3. 量化子と論理演算子（述語論理との共存）
- `∀` (ALL), `∃` (EXISTS) — 集合・ドメイン内の量化に引き続き使用
- `∧` (AND), `∨` (OR), `¬` (NOT), `⇒` (IMPLIES)
- 様相演算子と量化子を組み合わせる: `□(∀f ∈ API : return_type(f) == result<T, E>)`

### 4. 標準述語（Project-Specific Predicates）
- `tier(component, N)` : コンポーネントの階層。
- `is_pure_static(comp)` : vtableを一切含まないこと。
- `matches(impl, wit)` : 実装がWITの契約に従っていること。
- `contains(target, pattern)` : 特定のシンボル/パターンの包含。
- `derives_from(spec, req_id)` : 要求キーワード `{req_id}` からの導出。
- `permitted(addr)` : vMMIO許可テーブルでアクセスが許可されていること。

---

## 記述例：Tier 2 インターフェースのリファクタリング

```atc
@pre:  ∃w ∈ WIT : matches(w, docs/components/scheduler.md)
@pre:  is_tier(scheduler, 2)
□inv:  ∀f ∈ funs(scheduler) : ¬virtual(f) ∧ ¬override(f)
□inv:  ∀d ∈ deps(scheduler) : is_template_injected(d)
◇goal: build_status == SUCCESS ∧ wit_check(scheduler) == PASS
@post: ∀m ∈ modified_funs : derives_from(m, {StaticDI})
```

### TLA+への導出
```
ATCの □inv  → TLA+の Spec => []Inv
ATCの ◇goal → TLA+の Spec => <>Goal
ATCの P ⊳ Q → TLA+の P ~> Q (liveness property)
```

---

## 理論的背景：述語論理からの移行理由

1.  **TLA+との対称性**: TLA+は時相論理（LTL/CTL）で検証を行う。ATCの記法が同じ様相演算子を使うことで、仕様から検証モデルへの変換が機械的になる。
2.  **不変条件の明示性**: `@inv` は暗黙に「すべての状態で」を意味していたが、`□` を付けることでその意味が記号的に明示される。
3.  **活性条件の表現**: 述語論理では「いずれ達成される」を自然に表現できなかったが、`◇` によって容易に記述できる。
4.  **認知スーパーポジションの崩壊**: 特殊記号（`□`, `◇`）はエージェントのAttentionを「論理計算モード」へと引き込み、確率的な揺らぎを抑制する。

---

## 運用プロトコル

1.  **Brain Sync**: 永続メモリ [project_context.atc](/.agent/brain/project_context.atc) から全域的な `□inv` をロードする。
2.  **Declare**: タスク開始時に、そのタスク固有の ATC を様相論理形式で記述する。
3.  **Derive**: `□inv` と `◇goal` から TLA+ 仕様への導出を可能にする。
4.  **Verify**: `walkthrough.md` において、`◇goal` が論理的に到達可能であることを証明（記述）する。
