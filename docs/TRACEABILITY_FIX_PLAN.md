# トレーサビリティ修正計画

**生成日**: 2026-05-11  
**スクリプト**: `.claude/scripts/traceability_audit.py`  
**対象**: コンポーネント仕様のキーワード未紐付けセクション

---

## 📊 サマリー

| 対象 | 数 |
|:---|--:|
| **修正対象セクション** | 180 件 |
| **対象コンポーネント** | 23 ファイル |
| **優先度別** | Tier2(Core/Interface): 33件 / JIT: 62件 / Runtime: 67件 / Platform: 22件 / Other: 6件 |

---

## 🎯 修正グループ（ファイル別）

各グループの見出しをクリックして詳細を参照してください。

### [Tier 1/2] Core & Interface Components (優先度: 高)

| ファイル | NG数 | 修正推奨順位 |
|:---|--:|:---|
| `core/os_coos.md` | 3 | **1** |
| `core/os_scheduler.md` | 9 | **2** |
| `core/system_config.md` | 4 | **3** |
| `core/system_logging.md` | 7 | **4** |
| `core/system_syscall.md` | 6 | **5** |
| `interface/interface_wit.md` | 8 | **6** |
| `interface/ipc_router.md` | 3 | **7** |
| `interface/system_service.md` | 7 | **8** |
| **小計** | **47 件** | |

### [Tier 3] JIT & Runtime Components (優先度: 中)

| ファイル | NG数 | 修正推奨順位 |
|:---|--:|:---|
| `jit/jit_assembler_constexpr.md` | 9 | **9** |
| `jit/jit_compiler.md` | 11 | **10** |
| `jit/jit_engine_copy_patch.md` | 8 | **11** |
| `jit/jit_runtime_entry.md` | 8 | **12** |
| `jit/jit_runtime_hotspot.md` | 7 | **13** |
| `runtime/runtime_loader.md` | 17 | **14** |
| `runtime/runtime_vmmio.md` | 11 | **15** |
| `runtime/runtime_vsoc.md` | 8 | **16** |
| `runtime/runtime_interpreter.md` | 10 | **17** |
| `runtime/debug/debug_manager.md` | 9 | **18** |
| `runtime/debug/debug_gdb_rsp.md` | 6 | **19** |
| **小計** | **104 件** | |

### [Platform & Other] (優先度: 低)

| ファイル | NG数 | 修正推奨順位 |
|:---|--:|:---|
| `platform/platform_hal.md` | 12 | **20** |
| `platform/platform_memory.md` | 10 | **21** |
| `core/system_config_details.md` | 1 | **22** |
| `runtime/wasm_instruction.md` | 6 | **23** |
| **小計** | **29 件** | |

---

## 📋 詳細修正リスト

### 1️⃣ core/os_coos.md (3 件)

```
  1. L 21    3.2 内部ブロック図
  2. L 59    5.1 `coos_harness` (システムハーネス)
  3. L 80    6.1 直交表: CSP通信と状態遷移
```

**修正方針**: 
- `3.2 内部ブロック図`: `{3TierSeparation}` 等（親セクションの設計キーワード）
- `5.1 coos_harness`: `{ComponentHarness}` 等（Harness実装関連）
- `6.1 直交表`: 検証関連キーワード（TBD）

---

### 2️⃣ core/os_scheduler.md (9 件)

```
  1. L 16    3.2 内部ブロック図
  2. L 75      初期化 (`init-scheduler`)
  3. L 89      タスク生成 (`spawn`)
  4. L103      `spawn_task`
  5. L113      `yield`
  6. L121      `run`
  7. L129      `set_idle_handler`
  8. L136      `notify-interrupt` (内部 API)
  9. L 70  5. インターフェイス設計
```

**修正方針**: API個別メソッドは、対応する要求キーワード（`{CooperativeMultitasking}`, `{DirectContextSwitch}` 等）を記載

---

### 3️⃣ core/system_config.md (4 件)

```
  1. L 14    3.2 内部ブロック図
  2. L 40    4.2 状態遷移図
  3. L 48    5.1 公開API
  4. L 46  5. インターフェイス定義
```

---

### 4️⃣ core/system_logging.md (7 件)

```
  1. L 11    3.1 データ構造
  2. L 16    3.2 内部ブロック図
  3. L 34      `Logger` クラス
  4. L 80    4.4 状態遷移図
  5. L117      ログイベント記録 (`log_event`)
  6. L112    5.1 公開API
  7. L151    6.3 安全性制約と方策
```

---

### 5️⃣ core/system_syscall.md (6 件)

```
  1. L 43    4.1. 引数のパッキング
  2. L 65    5.1. カテゴリ一覧
  3. L103    5.5. IRQ (`0x30`-`0x3F`)
  4. L175    6.1. 役割
  5. L221      8.1.1. 仮想割り込みID
  6. L241  10. トラップ状態プロトコル
```

