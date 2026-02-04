---
name: WASI Spec Reader
description: Local WASI (WebAssembly System Interface) Specification を読み解き、システムインターフェイスの設計を支援するスキル
---

# WASI Spec Reader スキル

このスキルは、プロジェクト内にサブモジュールとして配置された WASI Specification を参照し、WASI 準拠のインターフェイス設計や実装を支援します。

## 1. 仕様書の場所

- **Base Path**: `docs/orders/references/wasi/`

## 2. ディレクトリ構造と主な内容

| ディレクトリ | 内容 | 活用シーン |
|:---|:---|:---|
| `proposals/` | 各機能ごとのプロポーザル（WIT形式とMarkdown） | `filesystem`, `io`, `sockets` などの具体的なAPI仕様を確認したい時 |
| `docs/` | 設計原則、全体目標、Preview2の詳細など | WASIの背景にある設計思想や機能の概要を理解したい時 |
| `specifications/` | リリース済みの仕様（Snapshotなど） | 特定のバージョンにおける定義を確認したい時 |

## 3. 推奨される調査手順

1. **機能の特定**: 調査したい機能（例: ファイル操作、クロック）に対応する `proposals/` 下のディレクトリを探す。
2. **WITファイルの確認**: ディレクトリ内の `.wit` ファイルを参照し、関数シグネチャ、型定義、リソースを把握する。
3. **ドキュメントの確認**: 同ディレクトリ内の `.md` ファイルを参照し、APIのセマンティクスや制約を確認する。

## 4. 主なプロポーザル

- `filesystem`: ファイル・ディレクトリ操作
- `io`: 読み取り・書き込みのストリーム、ポーリング
- `clocks`: 現在時刻、遅延（monotonic/wall clock）
- `random`: 乱数生成
- `sockets`: ネットワーク通信
- `cli`: 標準入出力、環境変数、コマンドライン引数

## 5. 注意事項

- WASIは現在進行形で進化しており、`proposals/` 内の WIT ファイルが最新の合意事項を反映しています。
- WIT (WebAssembly Interface Type) 形式の理解が必要な場合は、`docs/WitInWasi.md` を参照してください。
