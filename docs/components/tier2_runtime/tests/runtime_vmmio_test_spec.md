# vMMIO テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: `docs/components/tier2_runtime/runtime_vmmio.md`
参考実装: `docs/components/tier2_runtime/concepts/vmmio_concept.py`

Bit31によるRAM/vMMIO高速分岐、FlatMap PTE + 16エントリDirect-Mapped TLB、Tier1/2/3の3層セキュリティゲート、SHM所有権チェック、VDMA、TLB無効化を検証する。

## 2. テストケース一覧

### アドレス分解・高速バイパス (§1, §3.3)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| VMMIO-01 | Bit31==0はRAMバイパス | - | `access(addr)`、addr<0x8000_0000 | vMMIOテーブルに一切触れず`OK_GUEST_RAM`（TLB miss/hitカウンタが変化しない） | §1, vmmio_concept.py `test_ram_bypass_never_touches_page_table` |
| VMMIO-02 | ゲストRAM境界チェック（比較、マスクなし） | `guest_ram_size`設定済み | 境界ちょうど（size-1）とその1バイト先をアクセス | size-1はOK、size以降は`OUT_OF_BOUNDS`（2の冪制約なし） | §1「統一境界チェック」, vmmio_concept.py `test_linear_ram_bound_check_works_for_non_power_of_two_size` |
| VMMIO-03 | 境界外アドレスの黙示的ラップアラウンド禁止 | 境界外アドレス | アクセス | 必ずトラップし、折り畳んで継続しない | §1「トラップは必須」 |

### FlatMap PTE + TLB (§1, §4.8)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| VMMIO-10 | 静的デバイス(FC=12)ページへのアクセスとハンドラ呼び出し | SYSCTL等をmap_static_device済み | 該当アドレスへアクセス | `OK_SYSCALL`を返し、登録ハンドラが`(syscall_metadata, offset, is_write)`で呼ばれる | §4.4, vmmio_concept.py `test_static_device_syscall_dispatch` |
| VMMIO-11 | TLBヒット（2回目以降のアクセス） | 同一ページへ2回アクセス | 2回目のアクセス | `tlb_hits`が増加し、`tlb_misses`は増えない | §1「ダイレクトマップ方式ソフトウェアTLB」, vmmio_concept.py `test_tlb_hit_after_first_walk` |
| VMMIO-12 | 未定義FCのトラップ分類 | FC=13（未割当） | アクセス | `TRAP_UNDEFINED_FC`を返す | vmmio_concept.py `test_undefined_fc_traps` |
| VMMIO-13 | 未登録ページのトラップ分類 | 有効なFCだが該当VPNが未登録 | アクセス | `TRAP_UNREGISTERED_PAGE`を返す | §1「未登録ページ」 |
| VMMIO-14 | Folding XOR HashによるFC間の衝突回避 | FC=12/14/15の同一下位ページ番号 | `tlb_index`を比較 | 異なるTLBスロットに分散する | §1, vmmio_concept.py `test_tlb_index_separates_function_codes` |
| VMMIO-15 | 混在アクセスパターンでの高いTLBヒット率 | Syscall宛先とSHM宛先を交互にアクセス | 10回繰り返す | ヒット率90%以上（スラッシングしない） | vmmio_concept.py `test_interleaved_syscall_and_shm_keep_hitting_the_tlb` |
| VMMIO-16 | FlatMap登録件数と検索 | 32件のSHMページを登録 | 全件アクセス | 全件が正しく解決される。ホットな作業集合(8件)への繰り返しアクセスは100%ヒット | vmmio_concept.py `test_flatmap_pte_registration_and_tlb_caching` |
| VMMIO-17 | TLBヒット時も権限チェックは必ず実施 | TLBにキャッシュ済みのPTE | 権限を後から変更（例:Revoke） | TLBヒットであっても最新の権限判定が適用される（TLBは探索スキップのみを担う） | §4.8「権限チェック」 |

### 3層セキュリティゲート・SHM所有権 (§1「3層」, §4.6)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| VMMIO-20 | SHM所有者のみアクセス許可 | `map_shm_page(owner_id=7)` | `current_task_id=7`でアクセス | `OK_PHYSICAL` | §4.6, vmmio_concept.py `test_shm_owner_isolation` |
| VMMIO-21 | SHM非所有者は拒否 | 同上 | `current_task_id=9`でアクセス（TLBに既にキャッシュ済みでも） | `TRAP_OWNER_MISMATCH` | 同上 |
| VMMIO-22 | Revoke時のTLB即時無効化 | SHMページがTLBに常駐 | `revoke_shm_owner(vpn)` | 該当TLBエントリが無効化され、次回アクセスは強制的にFlatMap再walkになる | §4.6「Revoke」, vmmio_concept.py `test_revoke_invalidates_tlb_and_blocks_access_during_flight` |
| VMMIO-23 | Revoke後（in-flight中）は誰もアクセス不可 | Revoke直後 | 旧所有者・他タスク双方でアクセス | 両方とも`TRAP_OWNER_MISMATCH`（`FB_TASK_ID_FLIGHT`状態） | §4.6, vmmio_concept.py |
| VMMIO-24 | FC=14への書き込みはIPCルータのみ | 通常のゲストアクセス | FC=14へ直接書き込もうとする | 「FC=14エントリへの書き込みはIPCルータのみが行う」制約に反する経路が存在しないことを確認 | §3.3.2「FC=14 (SHM) エントリへの書き込みは IPCルータのみが行う」 |
| VMMIO-25 | PASSTHROUGH(FC=15)の物理アドレス変換 | `map_passthrough_page`済み | アクセス | `phys_addr = (pte.phys_page << 12) | offset`で正しく解決 | §4.3「PASSTHROUGH アドレス変換」 |

### VDMA (§4.2, §4.5)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| VMMIO-30 | REG_VDMA_*レジスタへの設定と`REG_VDMA_CTRL`起動 | レジスタに`SRC`/`DST`/`COUNT`設定 | `CTRL`のSTARTビットを1にする | 指定範囲が転送される | §4.5 |
| VMMIO-31 | SHM宛先へのVDMA転送時の所有権チェック | `dst`がFC=14アドレス、呼び出し元が非所有者 | VDMA実行 | `dispatch_access`と同一の権限チェックで拒否される | §4.5「SHMアドレスを転送先/元に指定した場合...同一の権限チェック」 |

## 3. テスト検証実績と網羅状況

- 仕様書に定義された各テストケース（不変条件・境界条件・エラー処理）の検証手順と期待結果を定義。

## 4. 未検証・スコープ外

- Cortex-M33実機でのTLB/FlatMapの実際のサイクル数（`{vMMIO_TLB}`の性能目標自体）。
- `register-hook`（§5.1）の完全な公開API契約。
