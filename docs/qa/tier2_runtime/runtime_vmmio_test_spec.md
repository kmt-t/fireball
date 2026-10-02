# vMMIO テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`runtime_vmmio.md`](docs/components/tier2_runtime/runtime_vmmio.md)
参考実装: [`vmmio_concept.py`](docs/components/tier2_runtime/concepts/vmmio_concept.py)

Bit31によるRAM/vMMIO高速分岐、64件のFlatMap PTE + 32エントリDirect-Mapped TLB、Stage1/2/3の3段階セキュリティゲート、SHM所有権チェック、TLB無効化を検証する。システムコールとVDMAのhost-call transportは本書の対象外とし、`runtime_syscall_test_spec.md`で検証する。

## 2. テストケース一覧

### 2.1 直交表マトリクス（Pairwise / Combinatorial Matrix）

メモリアクセスディスパッチにおける主要因子（アドレス領域 × アクセス種別 × TLB状態 × 権限状態）の組み合わせ網羅性を定義する。

| ケース番号 | アドレス領域 (FC) | アクセス種別 | TLB状態 | 権限状態 | 期待される結果・判定パス | 網羅ID |
| :---: | :--- | :--- | :--- | :--- | :--- | :--- |
| **C-01** | Guest RAM (Bit 31==0) | Read | N/A (Bypass) | 境界内 (`addr < size`) | テーブル非参照で直接アクセス成功 (`OK_GUEST_RAM`) | TEST-VMMIO-01 |
| **C-02** | Guest RAM (Bit 31==0) | Write | N/A (Bypass) | 境界外 (`addr >= size`) | 即時トラップ (`TRAP_MEMORY_OUT_OF_BOUNDS`) | TEST-VMMIO-02 |
| **C-03** | Static Device (FC=12) | Read / Write | Cold Miss | 有効・登録済み | FlatMap探索 → TLBリフィル → ハンドラ実行 (`OK_STATIC_DEVICE`) | TEST-VMMIO-10 |
| **C-04** | Static Device (FC=12) | Read / Write | Hit | 有効・登録済み | TLB完全 $O(1)$ ヒット → ハンドラ実行 (`OK_STATIC_DEVICE`) | TEST-VMMIO-11 |
| **C-05** | Undefined FC (FC=11) | Any | N/A | 未定義領域 | 即時トラップ (`TRAP_UNDEFINED_FC`) | TEST-VMMIO-12 |
| **C-06** | SHM (FC=14) | Read | Cold Miss | 有効マッピング | FlatMap二分探索 → TLBリフィル → 物理アクセス (`OK_PHYSICAL`) | TEST-VMMIO-20 |
| **C-07** | SHM (FC=14) | Write | Hit | 書き込み禁止 (`write=False`) | TLBヒット時も権限チェック執行 → トラップ (`TRAP_ACCESS_VIOLATION`) | TEST-VMMIO-17 |
| **C-08** | SHM (FC=14) | Read / Write | Evicted (衝突追い出し) | 有効マッピング | 衝突によるTLB追い出し確認 → 再ミス → FlatMap再探索成功 | TEST-VMMIO-18 |
| **C-09** | SHM (FC=14) | Any | Flushed (Revoke後) | アンマップ済み | TLBフラッシュ → FlatMap不在 → トラップ (`TRAP_UNREGISTERED_PAGE`) | TEST-VMMIO-22, 23 |
| **C-10** | Passthrough (FC=15) | Read / Write | Cold Miss / Hit | 有効マッピング | 物理アドレス直結変換 (`OK_PHYSICAL`) | TEST-VMMIO-25 |

---

### 2.2 テストケース詳細一覧

### アドレス分解・高速バイパス
<!-- traceability: {FastAddressCheck} {MemoryBoundaryCheck} -->

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-VMMIO-01 | Bit31==0はRAMバイパス | - | `access(addr)`、addr<0x8000_0000 | vMMIOテーブルに一切触れず`OK_GUEST_RAM`（TLB miss/hitカウンタが変化しない） | FastAddressCheck, `vmmio_concept.py` `test_ram_bypass_never_touches_page_table` |
| TEST-VMMIO-02 | ゲストRAM境界チェック（比較、マスクなし） | `guest_ram_size`設定済み | 境界ちょうど（size-1）とその1バイト先をアクセス | size-1はOK、size以降は`OUT_OF_BOUNDS`（2の冪制約なし） | FastAddressCheck, `vmmio_concept.py` `test_linear_ram_bound_check_works_for_non_power_of_two_size` |
| TEST-VMMIO-03 | 境界外アドレスの黙示的ラップアラウンド禁止 | 境界外アドレス | アクセス | 必ずトラップし、折り畳んで継続しない | MemoryBoundaryCheck, `vmmio_concept.py` `test_linear_ram_is_bounds_checked_not_waved_through` |

