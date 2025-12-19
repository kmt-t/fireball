# サービス

## 概要

wasmで実装されたハイパーバイザのコンポーネントをサービスと呼ぶ。サービスはwasmゲストからIPCなしで直接呼び出すことができる。

## WASI

HALのWASIラッパーはサービスとして提供される。wasmゲストから呼び出すことができる。

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
