# Fireball RSP（Remote Serial Protocol）仕様と説明

**Version:** 0.1.0
**Date:** 2025-11-30
**Author:** Takuya Matsunaga

---

## 概要

本ドキュメントは Fireball デバッガ バックエンド向け **RSP（Remote Serial Protocol）仕様** を説明し、設計判断を明記するものです。

**疑問：** stdin/stdout ではなく RSP を使うべきか？
**答：** 開発・テスト時は stdin/stdout で良い。本番・ハードウェアでは RSP 必須。

---

## 1. デバッガ トランスポート オプション

### 1.1 選択肢 1: stdin/stdout（POSIX - 開発用）

**用途**：x86-64 シミュレータ、CI/CD テスト、ローカル開発

**利点：**
- 実装简潔
- ハードウェア依存性なし
- POSIX 環境全対応

**欠点：**
- 実ハードウェア デバッグ不可
- localhost のみ
- 実時間デバッグ困難

**実装例：**

```cpp
void debugger_read_command(std::string& cmd) {
  cmd.clear();
  char c;
  while (std::cin.get(c)) {
    if (c == '\n') break;
    cmd += c;
  }
}

void debugger_send_response(const std::string& resp) {
  std::cout << resp << std::endl;
}
```

### 1.2 選択肢 2: RSP over UART（ハードウェア - 本番）

**用途**：実ハードウェア（STM32、nRF52、RISC-V）

**利点：**
- 業界標準（GDB、lldb 全対応）
- UART ハードウェア対応全プラットフォーム
- IDE 拡張機能整備（VSCode Cortex-Debug）
- エラー検出（チェックサム）

**欠点：**
- 実装複雑（~300 行）
- UART ハードウェア必須
- オーバーヘッド（16 進エンコーディング）

### 1.3 選択肢 3: TCP/IP over Network（将来）

**用途**：リモート デバッグ（Ethernet/Wi-Fi）

**状態**：Phase 3 未実装、将来対応予定

---

## 2. Fireball 戦略：ハイブリッド アプローチ

### 2.1 設計

```
┌────────────────────────────────────────┐
│   デバッガ サービス（コア ロジック）    │
│   - ブレークポイント管理                │
│   - レジスタ/メモリ アクセス             │
│   - 単一コマンド ハンドラ インターフェース │
└──────────────┬──────────────────────────┘
               │
     ┌─────────┼──────────┐
     │         │          │
     ▼         ▼          ▼
┌─────────┐┌──────────┐┌─────────┐
│ UART    ││ stdio    ││ TCP/IP  │
│トランス│ │トランス  ││トランス  │
│ポート  ││         ││（将来）   │
│(本番) ││(テスト) ││         │
└─────────┘└──────────┘└─────────┘
```

**メリット：** 単一プロトコル ハンドラ、複数トランスポート実装

### 2.2 実装パターン

```cpp
class debugger_transport {
 public:
  virtual ~debugger_transport() = default;
  virtual std::optional<std::string> read_command() = 0;
  virtual void send_response(const std::string& data) = 0;
};

// UART トランスポート（本番ハードウェア）
class uart_transport : public debugger_transport {
  // RSP: $<data>#<checksum>
  std::optional<std::string> read_command() override;
  void send_response(const std::string& data) override;
};

// stdio トランスポート（シミュレータ/テスト）
class stdio_transport : public debugger_transport {
  // シンプル行プロトコル: <command>\n
  std::optional<std::string> read_command() override;
  void send_response(const std::string& data) override;
};

class debugger_service {
 private:
  std::unique_ptr<debugger_transport> transport_;

 public:
  void event_loop() {
    while (true) {
      auto cmd = transport_->read_command();  // 全トランスポート対応
      if (!cmd) continue;
      auto resp = handle_command(*cmd);      // 同一ハンドラ
      transport_->send_response(resp);
    }
  }
};
```

---

## 3. RSP 完全仕様（概要）

### 3.1 パケット構造

```
$<data>#<checksum>
│     │  │         │
│     │  │         └─ チェックサム（2 16 進数字）
│     │  └─────────── デリミタ
│     └───────────── コマンド/レスポンス
└───────────────── 開始マーク
```

**チェックサム計算：**

```cpp
uint8_t calculate_checksum(const std::string& data) {
  uint8_t sum = 0;
  for (char c : data) {
    sum += (uint8_t)c;
  }
  return sum;  // 下位 8 ビット
}
```

### 3.2 コア コマンド

