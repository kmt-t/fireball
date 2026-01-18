# サービス

## 概要

サービスはマルチゲストでは共有されるハイパーバイザのライブラリを提供する。

- wasmで実装されたハイパーバイザのコンポーネントをサービスと呼ぶ。
- サービスはゲストからwasmのモジュールのようにアクセスすることができる。
- ゲストの設定でどのサービスをロードするか指定する。

## 隔離レベル

サービスをTierで分離する。 `{FaultIsolation}`

| Tier | 通信方式 | コンテキスト | 説明 |
| --- | --- | --- | --- |
| 0 | ダイレクト | ゲストと結合 | libc、WASI、ガベージコレクション |
| 1 | IPC | 分離 | その他サービス |

## WASI

HALのWASIラッパーはサービスとして提供される。wasmゲストから呼び出すことができる。 `{IPCRouter}`

※ 参考URL: `https://github.com/WebAssembly/WASI/blob/main/specifications/wasi-0.2.9/Overview.md`

- バージョン
  - WASI 0.2.9
- 対応モジュール
  - wasi:io@0.2.9
  - wasi:random@0.2.9
  - wasi:clocks@0.2.9
  - wasi:cli@0.2.9

## libc

OSSのWASIが実装されている環境向けのlibc(wasi-libc)をサービスとして提供する。

※ 参考URL: `https://github.com/WebAssembly/wasi-libc`

## ガベージコレクション

wasmの新しい仕様に含まれるガベージコレクションはサービスとして実装される。

## 非機能制約達成のための方策

- **メモリ隔離**: Tier 1 サービスは独立したヒープパーティションを使用する。 `{IndependentHeap}` `{MemoryIsolation}`
- **障害隔離**: サービス内でのエラーがシステム全体に波及しないよう隔離する。 `{FaultIsolation}`
