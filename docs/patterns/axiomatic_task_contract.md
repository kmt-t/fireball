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
- `searchable(concept)` : 概念 `{concept}` がキーワード検索によって要求仕様から実装まで到達可能であること。

---

## 5. 運用プロトコル

### 5.1 Brain Sync (Eternal Memory)
エージェントはセッション開始時に、`.agent/brain/*.atc` ファイル群（Eternal Memory）をロードし、プロジェクトの「不変の魂」を自身のコンテキストに定着させる。

1.  **`project_context.atc`**: システム全域の不変条件（□inv）と、エージェント・人間間の「Physical Time Model（クロック同期プロトコル）」をロードする。
2.  **`architecture_reference.atc`**: 各コンポーネントが遵守すべき、TLA+と直結した様相論理制約をロードする。
3.  **`navigation_dispatch.atc`**: タスクの種類に応じた「最適なスキルのルックアップテーブル」を参照し、探索コストを O(1) に収束させる。

### 5.2 ワークフロー
1.  **Declare**: タスク開始時に、そのタスク固有の ATC を記述し、認知の重ね合わせ（Superposition）を特定の設計意図へ崩壊（Collapse）させる。
2.  **Trace**: `□(∀r ∈ requirements : searchable(r))` を満たすよう、ドキュメントにキーワードを埋め込む。
3.  **Derive**: ATC の `□inv` と `◇goal` を、モデル検査用の TLA+ 仕様へと機械的に導出する。
4.  **Tension Analysis**: 設計上の対立が生じた際、それが「CONTRADICTION（二者択一）」か「ORTHOGONAL（設計による両立可能）」かを分析し、`tension_analysis.atc` を更新する。
5.  **Verify**: `walkthrough.md` において、`◇goal` が論理的に到達可能であることを記述・証明する。

---

## 6. 実例

```atc
@pre:  ∃w ∈ WIT : matches(w, docs/components/scheduler.md)
@pre:  is_tier(scheduler, 3)
□inv:  ∀f ∈ funs(scheduler) : ¬virtual(f) ∧ ¬override(f)
□inv:  ∀m ∈ Allocation : is_heap_less(m)
◇goal: build_status == SUCCESS ∧ wit_check(scheduler) == PASS
@post: ∀m ∈ modified_funs : derives_from(m, {StaticDI})
```

### TLA+への導出例
- ATCの `□inv`  → TLA+: `Spec => []Inv`
- ATCの `◇goal` → TLA+: `Spec => <>Goal`
- ATCの `P ⊳ Q` → TLA+: `P ~> Q` (Liveness)

---

## 7. 理論的背景：認知スーパーポジションの崩壊

AIエージェントの推論は、デフォルトでは「インターネット上の一般的な正解」が重ね合わさった確率的な状態にある。
ATCは、記号的な論理制約をコンテキストに叩き込むことで、この確率密度をプロジェクト固有の「唯一の正解」へと強制的に収束させるための物理的な手段である。
特に `□`（Necessarily）記号は、エージェントの Attention を論理計算モードへと誘い、確率的な揺らぎを抑制する効果を持つ。
