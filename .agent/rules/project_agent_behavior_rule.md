# プロジェクトエージェント行動ルール

本ドキュメントは、エージェントが作業を遂行する際の自己修正、メモリ管理、および外部環境とのインタラクションに関する厳格なプロトコルを定義する。

## 1. 自己修正とメモリ発見プロトコル

エージェントは、過去のセッションで行われたすべての設計判断や契約を「確実に忘れている」ことを不変条件として行動せよ。
自分の直感や一般的と思われる解法を疑い、作業開始前に必ず以下の手順を踏むこと。

- **`GEMINI.md` の再読**: ワークスペースルート `GEMINI.md` を読み、基本プロトコルを脳に再同期する。
- **ATCのロード**: `.agent/brain/スコープ_対象.atc` を読み込み、現在のシステム不変条件を再発見する。
- **局所セマンティクスの復元**: スキル内部の `README.md` や `SKILL.md` を読み、局所的な論理性セマンティクス 以前の時相論理変換規則等 を復元する。
- **情報探索の深層化 Skill-First Mandate**: 関連情報は必ず `general_codebase_explore` スキル等の検索ツールを用い、`docs/requires/requirement_list.md` の `{Keyword}` またはドキュメント内の既存用語をベースに文脈を収集せよ。
    - **警告**: `grep_search` 等の汎用ツールは「安易な探索 Path of Least Resistance」を誘発する。スキルを用いた高密度な解析を優先せよ。ツールに不備がある場合はバイパスせず、スキル自体を修正して再利用可能にすること。
- **WSL2 Mandate Windows Host Only**: ホストOSが Windows の場合、全てのビルド、テスト、スクリプト実行においてWSL2環境の使用を必須とする。コマンド実行時は原則として `wsl <command>` を使用せよ。Windowsホスト側での直接実行は原則禁止である。
- **人間への相談 Human-in-the-Loop**: 人間のコンテキスト、環境、物理的状態はエージェントよりも広範である。情報不足やトラブル時は独断せず速やかに相談せよ。
- **ローカル Ollama コエージェントプロトコル Co-Agent Mandate**: 広範なソースコードの横断的要約、巨大なログの解析、および大量のファイルを対象とした事実の一次抽出において、クラウドトークンの浪費とレイテンシを抑制するため、ローカルの Ollama `phi3:mini` を部下エージェントとして活用せよ。
    - **Axiomatic Output**: 部下の出力は常に述語論理のリスト形式 Logic Fact List とし、`.agent/brain/co_agent/` 配下に隔離保存せよ。
    - **Tiered Inference**: 部下が「事実の抽出 論理要約・構造化」を担い、メインエージェントがその抽象化されたデータを元に深い推論を行う。この構造分離 Isolation によりメインエージェントの記憶汚染を防止せよ。
    - **実行例**: `find src -name "*.cxx" | wsl python3 .agent/skills/project_ollama_query/scripts/query_ollama.py <SCOPE> "主要な依存関係をリストアップせよ"`

## 2. ドキュメントの配置ルール

新たな規約や永続化すべき知識を記述する場合、エージェントが作業の流れで必ず読む場所 本ドキュメント、`GEMINI.md`、または該当する `SKILL.md` の冒頭 に記述せよ。孤立したファイルに記述するだけでは、記憶の揮発により「存在自体を忘れる」リスクがある。

## 3. Axiomatic Task Contract (ATC) Pattern

エージェントの認知負荷を最小化し、内部状態をプロジェクト固有の論理空間へ強制的に収束 Collapse させるための、様相論理 Modal Logic ベースのアサーション・プロトコル。

### ATC-DSL v3 (Modal Logic) 記法

#### 1. 様相演算子
| 記号 | 意味 | TLA+対応 | 用途 |
| :--- | :--- | :--- | :--- |
| `□P` | **Necessarily** — Pはすべての状態で成立する | `[]P` | 不変条件 |
| `◇P` | **Possibly** — Pはいずれかの到達可能な状態で成立する | `<>P` | 到達可能性・活性 |
| `P ⊳ Q` | **Leads-to** — Pが成立すればいずれQが成立する | `P ~> Q` | 因果関係 |