### FlatMap PTE + TLB ({META_FlatMapIndexed})

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-VMMIO-10 | 静的デバイス(FC=12)ページへのアクセスとハンドラ呼び出し | IPCR等をmap_static_device済み | 該当アドレスへアクセス | `OK_STATIC_DEVICE`を返し、登録ハンドラが`(device_metadata, offset, is_write)`で呼ばれる | `vmmio_concept.py` `test_static_device_dispatch` |
| TEST-VMMIO-11 | TLBヒット（2回目以降のアクセス） | 同一ページへ2回アクセス | 2回目のアクセス | `tlb_hits`が増加し、`tlb_misses`は増えない | {META_RestrictedPhysicalAccess}, `vmmio_concept.py` `test_tlb_hit_after_first_walk` |
| TEST-VMMIO-12 | 未定義FCのトラップ分類 | FC=11（予約済み） | アクセス | `TRAP_UNDEFINED_FC`を返す | pysim `test_vmmio_12_undefined_function_code_traps`, `vmmio_concept.py` `test_undefined_fc_traps` |
| TEST-VMMIO-13 | 未登録ページのトラップ分類 | FC=13 DYNAMICの未登録VPN | アクセス | `TRAP_UNREGISTERED_PAGE`を返す | pysim `test_vmmio_13_dynamic_unregistered_page_traps`, `vmmio_concept.py` |
| TEST-VMMIO-14 | Folding XOR HashによるFC間の衝突回避 | FC=12/14/15の同一下位ページ番号 | `tlb_index`を比較 | 異なるTLBスロットに分散する | `vmmio_concept.py` `test_tlb_index_separates_function_codes` |
| TEST-VMMIO-15 | 混在アクセスパターンでの高いTLBヒット率 | 静的デバイス宛先とSHM宛先を交互にアクセス | 10回繰り返す | ヒット率90%以上（スラッシングしない） | `vmmio_concept.py` `test_interleaved_device_and_shm_keep_hitting_the_tlb` |
| TEST-VMMIO-16 | FlatMap登録件数と検索 | 32件のSHMページを登録 | 全件アクセス | 全件が正しく解決される。ホットな作業集合(8件)への繰り返しアクセスは100%ヒット | `vmmio_concept.py` `test_flatmap_pte_registration_and_tlb_caching` |
| TEST-VMMIO-17 | TLBヒット時も権限チェックは必ず実施 | TLBにキャッシュ済みのPTE | 読み出し専用ページへ書き込みアクセス | TLBヒットであってもインライン権限チェックで`TRAP_ACCESS_VIOLATION`となる | {META_RestrictedPhysicalAccess}, `vmmio_concept.py` `test_permission_checks_enforced_even_on_tlb_hit` |
| TEST-VMMIO-18 | Direct-Mapped TLB スロット衝突と置換（Eviction） | 同一ハッシュスロットに衝突する2つのVPN | Aアクセス（充填）→ Bアクセス（置換）→ 再度Aアクセス | Aの再アクセス時にミスが発生し、スロットが無条件上書きされる | `vmmio_concept.py` `test_tlb_slot_conflict_eviction` |
| TEST-VMMIO-19 | SYSCTL syscall doorbell の非提供 | SYSCTLページへアクセスする | SYSCTLアドレスをWASM `load/store`またはvMMIO `access`で参照する | SYSCTLページは未登録として `TRAP_UNREGISTERED_PAGE` になり、syscallは実行されない。host call経路は `runtime_syscall_test_spec.md`で検証する | `runtime_vmmio.md`, `runtime_syscall.md` |

### 3段階セキュリティゲート・SHMマッピング保護 ({OwnershipTransfer})
<!-- traceability: {OwnershipTransfer} {OwnerMismatchTrap} {UnregisteredPageTrap} -->

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
| TEST-VMMIO-28 | アクセス幅がRAM/PTEマッピング境界を越えない | ゲストRAM末尾または実容量を持つSHM/DYNAMIC PTE | 範囲が収まる幅と1バイト越える幅でアクセスする。DYNAMICでは実guestの1/2/4/8byte load/storeも使う | 収まる範囲だけ許可し、範囲外は物理アクセス前に`OUT_OF_BOUNDS`で拒否する。拒否時の実バッファとguest RAMを保存する | `vmmio_concept.py`、pysim `test_vmmio.py`、`test_syscall.py` `test_dynamic_guest_access_checks_full_instruction_width` |
| TEST-VMMIO-29 | FC=13 DYNAMICマッピングのゲスト所有者照合 | ゲストA所有でFC=13 PTEが登録済み | ゲストAとBの両方から同一アドレスを直接`vmmio.access`する | Aのみ`OK_PHYSICAL`、Bは`OWNER_MISMATCH`となる | pysim `test_hal_05_hal_buffer_slice_bounds_and_guest_mapping`, `runtime_vmmio.md` |
| TEST-VMMIO-32 | マップ済みSHMに対する非所有者アクセス | task A所有のFC=14 PTEが登録済みで、Revoke前 | task Bから同じページを読み書きする | `OWNER_MISMATCH`で遮断し、物理メモリを読み書きしない | `{OwnerMismatchTrap}`, pysim `test_vmmio_29_32_cached_owned_mapping_rejects_nonowner_read_and_write` |