| コマンド | 名前 | フォーマット | 返却 |
|---------|------|----------|------|
| `g` | レジスタ読み取り | `$g#<cksum>` | `$<hexdata>#<cksum>` |
| `m` | メモリ読み取り | `$m<addr>,<len>#<cksum>` | `$<hexdata>#<cksum>` |
| `c` | 実行継続 | `$c#<cksum>` | 実行（ブレークで停止） |
| `s` | ステップ実行 | `$s#<cksum>` | 1 命令実行 |
| `z0` | ブレークポイント削除 | `$z0,<addr>,<kind>#<cksum>` | `$OK#<cksum>` |
| `Z0` | ブレークポイント設定 | `$Z0,<addr>,<kind>#<cksum>` | `$OK#<cksum>` |
| `?` | 停止理由 | `$?#<cksum>` | `$S05#<cksum>`（SIGTRAP） |
| `H` | スレッド選択 | `$H<op><tid>#<cksum>` | `$OK#<cksum>` |

### 3.3 データ交換例

```
Host → Target:
  $m1000,10#1f

分解:
  $ : 開始
  m : メモリ読み取り
  1000,10 : アドレス 0x1000、16 バイト
  # : デリミタ
  1f : チェックサム

Target → Host:
  $48656c6c6f20576f726c64#ab

分解:
  $ : 開始
  48656c6c6f... : データ（16 進）= "Hello World"
  # : デリミタ
  ab : チェックサム
```

---

## 4. プラットフォーム選択基準

### 4.1 決定ツリー

```
これは本番ハードウェアか？
  ├─ YES: RSP over UART 使用
  │       （標準、IDE 統合、実績あり）
  └─ NO: x86-64 シミュレータか？
         ├─ YES: stdin/stdout 使用
         │       （簡潔、外部ツール不要）
         └─ NO: やはりハードウェア
                └─ RSP over UART 使用
```

### 4.2 推奨

| プラットフォーム | トランスポート | 理由 |
|---------|----------|------|
| **ARM Cortex-M4** | RSP/UART | 標準、UART 常装備 |
| **nRF52840** | RSP/UART | Nordic ボード UART サポート、GDB 対応 |
| **RISC-V（WCH）** | RSP/UART | 低リソース制約、UART 最適 |
| **x86-64 POSIX** | stdin/stdout | テスト用、ハードウェア不要 |
| **CI/CD（GitHub Actions）** | stdin/stdout | 自動テスト、シリアル ハードウェア無し |

---

## 5. テスト戦略

### 5.1 ユニット テスト（stdin/stdout）

```cpp
debugger_transport_mock transport;
transport.inject_command("$m1000,4#<cksum>");

auto response = debugger_service->process_command("m1000,4");
assert_response_matches(response, "$<hexdata>#<cksum>");
```

### 5.2 統合テスト（QEMU + RSP）

```bash
# ターミナル 1: QEMU シミュレータ
qemu-arm-softmmu -m 128M -serial stdio -kernel fireball.elf

# ターミナル 2: GDB クライアント
gdb ./fireball.elf
(gdb) target remote localhost:3333
(gdb) break 0x2000
(gdb) continue
```

### 5.3 ハードウェア テスト（実 STM32）

```bash
# ST-Link USB ターゲット接続
gdb ./fireball.elf
(gdb) target remote /dev/ttyUSB0 115200
(gdb) break main
(gdb) load
(gdb) continue
```

---

## 6. 実装チェックリスト

- [ ] `debugger_transport` 抽象インターフェース定義
- [ ] `uart_transport` RSP プロトコル実装
- [ ] `stdio_transport` シンプル行プロトコル実装
- [ ] コア コマンド ハンドラ：
  - [ ] メモリ読み書き
  - [ ] レジスタアクセス
  - [ ] ブレークポイント設定/削除
  - [ ] 実行継続/ステップ実行
  - [ ] クエリ（停止理由、スレッド情報）
- [ ] インタプリタ統合（各命令でブレークポイント確認）
- [ ] GDB テスト確認
- [ ] VSCode Cortex-Debug 拡張機能テスト

---

## まとめ

**stdin/stdout vs RSP か？**

✅ **開発・テスト**：stdin/stdout で十分（シンプル、ハードウェア不要）
❌ **本番・ハードウェア**：RSP over UART 必須（標準、IDE 統合）

Fireball の解決：
- 抽象 `debugger_transport` インターフェース
- 複数実装（UART、stdio、TCP/IP 将来）
- 単一コマンド ハンドラ（全トランスポート対応）

結果：**開発時は簡潔、本番時は業界標準** を両立。
