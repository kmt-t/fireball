# LLDB 21.1.8 による Fireball RSP デバッグ調査

<!-- traceability: {WasmCodeSectionPC} {RSPMinimalSet} -->

調査日: 2026-10-04
対象: Clang が生成した `wasm32-unknown-unknown` の DWARF 付き WASM と Fireball の最小 RSP サーバ

## 結論

今回 `$Z0` が送信されなかった原因は、FireballのRSP実装ではない。LLDB側でWASM `code` セクションのロードアドレスが未設定だったため、LLDBが有効なブレークポイントサイトを作れなかった。

LLDBのブレークポイントログには `Failed to add breakpoint site at 0xffffffffffffffff` が出ていた。`0xffffffffffffffff` はLLDBの無効アドレスである。接続後に次を実行すると、LLDBは `breakpoint set -n add_one` に対して `$Z0,17,0` を自動送信した。Fireballは `OK` を返し、続行後に `add_one` の該当位置で停止した。

```text
(lldb) target modules load --file /path/to/module.wasm code 0x0
(lldb) breakpoint set -n add_one
```

`code 0x0` は、WASMのコードセクションをFireballのRSP PC空間の先頭へ対応付ける設定である。LLDBの `target modules load` はモジュールのセクションにターゲット上のロードアドレスを割り当てるコマンドであり、ロードアドレスを指定するとアドレス検索はその空間で行われる。[LLDB「Defining Load Addresses for Sections」](https://lldb.llvm.org/use/symbolication.html#defining-load-addresses-for-sections)

## LLDBでの送信条件

`breakpoint set` は、名前やソース行からブレークポイント位置を解決する。その位置からプロセス上のロードアドレスを得られた場合、LLDBはブレークポイントサイトを作成して有効化する。`Process::CreateBreakpointSite` は無効でないロードアドレスに対して `EnableBreakpointSite` を呼ぶ。[LLVM 21.1.8 `Process.cpp`](https://github.com/llvm/llvm-project/blob/llvmorg-21.1.8/lldb/source/Target/Process.cpp#L1600-L1689)

GDB Remoteプロセスの `EnableBreakpointSite` は、通常のソフトウェアブレークポイントを先に試す。そこではRSPの `Z0` パケットを送る。[LLVM 21.1.8 `ProcessGDBRemote.cpp`](https://github.com/llvm/llvm-project/blob/llvmorg-21.1.8/lldb/source/Plugins/Process/gdb-remote/ProcessGDBRemote.cpp#L3078-L3125)

このため、関数名やDWARF行情報をローカルで解決できても、セクションのロードアドレスがなければRSPブレークポイントは登録されない。今回の `target modules load ... code 0x0` が不足していたアドレス対応を補い、LLDBがブレークポイントサイトを作れるようにした。

## `qSupported` と `swbreak` の意味

`swbreak+` は、ソフトウェアブレークポイント停止を停止応答の `swbreak` 理由で報告できることを表す。ソフトウェアブレークポイント挿入パケット `Z0` の対応可否を知らせるフラグではない。GDB RSP仕様は `Z0` をソフトウェアブレークポイントの挿入として別に定義している。[GDB「General Query Packets」](https://sourceware.org/gdb/current/onlinedocs/gdb.html/General-Query-Packets.html)、[GDB「Packets」](https://sourceware.org/gdb/current/onlinedocs/gdb.html/Packets.html)

LLDB 21.1.8 は `Z0` の対応状態を初期状態で有効とし、挿入要求に空の未対応応答が返った場合に無効化する。したがって、Fireballの `qSupported` 応答が `PacketSize=256` のみであることは、LLDBが `Z0` を送らなかった原因ではない。[LLVM 21.1.8 `GDBRemoteCommunicationClient.cpp`](https://github.com/llvm/llvm-project/blob/llvmorg-21.1.8/lldb/source/Plugins/Process/gdb-remote/GDBRemoteCommunicationClient.cpp#L60-L74)、[同 `SendGDBStoppointTypePacket`](https://github.com/llvm/llvm-project/blob/llvmorg-21.1.8/lldb/source/Plugins/Process/gdb-remote/GDBRemoteCommunicationClient.cpp#L2829-L2888)

`swbreak+` の追加は、停止応答の意味付けを拡張する場合に検討する項目である。今回の停止応答 `S05` は一般的な `SIGTRAP` を示しており、`Z0` の挿入とは独立している。

## Fireball側の対応

FireballのRSP仕様は `Z0,<addr>,<kind>` をサポート対象とし、アドレスをWASM Code section payload相対オフセットと定義している。[gdb_rsp_protocol.md](docs/specs/gdb_rsp_protocol.md#3-サポートコマンドマトリクス-gdb-rsp-commands)

pysimのRSP実装は `Z0` の形式とアドレスを検証し、デバッガへブレークポイントを登録した後に `OK` を返す。[debugger.py](experiments/pysim/tier3_plugins/debugger/debugger.py#L414-L425)

LLDBに仮想レジスタ構成を伝える設定は別途必要である。Fireballは `qRegisterInfo` や `qXfer:features:read:target.xml` を提供しないため、LLDBの `plugin.process.gdb-remote.target-definition-file` でレジスタ定義を渡す方法が使える。これはレジスタの解釈に関する設定であり、今回のロードアドレス不足とは別の条件である。[LLDB「Troubleshooting Registers」](https://lldb.llvm.org/use/troubleshooting.html#why-do-i-see-more-less-or-different-registers-than-i-expected)

## 確認結果の範囲

この確認はLLDB 21.1.8、Clang生成の小さなDWARF付きWASM、Fireball pysim RSPサーバで行った。FireballがDWARFを読み込んだわけではない。シンボルと行情報はLLDBがローカルのWASMファイルから読み、FireballはRSP経由で受け取ったコード相対PCのブレークポイントを処理した。