### vIRQ原因付き階層ディスパッチ

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-VMMIO-40 | vIRQページと静的原因源表 | `0xC000_3000`の専用ページを有効化 | 原因源表を読み出す | 固定表が`vector_id`、分類、`source_id`、属性を持ち、動的に追加・削除できない | `runtime_vmmio.md` §4.7 |
| TEST-VMMIO-41 | 原因レコードの固定形式 | 物理イベントを原因源へ変換 | 5ワードのイベントを生成してCOOSへ渡す | `vector_id`、`source_id`、`cause_code`、`payload0`、`payload1`の順序・値が保持され、ポインタや可変長領域を含まない | `runtime_vmmio.md` |
| TEST-VMMIO-42 | vIRQ登録スロットの範囲検証 | root・4分類・デバイスの静的スロット | 範囲外スロットへ書き込む | vSoCが拒否し、保留表・有効表を変更しない | `runtime_vmmio.md` §4.7 |
| TEST-VMMIO-43 | 物理デバイスの原因集約 | 1デバイスに複数の`source_id`/`cause_code` | 複数原因を順に発生させる | 1つのデバイスディスパッチャへ集約され、原因値で振り分けられる | `runtime_vmmio.md` §4.7 |
| TEST-VMMIO-44 | 未登録vIRQノードのドロップ | 原因源は有効だが対象ノード未登録 | イベントをCOOSへ投入してドレイン | ゲスト関数を呼び出さず、診断カウンタだけを更新する | `runtime_vmmio.md` §4.7 |

### VDMA ({VDMA})

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| TEST-VMMIO-30 | VDMA制御ページの非提供 | VDMAレジスタアドレスへアクセスする | `0xC000_2000`をvMMIO `access`する | VDMA制御ページは未登録として `TRAP_UNREGISTERED_PAGE` になる | {VDMA}, runtime_syscall_test_spec.md |
| TEST-VMMIO-31 | VDMA転送の検証範囲 | vDMA専用host callを発行する | `fireball:host/vdma.start`の転送結果と転送先権限を確認する | 専用host callによる転送と共通vMMIO権限ゲートを検証する | {VDMA}, runtime_syscall_test_spec.md |

### 実装上の注意点に対応する検証
<!-- traceability: {GOTCHA-VMMIO-01} {GOTCHA-VMMIO-02} {GOTCHA-VMMIO-03} -->

| GOTCHA参照 | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GOTCHA-VMMIO-01 | Bit 31 RAM 高速バイパスのテーブル完全非参照 | `addr < 0x8000_0000` の任意のアドレス | `access(addr)` を実行 | ページテーブル走査（FlatMap walk）および TLB 検索・更新を一切行わず即座に `OK_GUEST_RAM` を返す（`tlb_hits` / `tlb_misses` が不変）。 | [`runtime_vmmio.md`](docs/components/tier2_runtime/runtime_vmmio.md) GOTCHA-VMMIO-01 |
| GOTCHA-VMMIO-02 | Direct-Mapped TLB の 5-bit Folding XOR Hash | 同一下位ページ番号を持つ異なる FC（FC=12 静的デバイス, FC=14 SHM, FC=15 パススルー） | 各ページの `tlb_index` を算出 | `vpn` を 20→10→5 bit と2回の XORで折りたたみ（`temp = vpn ^ (vpn >> 10); temp = temp ^ (temp >> 5); temp & 0x1F`）ことで、異なるFCの同一下位ページを分散する。 | [`runtime_vmmio.md`](docs/components/tier2_runtime/runtime_vmmio.md) GOTCHA-VMMIO-02 |
| GOTCHA-VMMIO-03 | SHM Revoke 後の未マッピング遮断と TLB 即時破棄 | SHM ページ（FC=14）が TLB にキャッシュされた状態 | `revoke_shm(vpn)` を実行 | 対象 TLB スロットを無効化し、FlatMap から削除する。以降のアクセスは `TRAP_UNREGISTERED_PAGE` で拒絶する。 | [`runtime_vmmio.md`](docs/components/tier2_runtime/runtime_vmmio.md) GOTCHA-VMMIO-03 |

## 3. テスト検証実績と網羅状況