---

### 6️⃣ interface/interface_wit.md (8 件)

```
  1. L  3  1. 目的 `{WIT_Interface_Purpose}`  ← キーワード記載済みだが本文がなし
  2. L 53    `fireball:host/trap`
  3. L 72    5.1 `fireball:host/timer` (wasi:clocks 準拠)
  4. L 82    5.3 `fireball:host/bus` (Master/Slave Bus)
  5. L110  6. 非同期通知メカニズム
  6. L115  7. フィードバック：WASI 準拠における制約事項
  7. L122  8. 命名規則 (Naming Conventions)
  8. L133    8.1 設計上の留意点
```

---

### 7️⃣ interface/ipc_router.md (3 件)

```
  1. L 15    3.2 内部ブロック図
  2. L 78    4.2 状態遷移図
  3. L116      `register_service`
```

---

### 8️⃣ interface/system_service.md (7 件)

```
  1. L 11    3.1 データ構造
  2. L 14    3.2 内部ブロック図
  3. L 28      `service` (サービス定義)
  4. L 51    4.2 状態遷移図
  5. L 59    4.3 内部シーケンス
  6. L151      リカバリー戦略の種類
  7. L160    5.2 公開API
```

---

### 9️⃣ jit/jit_assembler_constexpr.md (9 件)

```
  1. L 11    3.1 データ構造
  2. L 15    3.2 内部ブロック図
  3. L 28      `riscv::i_type`
  4. L 39      `arm::add_imm`
  5. L  9  3. 静的モデル
  6. L 61    4.1 アルゴリズム
  7. L 59  4. 動的モデル
  8. L 72  5. インターフェイス定義
  9. L 93    6.2 安全性制約
```

---

### 🔟 jit/jit_compiler.md (11 件)

```
  1. L 22    3.2 内部ブロック図
  2. L 50      `jit_harness`
  3. L 60      `jit_context`
  4. L 84      Copy-and-Patch コンパイル手順
  5. L 93      JITトレース検索アルゴリズム
  6. L118      ホットスポット判定 (yield 時)
  7. L127    4.2 状態遷移図
  8. L184    5.1 直行表: 検索・昇格・GC
  9. L230      `lookup_trace`
 10. L238      `get_card_state`
 11. L245      `get_search_range`
```

---

### 1️⃣1️⃣ jit/jit_engine_copy_patch.md (8 件)

```
  1. L 11    3.1 データ構造
  2. L 16    3.2 内部ブロック図
  3. L 28      `CopyAndPatchEngine` クラス
  4. L  9  3. 静的モデル
  5. L 47    4.1 アルゴリズム
  6. L 61    4.2 状態遷移図
  7. L 45  4. 動的モデル
  8. L 83  5. インターフェイス定義
```

---

### 1️⃣2️⃣ jit/jit_runtime_entry.md (8 件)

```
  1. L 12    3.1 データ構造
  2. L 10  3. 静的モデル
  3. L 40    4.1 アルゴリズム
  4. L 51    4.2 状態遷移図
  5. L 38  4. 動的モデル
  6. L 87      `lookup`
  7. L 81  5. インターフェイス定義
  8. L105  6. 制約達成の方策
```

---

### 1️⃣3️⃣ jit/jit_runtime_hotspot.md (7 件)

```
  1. L 11    3.1 データ構造
  2. L  9  3. 静的モデル
  3. L 36    4.1 アルゴリズム
  4. L 34  4. 動的モデル
  5. L 65      `record_execution`
  6. L 59  5. インターフェイス定義
  7. L 84    6.1 性能制約
```

---

### 1️⃣4️⃣ runtime/runtime_loader.md (17 件) ⭐ 最多

```
  1. L 16    3.2 内部ブロック図
  2. L 37      `WasmLoader` クラス
  3. L 53      `BinaryStream`
  4. L 65      `function_accessor` (関数アクセサ)
  5. L 75      `global_accessor` (グローバルアクセサ)
  6. L115    4.3 軽量検証スコープ
  7. L126    4.4 状態遷移図
  8. L164      `prepare`
  9. L175      `load`
 10. L185      `resolve-imports`
 11. L194      `unload`
 12. L204      `lookup`
 13. L213      `get-section`
 14. L224      `lookup-export-func`
 15. L233      `get-function`
 16. L159    5.1 公開API
 17. L157  5. インターフェイス定義
```

---

### 1️⃣5️⃣ runtime/runtime_vmmio.md (11 件)

```
  1. L 36    3.2 内部ブロック図
  2. L 58      `vmmio_address` (アドレスフィールド定義)
  3. L118      `VmmioController` クラス
  4. L127      `vmmio_pte_static` (FC=4 Static Device PTE)
  5. L151      `vmmio_pte_tier3` (FC=6/7 Tier 3 PTE)
  6. L176      FC ごとの flat_map テーブル
  7. L202    4.1 アルゴリズム: アクセスディスパッチ
  8. L255    4.2 性能分析（Tier別）
  9. L362    4.4 SYSCTL レジスタ詳細
 10. L437      フック登録 (`register-hook`)
 11. L430  5. インターフェイス定義
```

