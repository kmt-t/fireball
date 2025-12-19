# サービス

## 概要

wasmで実装されたハイパーバイザのコンポーネントをサービスと呼ぶ。サービスはwasmゲストからIPCなしで直接呼び出すことができる。

## WASI

HALのWASIラッパーはサービスとして提供される。wasmゲストから呼び出すことができる。

## libc

OSSのWASIが実装されている環境向けのlibc(wasi-libc)をサービスとして提供する。

※ 参考URL: `https://github.com/WebAssembly/wasi-libc`

## ガベージコレクション

wasmの新しい仕様に含まれるガベージコレクションはサービスとして実装される。