pysimの実装テストは [`test_vmmio.py`](experiments/pysim/qa/tier2_runtime/test_vmmio.py) を正本とする。
本スイートはゲートの戻り値、返却物理アドレス、ハンドラへの引数、PTE、TLBを検査する。
実際の物理メモリ読み書きはこのコントローラの責務に含まれず、拒否時には使用可能な物理アドレスが返らないことを確認する。

| ケースID | 実装テスト | 直接観測する結果 |
| :--- | :--- | :--- |
| TEST-VMMIO-01 | `test_vmmio_01_ram_bypasses_page_table_and_tlb` | RAMの成功、全TLBエントリとカウンタの不変 |
| TEST-VMMIO-02、03 | `test_vmmio_02_ram_exact_boundary_without_power_of_two_assumption`、`test_vmmio_03_out_of_bounds_ram_never_wraps` | 非2の冪容量の末尾・超過境界、折り畳まれ得るアドレスの拒否 |
| TEST-VMMIO-10、11 | `test_vmmio_10_11_static_handler_receives_address_fields_on_miss_and_hit` | cold・hit双方のハンドラ引数と結果 |
| TEST-VMMIO-12、13 | `test_vmmio_12_undefined_function_code_traps`、`test_vmmio_13_dynamic_unregistered_page_traps` | 未定義FCと未登録PTEの別トラップ |
| TEST-VMMIO-14、15 | `test_vmmio_14_15_interleaved_function_codes_keep_separate_tlb_slots` | FC間のスロット分離、交互アクセスの正しい解決、18hit・2miss |
| TEST-VMMIO-16 | `test_vmmio_16_all_registered_pages_resolve_and_hot_working_set_hits` | 32ページすべての物理アドレス、8ページ作業集合のhit |
| TEST-VMMIO-17 | `test_vmmio_17_read_only_shm_permission_is_enforced_on_tlb_hit` | warmな読み出し専用PTEへの書込み拒否 |
| TEST-VMMIO-18 | `test_vmmio_18_collision_rewalks_correct_pte_instead_of_reusing_wrong_translation` | A→B→A衝突時の各物理アドレスと再walk |
| TEST-VMMIO-19、30 | `test_vmmio_19_30_legacy_syscall_and_vdma_control_pages_are_unregistered` | 廃止doorbellの未登録トラップ |
| TEST-VMMIO-20〜23 | `test_vmmio_20_to_23_unmap_or_revoke_removes_pte_and_warm_translation` | マップ時の物理アドレス、削除後のPTE/TLB不在、旧所有者と他タスク双方の拒否 |
| TEST-VMMIO-25 | `test_vmmio_25_passthrough_resolves_physical_page_and_offset` | 物理ページ番号とページ内オフセットの保存 |
| TEST-VMMIO-28 | `test_vmmio_28_access_width_cannot_cross_ram_or_shm_mapping`、`test_dynamic_guest_access_checks_full_instruction_width` | RAM、SHM実サイズ、ページ境界の幅検査。実NativeInterpreterのDYNAMIC load/storeでは末尾ちょうどの値と1byte超過の具体trapを検査する |
| TEST-VMMIO-29、32 | `test_vmmio_29_32_cached_owned_mapping_rejects_nonowner_read_and_write` | warmなDYNAMIC/SHMの非所有者読書き拒否、PTEと所有者の保存 |

2026-10-01にLinux・CPython 3.14.6・uv環境で局所スイートを実行した。
実行コマンドを示す。

```bash
UV_CACHE_DIR=/tmp/fireball-test-design-uv uv run --no-sync python -m pytest -q experiments/pysim/qa/tier2_runtime/test_vmmio.py
```

25件成功、0件失敗、0件skipである。
収集件数にはパラメータ化した容量、FC、アドレス、Revokeの水準を含む。
隔離コピーで境界外RAMアドレスを容量マスクで折り畳む変異を作り、TEST-VMMIO-03の4水準が失敗することも確認した。

## 4. 未検証・スコープ外

- ARMv8-M実機でのTLB/FlatMapサイクル数と性能目標の適用はTBD。
- `register-hook` の完全な公開API契約。

- TEST-VMMIO-24のFC=14管理経路限定、TEST-VMMIO-26〜27の連続仮想領域アロケータは、本pysimスイートでは未実装である。参考実装の該当ケースと実装側の管理経路を別途照合する。
- TEST-VMMIO-31のVDMA host callは [`runtime_syscall_test_spec.md`](docs/qa/tier2_runtime/runtime_syscall_test_spec.md) の担当である。本スイートの成功件数には含めない。
- TEST-VMMIO-40〜44のvIRQ階層は本pysimスイートでは未実装である。vSoC側の検査を本書の各原因・登録契約へ対応させる必要がある。
- TEST-VMMIO-01のTLB/PTE状態不変は観測済みである。状態変更を伴わない隠れたテーブル読み出しの完全非参照、および各検索の計算量は、この機能検査だけでは証明しない。
