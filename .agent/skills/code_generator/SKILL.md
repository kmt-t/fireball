---
name: Code Generation
description: >-
  JSONデータ/WIT IDLからPythonスクリプトでC++コードを自動生成するメタスキル。
  WHEN: 命令セット定義, レジスタマップ生成, WITからのスタブ生成, 規則性のあるコードの追加・変更
  SCOPE: コード生成の手順と原則。生成対象のデータ定義は各コンポーネントのdataディレクトリを参照。
  RELATED: wasm_development（WIT定義の参照元）, fireball_vocabulary（生成コードの型語彙）
---

# Code Generation スキル

## 概要

このスキルは、手動作業によるミスを削減し、設計データからの再現性を担保するために、PythonスクリプトとJSONデータを用いた「コード生成ツール」を構築・運用する手順を定義します。 `{Traceability}` `{SourceOfTruth}`
インタープリタの命令定義、JITのコード生成ロジック、レジスタマップなど、規則性があり再現性が求められるコードが対象です。

## 生成フロー

```mermaid
graph LR
    subgraph Input
        JSON[JSON Data]
        WIT[WIT IDL]
    end
    subgraph Logic
        Python[Python Generator]
        Bindgen[wit-bindgen]
    end
    subgraph Output
        Header[*.hxx (Header)]
        Source[*.cxx (Source)]
    end

    JSON --> Python
    WIT --> Bindgen
    Python --> Header
    Bindgen --> Source
```

## 原則

1. **Source of Truth (情報の真実在)**: 生成対象のデータはすべてJSONファイル、または **WIT IDL** などのIDLファイルに集約し、コードはその投影（プロジェクション）として扱う。 `{SourceOfTruth}`
2. **適切なツールの選択**: 内部データ構造や定数定義にはJSON+Python、ゲスト/ホスト間のインターフェイス（システムコール等）には相互運用性に優れた **WIT (WebAssembly Interface Type)** を使用する。
3. **自動生成コードの純粋性**: 自動生成されたファイルは手動で編集しない。変更が必要な場合は、メタデータ（JSON/WIT）または生成ロジックを修正する。 `{Reproducibility}`
4. **プロジェクト規約の自動適用**: 生成スクリプト内で `snake_case` や `#pragma once`、`fireball_vocabulary`（`byte_count` 等）を適用し、規約遵守を自動化する。

## 手順

1. **メタデータの抽象化と定義形式の選定**: 
   - 内部実装（命令セット、内部構成）: JSONスキーマを設計。
   - **サービス・ゲスト境界**: **WIT IDL** を用いてインターフェイスを定義。
2. **生成スクリプト/ツールの実行**: 
   - JSON形式: Pythonスクリプトを用いてプロジェクト規約（`#pragma once` 等）に準拠したC++コードを出力。
   - WIT形式: `wit-bindgen` または独自スクリプトを用いて生成。
3. **運用ディレクトリ構成**:
   - `scripts/`: ジェネレータ本体
   - `data/`: 定義データ（JSON）
   - `inc/`: 出力されるヘッダファイル
4. **検証**: 
   - 生成されたコードがプロジェクトのフォーマッタを通過し、生プリミティブ（`uint32_t` 等）ではなく `fireball_vocabulary` のエイリアスを使用しているか確認する。

## 運用上の利点

- **JIT/インタープリタ**: 命令の追加・変更がメタデータ一箇所で完結し、デコーダと実行部の不整合を防止できる。
- **レジスタマップ**: ハードウェア仕様の変更を即座にソフトウェア定義に反映できる。

## 設計完了チェックリスト

- [ ] すべての定義が JSON/WIT などの Source of Truth から生成されているか。
- [ ] 生成コードに `#pragma once` が使用され、レガシーなインクルードガードが排除されているか。
- [ ] 生成コードで `fireball_vocabulary`（`address`, `byte_count` 等）が適切に使用されているか。
- [ ] クラスメンバーにおいて末尾アンダースコア `_` の命名規則が守られているか。
- [ ] 自動生成ファイルであることを示す警告コメントが先頭に含まれているか。
