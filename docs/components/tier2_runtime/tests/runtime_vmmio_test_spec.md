# vMMIO テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`runtime_vmmio.md`](docs/components/tier2_runtime/runtime_vmmio.md)
参考実装: [`vmmio_concept.py`](docs/components/tier2_runtime/concepts/vmmio_concept.py)

Bit31によるRAM/vMMIO高速分岐、FlatMap PTE + 16エントリDirect-Mapped TLB、Stage1/2/3の3段階セキュリティゲート、SHM所有権チェック、VDMA、TLB無効化を検証する。

## 2. 直交表マトリクス（Pairwise / Combinatorial Matrix）

メモリアクセスディスパッチにおける主要因子（アドレス領域 × アクセス種別 × TLB状態 × 権限状態）の組み合わせ網羅性を定義する。

| ケース番号 | アドレス領域 (FC) | アクセス種別 | TLB状態 | 権限状態 | 期待される結果・判定パス | 網羅ID |
| :---: | :--- | :--- | :--- | :--- | :--- | :--- |
| **C-01** | Guest RAM (Bit 31==0) | Read | N/A (Bypass) | 境界内 (`addr < size`) | テーブル非参照で直接アクセス成功 (`OK_GUEST_RAM`) | TEST-VMMIO-01 |
| **C-02** | Guest RAM (Bit 31==0) | Write | N/A (Bypass) | 境界外 (`addr >= size`) | 即時トラップ (`TRAP_MEMORY_OUT_OF_BOUNDS`) | TEST-VMMIO-02 |
| **C-03** | Static Device (FC=12) | Read / Write | Cold Miss | 有効・登録済み | FlatMap探索 → TLBリフィル → ハンドラ実行 (`OK_SYSCALL`) | TEST-VMMIO-10 |
| **C-04** | Static Device (FC=12) | Read / Write | Hit | 有効・登録済み | TLB完全 $O(1)$ ヒット → ハンドラ実行 (`OK_SYSCALL`) | TEST-VMMIO-11 |
| **C-05** | Undefined FC (FC=13) | Any | N/A | 未定義領域 | 即時トラップ (`TRAP_UNDEFINED_FC`) | TEST-VMMIO-12 |
| **C-06** | SHM (FC=14) | Read | Cold Miss | 有効マッピング | FlatMap二分探索 → TLBリフィル → 物理アクセス (`OK_PHYSICAL`) | TEST-VMMIO-20 |
| **C-07** | SHM (FC=14) | Write | Hit | 書き込み禁止 (`write=False`) | TLBヒット時も権限チェック執行 → トラップ (`TRAP_ACCESS_VIOLATION`) | TEST-VMMIO-17 |
| **C-08** | SHM (FC=14) | Read / Write | Evicted (衝突追い出し) | 有効マッピング | 衝突によるTLB追い出し確認 → 再ミス → FlatMap再探索成功 | TEST-VMMIO-18 |
| **C-09** | SHM (FC=14) | Any | Flushed (Revoke後) | アンマップ済み | TLBフラッシュ → FlatMap不在 → トラップ (`TRAP_UNREGISTERED_PAGE`) | TEST-VMMIO-22, 23 |
| **C-10** | Passthrough (FC=15) | Read / Write | Cold Miss / Hit | 有効マッピング | 物理アドレス直結変換 (`OK_PHYSICAL`) | TEST-VMMIO-25 |

---

## 3. テストケース一覧

### アドレス分解・高速バイパス ({FastAddressCheck})

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-VMMIO-01 | Bit31==0はRAMバイパス | - | `access(addr)`、addr<0x8000_0000 | vMMIOテーブルに一切触れず`OK_GUEST_RAM`（TLB miss/hitカウンタが変化しない） | {FastAddressCheck}, `vmmio_concept.py` `test_ram_bypass_never_touches_page_table` |
| TEST-VMMIO-02 | ゲストRAM境界チェック（比較、マスクなし） | `guest_ram_size`設定済み | 境界ちょうど（size-1）とその1バイト先をアクセス | size-1はOK、size以降は`OUT_OF_BOUNDS`（2の冪制約なし） | {FastAddressCheck}, `vmmio_concept.py` `test_linear_ram_bound_check_works_for_non_power_of_two_size` |
| TEST-VMMIO-03 | 境界外アドレスの黙示的ラップアラウンド禁止 | 境界外アドレス | アクセス | 必ずトラップし、折り畳んで継続しない | {MemoryBoundaryCheck}, `vmmio_concept.py` `test_linear_ram_is_bounds_checked_not_waved_through` |