#### 2. 状態遷移の記述
```atc
@pre:  <現在の状態 s において真であるべき命題>
@post: <アクション実行後の状態 s' において達成されるべき命題>
□inv:  <すべての到達可能な状態で維持される不変条件>  (= TLA+ の []Inv)
◇goal: <最終的に到達すべき状態>                     (= TLA+ の <>Goal)
```

#### 3. 量化子と論理演算子
- `∀` (ALL), `∃` (EXISTS) — 集合・ドメイン内の量化に使用
- `∧` (AND), `∨` (OR), `¬` (NOT), `⇒` (IMPLIES)
- 様相演算子と量化子を組み合わせる: `□(∀f ∈ API : return_type(f) == result<T, E>)`

#### 4. 標準述語 Project-Specific Predicates
- `tier(component, N)` : コンポーネントの階層。
- `is_pure_static(comp)` : vtableを一切含まないこと。
- `matches(impl, wit)` : 実装がWITの契約に従っていること。
- `contains(target, pattern)` : 特定のシンボル/パターンの包含。
- `derives_from(spec, req_id)` : 要求キーワード `{req_id}` からの導出。
- `permitted(addr)` : vMMIO許可テーブルでアクセスが許可されていること。
- `searchable(concept)` : 概念 `{concept}` がキーワード検索によって要求仕様から実装まで到達可能であること。

### 運用プロトコル

#### Brain Sync (Eternal Memory)
エージェントはセッション開始時に、`.agent/brain/*.atc` ファイル群 Eternal Memory をロードし、プロジェクトの「不変の魂」を自身のコンテキストに定着させる。

1.  **`project_context.atc`**: システム全域の不変条件 □inv と、エージェント・人間間の Physical Time Model クロックス同期プロトコル をロードする。
2.  **`architecture_reference.atc`**: 各コンポーネントが遵守すべき、TLA+と直結した様相論理制約をロードする。
3.  **`navigation_dispatch.atc`**: タスクの種類に応じた最適なスキルのルックアップテーブルを参照し、探索コストを O(1) に収束させる。

#### ワークフロー
1.  **Declare**: タスク開始時に、そのタスク固有の ATC を記述し、認知の重ね合わせ Superposition を特定の設計意図へ崩壊 Collapse させる。
2.  **Trace**: `□(∀r ∈ requirements : searchable(r))` を満たすよう、ドキュメントにキーワードを埋め込む。
3.  **Derive**: ATC の `□inv` と `◇goal` を、モデル検査用の TLA+ 仕様へと機械的に導出する。
4.  **Tension Analysis**: 設計上の対立が生じた際、それが CONTRADICTION 二者択一 か ORTHOGONAL 設計による両立可能 かを分析し、`tension_analysis.atc` を更新する。
5.  **Verify**: `walkthrough.md` において、`◇goal` が論理的に到達可能であることを記述・証明する。

### 実例

```atc
@pre:  ∃w ∈ WIT : matches(w, docs/components/os_scheduler.md)
@pre:  is_tier(scheduler, 3)
□inv:  ∀f ∈ funs(scheduler) : ¬virtual(f) ∧ ¬override(f)
□inv:  ∀m ∈ Allocation : is_heap_less(m)
◇goal: build_status == SUCCESS ∧ wit_check(scheduler) == PASS
@post: ∀m ∈ modified_funs : derives_from(m, {StaticDI})
```

### 理論的背景：認知スーパーポジションの崩壊

AIエージェントの推論は、デフォルトでは「インターネット上の一般的な正解」が重ね合わさった確率的な状態にある。
ATCは、記号的な論理制約をコンテキストに叩き込むことで、この確率密度をプロジェクト固有の「唯一の正解」へと強制的に収束させるための物理的な手段である。
特に `□` Necessarily 記号は、エージェントの Attention を論理計算モードへと誘い、確率的な揺らぎを抑制する効果を持つ。
