# PageIndex MCP 導入検討メモ

本書は PageIndex MCP の導入適合性を調べた検討メモである。仕様や採用方針の正本ではない。
調査日は2026年9月30日である。

## 1. 判断

PageIndex は長文資料の階層をたどる検索補助として有望である。
Fireball の Markdown 仕様を直接扱う方法と、関連文書の読み落としを強制的に検出する機能は確認できなかった。

そのため PageIndex だけを読み落とし防止の仕組みにする案は推奨しない。
既存の DocGraph を使うローカル MCP を網羅性確認の中心に置き、PageIndex はPDF資料の探索に限定して試す案を推奨する。

## 2. PageIndex MCP の適合性

### 2.1 接続

PageIndex は開発者向けの Streamable HTTP MCP サーバーを提供する。
接続先は `https://api.pageindex.ai/mcp` であり、APIキーによるBearer認証を使う。
[PageIndex接続ガイド](https://docs.pageindex.ai/mcp)
Codex はプロジェクト単位またはユーザー単位でHTTP MCPサーバーを設定できる。
トークンを環境変数から読み込む設定も利用できる。
[Codex MCPガイド](https://learn.chatgpt.com/docs/extend/mcp)

### 2.2 文書形式

PageIndex のクラウドSDKは PDF、PPTX、Word 文書を受け付ける。
ローカル索引モードはPDFのみを受け付ける。
[PageIndex文書処理仕様](https://docs.pageindex.ai/sdk/documents)
確認した公式資料には Markdown の直接取り込み手順がない。

Fireballで利用する場合は Markdown をPDFへ変換する必要がある。
変換時に `&#123;Keyword&#125;`、Markdownリンク、見出し構造を保つ必要がある。
PDFページ引用から元のMarkdownファイル、見出し、行へ戻す対応表も必要になる。
これらの変換と対応表はPageIndexの標準機能ではなく、プロジェクト側の追加実装となる。

### 2.3 検索と読み落とし防止

PageIndex MCP は文書やフォルダの探索、文書構造の取得、ページ内容の取得を行う。
公式の推奨フローは、文書一覧と検索から始め、文書構造を確認して対象ページを読む手順である。
[PageIndex MCPツール一覧](https://docs.pageindex.ai/js-sdk/mcp-tools)

この機能は、エージェントが関係資料を見つける助けになる。
一方、タスクに必要な全仕様・形式モデル・コンセプトコード・テスト仕様を読んだか判定する網羅性ゲートは、確認した公式資料に記載されていない。
検索結果だけで「読み落としなし」を保証することはできない。

## 3. Fireball側の既存基盤

[`README.md`](tools/spec-integrator/README.md) によると、`spec-integrator` はファイル、見出しセクション、要求キーワードをDocGraphとして扱う。
Markdownリンクと `&#123;Keyword&#125;` の参照関係を使って関連セクションをたどれる。
`build` はドキュメントDBと索引を作り、`check-doc` は構造、traceability、Tier依存、形式検証、証跡、義務、整合性を検査する。

現在のCLIは文書検査とDocGraphの可視化を提供する。
リポジトリ内にMCPサーバー実装は見つからなかった。
既存のDBとグラフを読むローカルMCPを追加すれば、Markdownを変換せずに正本を検索できる。

## 4. 推奨する構成

Fireball Markdown 正本は `spec-integrator build` でDocGraphと文書DBへ反映する。
ローカル Fireball Docs MCP はそのDBからCodexへ関連範囲を返す。
PageIndex MCP は外部の長文PDF資料を検索するためにCodexへ接続する。

Fireball Docs MCP はローカルの正本とDocGraphから関連範囲を提示する。
PageIndex MCP は、外部の長文PDFや図表を含む資料の探索に使う。
PageIndexの回答は参考情報として扱い、Markdown正本を判断根拠にする。

読み落とし確認用のMCPは、少なくとも次の操作を提供する。

- 対象ファイル、コンポーネント、または `&#123;Keyword&#125;` から関連セクション一覧を返す。
- キーワードの定義元と参照先を区別して返す。
- 要求、コンポーネント仕様、形式モデル、コンセプトコード、QA資料の対応範囲を提示する。
- MCP経由で実際に取得したセクションを記録し、未取得の必須範囲を表示する。
- 対象範囲が曖昧な場合に、未確定の関連資料を明示する。

この仕組みはDocGraphに登録された関係の網羅を確認する。
文書に書かれていない意味上の関係までは検出しない。
その限界は既存の `check-doc`、コンポーネントレビュー、人間の設計レビューで補う。

## 5. 運用上の制約

### 5.1 クラウドへの文書送信

PageIndexの開発者向けMCPはPageIndex Cloudに登録済みの文書を検索する。
[PageIndex接続ガイド](https://docs.pageindex.ai/mcp)
公式MCPサーバーのローカルPDF処理コードも、ローカルファイルを読み込み、署名付きアップロード先へ送信してから文書登録を依頼する。
[ローカルPDF処理実装](https://github.com/VectifyAI/pageindex-mcp/blob/master/src/tools/process-document.ts)
したがって、ローカルMCPプロセスを使うだけでは文書が端末内に留まるとは限らない。

PageIndexの利用規約では、顧客データの権利は顧客に残る。
一方、サービス提供のための利用許諾に加え、サービスの性能・機能改善への利用も記載されている。
試用期間の終了時にはデータが利用不能になり、削除される場合がある。
[PageIndex利用規約](https://pageindex.ai/policies)
非公開の設計資料やソースを送る前に、適用される契約、データ処理条件、組織の秘密情報ルールを確認する必要がある。

PageIndex SDKにはローカル索引モードがある。
[PageIndexローカル索引とCloudの概要](https://docs.pageindex.ai/getting-started)
ただし、開発者向けの標準MCP接続先はCloud用である。
[PageIndex MCP接続ガイド](https://docs.pageindex.ai/mcp)
ローカル索引SDKを採用しても、そのまま標準のリモートMCPへ接続できるとは限らない。

### 5.2 料金

2026年9月18日更新の公式料金資料では、Cloud索引化は1ページ0.01米ドルである。
保持中の索引ページは1ページあたり月0.001米ドルで、最初の1,000ページは無料とされる。
検索自体にPageIndexの料金はなく、利用するLLM事業者への推論料金が別途かかる。
[PageIndex Cloud料金](https://docs.pageindex.ai/pricing)

料金とプランは変更される可能性がある。
試験対象のページ数を見積もり、実利用時の条件をDeveloper Dashboardで確認する。

## 6. 小規模試行の条件

PageIndexを試す場合は、公開可能な少数のPDFまたは変換済み資料に限定する。
索引対象としたMarkdownのコミット、ファイルパス、見出し、PDFページの対応を記録する。
文書更新時には再索引の要否と古い索引の削除を確認する。

試行では、回答の正確さに加えて、次の点を確認する。

- 関連文書の発見率と不要資料の混入率。
- 引用からMarkdown正本へ戻れる割合。
- `&#123;Keyword&#125;` とリンクの意味がPDF変換後も追跡できる割合。
- 変更後の再索引に要する作業と料金。
- Codexで必要な探索ツールが安定して利用できること。

読み落とし防止の成立判定は、PageIndexの回答品質ではなく、ローカルMCPがDocGraph上の必須範囲を提示し、未取得範囲を残したまま完了扱いしないことに置く。

## 7. 参照資料

- [PageIndex MCP接続ガイド](https://docs.pageindex.ai/mcp)
- [PageIndex MCPツール一覧](https://docs.pageindex.ai/js-sdk/mcp-tools)
- [PageIndex文書処理仕様](https://docs.pageindex.ai/sdk/documents)
- [PageIndexローカル索引とCloudの概要](https://docs.pageindex.ai/getting-started)
- [PageIndex Cloud料金](https://docs.pageindex.ai/pricing)
- [PageIndex利用規約](https://pageindex.ai/policies)
- [PageIndex MCPのローカルPDF処理実装](https://github.com/VectifyAI/pageindex-mcp/blob/master/src/tools/process-document.ts)
- [Codex MCP公式ガイド](https://learn.chatgpt.com/docs/extend/mcp)
- [README.md](tools/spec-integrator/README.md)