### FlatMap PTE + TLB ({META_FlatMapIndexed})

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-VMMIO-10 | 静的デバイス(FC=12)ページへのアクセスとハンドラ呼び出し | SYSCTL等をmap_static_device済み | 該当アドレスへアクセス | `OK_SYSCALL`を返し、登録ハンドラが`(syscall_metadata, offset, is_write)`で呼ばれる | `vmmio_concept.py` `test_static_device_syscall_dispatch` |
| TEST-VMMIO-11 | TLBヒット（2回目以降のアクセス） | 同一ページへ2回アクセス | 2回目のアクセス | `tlb_hits`が増加し、`tlb_misses`は増えない | {META_RestrictedPhysicalAccess}, `vmmio_concept.py` `test_tlb_hit_after_first_walk` |
| TEST-VMMIO-12 | 未定義FCのトラップ分類 | FC=13（未割当） | アクセス | `TRAP_UNDEFINED_FC`を返す | `vmmio_concept.py` `test_undefined_fc_traps` |
| TEST-VMMIO-13 | 未登録ページのトラップ分類 | 有効なFCだが該当VPNが未登録 | アクセス | `TRAP_UNREGISTERED_PAGE`を返す | `vmmio_concept.py` `test_undefined_fc_traps` |
| TEST-VMMIO-14 | Folding XOR HashによるFC間の衝突回避 | FC=12/14/15の同一下位ページ番号 | `tlb_index`を比較 | 異なるTLBスロットに分散する | `vmmio_concept.py` `test_tlb_index_separates_function_codes` |
| TEST-VMMIO-15 | 混在アクセスパターンでの高いTLBヒット率 | Syscall宛先とSHM宛先を交互にアクセス | 10回繰り返す | ヒット率90%以上（スラッシングしない） | `vmmio_concept.py` `test_interleaved_syscall_and_shm_keep_hitting_the_tlb` |
| TEST-VMMIO-16 | FlatMap登録件数と検索 | 32件のSHMページを登録 | 全件アクセス | 全件が正しく解決される。ホットな作業集合(8件)への繰り返しアクセスは100%ヒット | `vmmio_concept.py` `test_flatmap_pte_registration_and_tlb_caching` |
| TEST-VMMIO-17 | TLBヒット時も権限チェックは必ず実施 | TLBにキャッシュ済みのPTE | 読み出し専用ページへ書き込みアクセス | TLBヒットであってもインライン権限チェックで`TRAP_ACCESS_VIOLATION`となる | {META_RestrictedPhysicalAccess}, `vmmio_concept.py` `test_permission_checks_enforced_even_on_tlb_hit` |
| TEST-VMMIO-18 | Direct-Mapped TLB スロット衝突と置換（Eviction） | 同一ハッシュスロットに衝突する2つのVPN | Aアクセス（充填）→ Bアクセス（置換）→ 再度Aアクセス | Aの再アクセス時にミスが発生し、スロットが無条件上書きされる | `vmmio_concept.py` `test_tlb_slot_conflict_eviction` |

### 3段階セキュリティゲート・SHMマッピング保護 ({OwnershipTransfer})

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-VMMIO-20 | マッピング存在時のみアクセス許可 | `map_shm_page(vpn, phys_page)`済み | 該当アドレスへアクセス | `OK_PHYSICAL` | {OwnershipTransfer}, `vmmio_concept.py` `test_shm_unmap_isolation` |
| TEST-VMMIO-21 | アンマップ後は即座に拒否 | 同上 | `unmap_shm_page(vpn)`後にアクセス | `TRAP_UNREGISTERED_PAGE`（ホットパスでのowner比較なしにPTE不在で遮断） | {OwnershipTransfer}, `vmmio_concept.py` `test_shm_unmap_isolation` |
| TEST-VMMIO-22 | Revoke時のTLB即時無効化 | SHMページがTLBに常駐 | `revoke_shm(vpn)` | 該当TLBエントリが無効化され、次回アクセスは強制的にFlatMap再walkになる | {OwnershipTransfer}, `vmmio_concept.py` `test_revoke_invalidates_tlb_and_blocks_unmapped_access` |
| TEST-VMMIO-23 | Revoke後（in-flight中）は誰もアクセス不可 | Revoke直後 | 送信元・他タスク双方でアクセス | 両方とも`TRAP_UNREGISTERED_PAGE`（未マッピング状態） | {OwnershipTransfer}, `vmmio_concept.py` `test_revoke_invalidates_tlb_and_blocks_unmapped_access` |
| TEST-VMMIO-24 | FC=14への書き込みはIPCルータのみ | 通常のゲストアクセス | FC=14へ直接書き込もうとする | 「FC=14エントリへの書き込みはIPCルータのみが行う」制約に反する経路が存在しないことを確認 | {OwnershipTransfer}, `vmmio_concept.py` `test_shm_unmap_isolation` |
| TEST-VMMIO-25 | PASSTHROUGH(FC=15)の物理アドレス変換 | `map_passthrough_page`済み | アクセス | `phys_addr = (pte.phys_page << 12) \| offset`で正しく解決 | {PhysicalPassthrough}, `vmmio_concept.py` `test_passthrough_page_access` |
| TEST-VMMIO-26 | ビット並列連続ビットマップアロケータ | 32ページの空き仮想空間 | `alloc_consecutive(k)` / `free_consecutive` | $O(1)$で連続$k$ページが確保・解放され、断片化時も正しく探索される | `vmmio_concept.py` `test_shm_virtual_address_allocator_consecutive` |
| TEST-VMMIO-27 | マルチページ連続マッピングとアクセス | 連続3ページをアロケート・マップ | 3ページすべてのアドレスへアクセス | 全ページが正しい物理アドレスに変換され、一括アンマップ後は全て未登録トラップとなる | `vmmio_concept.py` `test_vmmio_alloc_and_map_multipage` |

