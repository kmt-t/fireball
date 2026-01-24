# RAM内訳整理プラン（デバッグ無効/JIT有効/ログ最小/ゲスト8KB）

## 目的
現行設計のRAM内訳（どこに余裕があるか）を根拠付きで整理し、64KB前提での余裕と不確定要素を可視化し、32KB最小構成検討の前提資料を作る。

## 前提条件（ユーザ指定・更新）
- ターゲットRAM 64KB
- ゲストは**リニアメモリのみ**（ゲストヒープは廃止）
- ゲストリニアメモリ 24KB
- JIT有効
- ログバッファ 512B
- RSPパケットバッファ 1KB（デバッグ無効でも固定確保する想定）

## 参照元（メモリ関連の根拠）
- 要求の最小構成RAM制約: [`docs/orders/requires/list.md:94`](docs/orders/requires/list.md:94)
- ヒープパーティション最小サイズ: [`docs/orders/architecture/overview.md:131`](docs/orders/architecture/overview.md:131)
- システムコンフィグのデフォルト値: [`docs/orders/requires/system_configs.md:8`](docs/orders/requires/system_configs.md:8)
- vSoCメモリ目標/ゲストRAM配置: [`docs/orders/components/vsoc.md:159`](docs/orders/components/vsoc.md:159)
- インタープリタの固定スタック方針: [`docs/orders/components/interpreter.md:169`](docs/orders/components/interpreter.md:169)
- HALバッファ/ログ/デバッグの固定サイズ: [`docs/orders/requires/system_configs.md:23`](docs/orders/requires/system_configs.md:23)

## 現状の「数値がある」項目（暫定合算候補）
### A. アーキテクチャのヒープパーティション最小値（案A）
- ネイティブヒープ 4KB
- vSoCヒープ 2KB
- サブシステム・Tier1サービスヒープ 2KB
- wasmリニアメモリ 8KB
合計: 16KB（最小サイズの合計）

### B. システムコンフィグのデフォルト値（案B：64KB標準構成）
- Kernel heap 8KB
- Runtime heap 4KB
- Subsystem heap 4KB
- Service heap 4KB
- Guest heap 0KB（廃止反映）
合計: 20KB

### C. 32KB最小構成案（案C）
- Kernel heap 4KB
- Runtime heap 2KB
- Subsystem heap 2KB
- Service heap 2KB
- JIT cache 2KB
- HAL buffer 512B × 4 = 2KB
- Log buffer 256B
- RSP packet buffer 512B
- Guest linear memory 8KB
合計: 22.75KB

### D. 固定バッファ類（案A/B共通）
- JIT cache 4KB
- HAL buffer 1024B × 4 = 4KB
- Log buffer 512B
- RSP packet buffer 1KB
合計: 9.5KB

### E. ゲストRAM
- 案A/B: 24KB（標準）
- 案C: 8KB（最小）

## 合算（案A/案B/案C）
> デバッグ無効/JIT有効 前提

| 案 | ヒープ合計 | バッファ類 | ゲストリニアメモリ | 合計 | ターゲットとの差分 |
|---|---|---|---|---|---|
| 案A (最小値) | 8KB | 9.5KB | 24KB | 41.5KB | +22.5KB (vs 64KB) |
| 案B (標準) | 20KB | 9.5KB | 24KB | 53.5KB | +10.5KB (vs 64KB) |
| 案C (最小構成) | 10KB | 4.75KB | 8KB | 22.75KB | +9.25KB (vs 32KB) |

### 補足
- 案Aはアーキの最小値を採用し、ゲスト24KBを維持。
- 案Bはコンフィグ値を採用し、ゲスト24KBを維持。64KBに対して十分な余裕（約10KB）がある。
- 案Cは各リソースを削り、ゲストを8KBに制限することで32KBに収まることを確認。

## 注意点（矛盾/未確定）
- `FB_CONF_GUEST_RAM_SIZE` は 64KB 例示であり、前提の 24KB と一致しない。 [`docs/orders/requires/system_configs.md:30`](docs/orders/requires/system_configs.md:30)
- ヒープパーティション表の最小値（16KB）と、コンフィグのヒープ合計（44KB）が一致しない。
- ゲストヒープ廃止の設計反映が未完了（要求/設計/コンフィグの整合が必要）。
- 構造体サイズ、配列長、IPCメッセージなどの実サイズは未記載。
- デバッグ無効でもRSPバッファを1KB確保する扱いは、設計上の明文化が必要。 [`docs/orders/requires/system_configs.md:45`](docs/orders/requires/system_configs.md:45)

## 未確定サイズの候補（要確認/バックログ候補）
- `execution_context_t`, `call_frame_t`, `control_frame_t` の実サイズ（実装依存）。 [`docs/orders/components/interpreter.md:38`](docs/orders/components/interpreter.md:38)
- `opcode_handler_table` と `debug_handler_table` のサイズ（命令数とテーブル配置）。 [`docs/orders/components/interpreter.md:80`](docs/orders/components/interpreter.md:80)
- `debug_command_queue_t` の深さ・バッファサイズ（RSPパーサからの供給量）。 [`docs/orders/components/debugger.md:8`](docs/orders/components/debugger.md:8)
- IPCルータのレジストリ配列サイズ（`FB_CONF_ROUTER_MAX_SERVICES` の具体メモリ量）。 [`docs/orders/requires/system_configs.md:17`](docs/orders/requires/system_configs.md:17)
- HALの `device_t` 配列と `hal_buffer_t` プールのオーバーヘッド（構造体サイズ）。 [`docs/orders/components/hal.md:8`](docs/orders/components/hal.md:8)
- vMMIOマップ配列の実サイズ（`FB_CONF_VMMIO_MAX_REGIONS` に依存）。 [`docs/orders/requires/system_configs.md:30`](docs/orders/requires/system_configs.md:30)
- ロギングのリングバッファ要素サイズ（`log_entry_t` サイズ）。 [`docs/orders/components/logging.md:24`](docs/orders/components/logging.md:24)
- サービスレジストリ `service_registry_t` の実サイズ（URI/エントリ構成）。 [`docs/orders/components/services.md:8`](docs/orders/components/services.md:8)

## 次のアクション（整理方針）
1. 案A（アーキ最小値）と案B（コンフィグ値）を併記した合算表を作成。
2. 固定バッファ類の有効/無効条件を整理（RSPバッファ1KBの設計反映）。
3. 未確定サイズをリスト化し、バックログ候補として記録。
4. 64KB前提での余裕を算出し、32KBへの削減候補を提示。

## 32KB最小構成の成立性（方向性）
- 優先方針: **ゲストリニアメモリを 24KB → 16KB 以下へ削減**（ゲスト機能制限を許容）
- 影響範囲: `FB_CONF_GUEST_RAM_SIZE` と vSoC/インタープリタのメモリ制約記述の更新が必要。 [`docs/orders/requires/system_configs.md:30`](docs/orders/requires/system_configs.md:30)

## 追加で必要な決定事項（ユーザ確認予定）
- ヒープサイズは「アーキの最小値」か「コンフィグ値」どちらを前提にするか。
- デバッグ無効時に RSP パケットバッファを 1KB 確保する方針を、設計に明文化するか。
- ゲストリニアメモリ 24KB を前提に、コンフィグ/要求/設計の整合をどう取るか。