---

### 1️⃣6️⃣ runtime/runtime_vsoc.md (8 件)

```
  1. L 16    3.2 内部ブロック図
  2. L 44      `vsoc_harness`
  3. L 55      `vsoc_context`
  4. L 89    4.2 状態遷移図
  5. L104      WASM実行およびJIT遷移シーケンス
  6. L160      `prepare`
  7. L186      `notify-interrupt`
  8. L231    5.4 URI/IPCインターフェイス
```

---

### 1️⃣7️⃣ runtime/runtime_interpreter.md (10 件)

```
  1. L 11    3.1 データ構造
  2. L 16    3.2 内部ブロック図
  3. L 36      `Interpreter` クラス
  4. L 59      `call_frame` (コールフレーム)
  5. L 70      `control_frame` (制御フレーム)
  6. L112    4.2 状態遷移図
  7. L151      `initialize`
  8. L190    5.2 URI/IPCインターフェイス
  9. L207    6.2 メモリ制約と方策
 10. L215  7. 参考実装リスト
```

---

### 1️⃣8️⃣ runtime/debug/debug_manager.md (9 件)

```
  1. L 11    3.1 データ構造
  2. L 15    3.2 内部ブロック図
  3. L 58    4.1 アルゴリズム
  4. L 62    4.2 状態遷移図
  5. L 56  4. 動的モデル
  6. L 98      デバッガ接続 (`attach`)
  7. L108      コマンド処理 (`poll_commands`)
  8. L 93    5.1 公開API
  9. L 91  5. インターフェイス定義
```

---

### 1️⃣9️⃣ runtime/debug/debug_gdb_rsp.md (6 件)

```
  1. L  8    2.1 セッション管理
  2. L 18    2.2 メモリアクセス
  3. L 25    2.3 レジスタアクセス
  4. L 33    2.4 ブレークポイント
  5. L  6  2. コマンドリスト
  6. L 52  3. 応答形式
```

---

### 2️⃣0️⃣ platform/platform_hal.md (12 件)

```
  1. L 11    3.1 データ構造
  2. L 16    3.2 内部ブロック図
  3. L 31      `device` (デバイス情報)
  4. L 58    4.2 状態遷移図
  5. L 93      データの読み出し
  6. L103      データの書き込み (write)
  7. L119      非標準制御 (control)
  8. L139      `gpio-controller` (物理GPIO制御)
  9. L145      `periodic-timer` (時刻とタイマー)
 10. L151      `bus-master` / `bus-slave` (I2C/SPI通信)
 11. L137    5.2 Tier 3 リソースインターフェイス
 12. L162    5.2 URI/IPCインターフェイス
```

---

### 2️⃣1️⃣ platform/platform_memory.md (10 件)

```
  1. L 11    3.1 データ構造
  2. L  9  3. 静的モデル
  3. L 20      初期化
  4. L 31      `allocate` (kernel/task専用)
  5. L 40      `allocate-shared` (IPC転送データ専用)
  6. L 48      `claim` (IPC受信側)
  7. L 18  4. インターフェイス設計
  8. L 99    8.1 shared-block のリソース化
  9. L 97  8. 設計判断の記録
 10. L116    8.3 check_ownership() の削除
```

---

### 2️⃣2️⃣ core/system_config_details.md (1 件)

```
  1. L 51    2.7 型定義・予約値
```

---

### 2️⃣3️⃣ runtime/wasm_instruction.md (6 件)

```
  1. L 10    2.1 制御フロー命令
  2. L 25    2.2 メモリ命令
  3. L 39    2.3 算術演算命令 (i32)
  4. L 59    2.4 比較命令 (i32)
  5. L  6  2. 命令リスト
  6. L 85    2.6 その他
```

---

## 🚀 修正方法

各ファイルについて：

1. **NGセクションを特定**: 上の詳細リストで当該セクションを確認
2. **親セクションのキーワード参照**: より上位（##）の見出しにあるキーワードを確認
3. **関連キーワド付与**: 行末に `` `{Keyword1}` `{Keyword2}` `` の形式で追加
4. **再検証**: スクリプト再実行して NG が減ったか確認

```bash
python3 .claude/scripts/traceability_audit.py
```

修正例:
```markdown
#### 3.2 内部ブロック図 `{3TierSeparation}` `{ComponentHarness}`
```

---

## 📝 進捗管理

修正完了したら、該当タスクをチェック ✓ してください。

- [ ] 1. os_coos.md
- [ ] 2. os_scheduler.md
- [ ] 3. system_config.md
- ...
