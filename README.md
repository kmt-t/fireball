# Fireball - WASM Hypervisor for Embedded Systems

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Fireballは、極小リソース環境（RAM 64KB以下）で動作する高性能WASM JITランタイムです。ARM Cortex-MやRISC-Vなどの組み込みマイコンをターゲットとし、スタックレス設計および静的メモリ管理により低オーバーヘッドな実行環境を提供します。

## 概要

Fireballは `docs/architecture/` に定義された設計思想に基づき、以下の特徴を持ちます：

- **vSoCアーキテクチャ**: ハーネスパターンによるプラグイン形式のシステム構成
- **低オーバーヘッド**: 協調型マルチタスク (COOS) による軽量コンテキストスイッチ
- **動的代謝**: インタプリタとJITエンジンの切り替え実行
ato- **厳格なリソース管理**: 動的メモリ確保（ヒープ）の原則禁止と15KLOC制約

詳細は [アーキテクチャ概要](docs/architecture/architecture_overview.md) を参照してください。

## セットアップ

ビルドには Clang, CMake, Ninja が必要です。

1. **前提ツール**
   - Clang (13+)
   - CMake (4.2+)
   - Ninja
   - Python 3.14+ (pyenv推奨)
   - wit-bindgen
   - TLA+ (TLC)

2. **依存環境のインストール**
   ```bash
   pip install -r requirements.txt
   ```

## ビルド方法

### ホスト環境 (x64) 向け
```bash
mkdir cmake-build
cd cmake-build
cmake -G Ninja ..
ninja
```

## ドキュメントと開発プロセス

Fireballの開発は `docs/` に格納された仕様書に基づきます。実装や変更を行う際は、必ず該当するドキュメントを確認し、 `.claude/rules/` に定義された開発ポリシーを遵守してください。

- **設計ドキュメント**: `docs/components/`
- **開発ガイドライン**: `.claude/rules/development-policy.md`
- **リソース予算**: `docs/architecture/resource_budget.md`

## ライセンス

MIT License - 詳細は [LICENSE](LICENSE) ファイルを参照。