### vIRQ原因付き階層ディスパッチ

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-VMMIO-40 | vIRQページと静的原因源表 | `0xC000_3000`の専用ページを有効化 | 原因源表を読み出す | 固定表が`vector_id`、分類、`source_id`、属性を持ち、動的に追加・削除できない | `runtime_vmmio.md` §4.7 |
| TEST-VMMIO-41 | 原因レコードの固定形式 | 物理イベントを原因源へ変換 | 5ワードのイベントを生成してCOOSへ渡す | `vector_id`、`source_id`、`cause_code`、`payload0`、`payload1`の順序・値が保持され、ポインタや可変長領域を含まない | `runtime_vmmio.md` §4.7 |
| TEST-VMMIO-42 | vIRQ登録スロットの範囲検証 | root・4分類・デバイスの静的スロット | 範囲外スロットへ書き込む | vSoCが拒否し、保留表・有効表を変更しない | `runtime_vmmio.md` §4.7 |
| TEST-VMMIO-43 | 物理デバイスの原因集約 | 1デバイスに複数の`source_id`/`cause_code` | 複数原因を順に発生させる | 1つのデバイスディスパッチャへ集約され、原因値で振り分けられる | `runtime_vmmio.md` §4.7 |
| TEST-VMMIO-44 | 未登録vIRQノードのドロップ | 原因源は有効だが対象ノード未登録 | イベントをCOOSへ投入してドレイン | ゲスト関数を呼び出さず、診断カウンタだけを更新する | `runtime_vmmio.md` §4.7 |

### VDMA ({VDMA})

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-VMMIO-30 | REG_VDMA_*レジスタへの設定と`REG_VDMA_CTRL`起動 | レジスタに`SRC`/`DST`/`COUNT`設定 | `CTRL`のSTARTビットを1にする | 指定範囲が転送される | {VDMA}, [`scenario10_vmmio_virtual_devices.py`](experiments/pysim/scenarios/scenario10_vmmio_virtual_devices.py) |
| TEST-VMMIO-31 | SHM宛先へのVDMA転送時のマッピングチェック | `dst`が未マッピングのFC=14アドレス | VDMA実行 | `dispatch_access`と同一の権限チェックで拒否される | {VDMA}, {OwnershipTransfer}, [`scenario10_vmmio_virtual_devices.py`](experiments/pysim/scenarios/scenario10_vmmio_virtual_devices.py) |

### 実装の勘所・不変条件（Gotchas & Implementation Invariants）

| GOTCHA ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GOTCHA-VMMIO-01 | Bit 31 RAM 高速バイパスのテーブル完全非参照 | `addr < 0x8000_0000` の任意のアドレス | `access(addr)` を実行 | ページテーブル走査（FlatMap walk）および TLB 検索・更新を一切行わず即座に `OK_GUEST_RAM` を返す（`tlb_hits` / `tlb_misses` が不変）。**実装の勘所**: ゲスト RAM アクセス時に誤って TLB 検索フックを挟むと、実行時メモリアクセスの最頻パスで深刻な性能低下を引き起こす | `runtime_vmmio.md` {GOTCHA-VMMIO-01} |
| GOTCHA-VMMIO-02 | Direct-Mapped TLB の 4-bit Folding XOR Hash | 同一下位ページ番号を持つ異なる FC（FC=12 静的, FC=14 SHM, FC=15 パススルー） | 各ページの `tlb_index` を算出 | 単純な下位4bitマスクではなく `(vpn ^ (vpn >> 4) ^ (vpn >> 8) ^ (vpn >> 12) ^ (vpn >> 16)) & 15` により、異なる FC の同一下位ページが互いに異なるスロットへ分散する。**実装の勘所**: 単純な下位マスクを用いると、Syscall（FC=12）と SHM（FC=14）の同一番号ページが同一スロットで常に衝突・スラッシングを起こす | `runtime_vmmio.md` {GOTCHA-VMMIO-02} |
| GOTCHA-VMMIO-03 | SHM Revoke 後の未マッピング遮断と TLB 即時破棄 | SHM ページ（FC=14）が TLB にキャッシュされた状態 | `revoke_shm(vpn)` を実行 | 対象 TLB スロットが無効化され、FlatMap からも削除されるためアクセスが即座に `TRAP_UNREGISTERED_PAGE` で拒絶される。**実装の勘所**: PTE のアンマップを行っても TLB の該当スロットをフラッシュし忘れると、旧所有者が in-flight 中（ランデブー待ち）に TLB ヒット経由でデータを不正読み書きできる重大な脆弱性となる | `runtime_vmmio.md` {GOTCHA-VMMIO-03} |

## 3. テスト検証実績と網羅状況

- 仕様書に定義された各テストケース（不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

## 4. 未検証・スコープ外

- Cortex-M33実機でのTLB/FlatMapの実際のサイクル数（`{vMMIO_TLB}`の性能目標自体）。
- `register-hook` の完全な公開API契約。
