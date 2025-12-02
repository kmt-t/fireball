# Fireball IPC（Inter-Process Communication）プロトコル設計

**Version:** 0.2.1  
**Date:** 2025-12-02  
**Author:** Takuya Matsunaga / Cline（表現整形・一貫性調整）

---

## 概要（Overview）

Fireball の IPC プロトコルは、CPU 効率と最小の RAM フットプリントを両立しつつ、ログの可視性を担保するために設計された、ハイブリッドな固定長 Key-Value ベースの通信仕様です。本ドキュメントでは用語と表現を統一し、実装時に混乱が生じないように記述を整理しています。

主な設計方針：
- 固定長レコードでパース処理を最小化し、ルーティングを高速化する。  
- ログ用の文字列辞書は Logger サービスの ROM に置き、ランタイム RAM を消費しない。  
- Router はルーティングに専念し、可視化（文字列変換）は Logger が担当する（責務の分離）。

---

## 用語の定義（Terminology）

- 型付きKey-Value：本プロトコルで可変長データを扱う際に使うバイナリエンコードの総称（ドキュメント内では「型付きKey-Value」と表記して統一）。  
- Functional メッセージ：ルーティング対象の機能呼び出し系メッセージ（Device 操作など）。  
- Dict（Dictionary）メッセージ：Logger の ROM 辞書を参照する短いログメッセージ（Key は辞書オフセット）。  
- co_value / shared_heap：大容量バッファを共有して所有権を移すメカニズム（Mode 2）。

---

## 1. コア設計原則

- 責務の分離：Router はルーティングのみを行い、文字列変換や可視化は Logger が行う。  
- 固定長と効率：IPC の基本単位は 8 バイト固定長レコードとし、パースのオーバーヘッドを低減する。  
- ログ辞書の ROM 配置：ログキー文字列はビルド時に ROM に配置し、実行時の RAM 消費をゼロにする。  
- 互換性と拡張性：型付きKey-Value を同一プロトコルで扱えるようにし、可変長データをレコード列で転送する。

---

## 2. IPC メッセージ構造仕様

IPC メッセージは、最大 256 バイト（8 バイト × 32 レコード）の固定長バッファとして CSP チャネルで転送されます。小～中規模のメッセージはこの枠内で完結します。

### 2.1 固定長レコード（8 バイト）

各レコードの構成：

- Type & Scope（1 バイト）: 上位 3 ビットで Scope（処理経路）、下位 5 ビットでデータ型を表す。  
- Key ID（3 バイト）: Scope により解釈が変わる（例えばルート ID、または Logger ROM のバイトオフセット）。  
- Value（4 バイト）: 32bit のデータ、ハンドル、または小さな即値。

合計：8 バイト。

※ エンディアンはプラットフォームの標準（little-endian）を想定します。必要があればフレームにバージョン/エンディアンビットを追加してください（後述の改善案参照）。

### 2.2 Scope（処理経路）と解釈

Type & Scope の上位 3 ビット（Scope）により処理経路を分岐します。代表的な分類：

- Functional（例: `01x`）：機能的 IPC。Key ID をルーティングテーブルのキーとして解釈し、該当サービスへ転送。  
- Dict（例: `001`）：Logger 用辞書参照。Key ID は Logger ROM 辞書へのオフセットを示す。Logger が受け取って可視化処理を行う。

### 2.3 データ型（下位 5 ビット）

データ型コード例（下位 5 ビット）：

- 0x00: INT32  
- 0x01: UINT32  
- 0x02: FLOAT32  
- 0x03: HANDLE（fd／ポインタ表現）  
- 0x04: BOOL  

（必要に応じて拡張可能。新しい型を追加する際は互換性と Logger のデコーダを更新すること。）

---

## 3. コンポーネント責務（整理）

### 3.1 IPC Router

- Functional メッセージを受け取り、Key ID（ルート識別子）に基づいてターゲットの CSP チャネルへ転送する。  
- Logger 用の Dict メッセージは専用の Logger チャネルへフォワードする。  
- 文字列辞書や可視化ロジックを持たず、ステートは最小限に留める。

### 3.2 Logger サービス

- ROM に格納された辞書（自動生成）を使い、Dict メッセージを人間可読なログメッセージへ変換する。  
- Key ID はビルド時に生成される `constexpr` オフセット（inc/log_keys_offsets.hxx）として扱う。  
- 文字列生成はポインタ演算と最小のパースで済ませ、遅延・割り込みに影響しないようにする。

### 3.3 送信側サービス（サブシステム）

- ログ送信時は Dict スコープを使い、Key ID と必要な値（Value）をセットして Logger へ送信する。  
- 機能呼び出しは Functional スコープを使い、Key ID をルートの識別子として指定する。

---

## 4. API とコード例（整理・一貫化）

### 4.1 レコード型（C++）

```cpp
namespace fireball { namespace ipc {

#pragma pack(push,1)
struct ipc_record_t {
  uint8_t type_scope;      // Scope + data_type
  uint8_t key_id_bytes[3]; // 24-bit Key ID (big-endian logical; platform stores little-endian)
  uint32_t value;          // 32-bit value (platform endianness)
};
#pragma pack(pop)

// メッセージは最大 32 レコード
using ipc_message_t = std::array<ipc_record_t, 32>;

} } // namespace fireball::ipc
```

注：実装ではメモリ配置とエンディアンの取り扱いを明示して下さい（上の設計ではプラットフォーム準拠の little-endian を想定）。

### 4.2 Logger 用ヘルパ（C++）

```cpp
// inc/log_keys_offsets.hxx をインクルードして使用する例
logger::log_sender logger;
logger.log_uint32(log_keys::HAL_DEVICE_READ_SUCCESS, bytes_read);
```

logger::log_sender の内部は、Dict スコープを作成して `send_record` を呼び出すだけです。

---

## 5. シリアライゼーションモード（整理）

本プロトコルは用途に応じた 3 つのモードをサポートします。表現を統一し、用語も整えています。

### Mode 1 — 型付きKey-Value（可変長）
- 特性：可変長・構造化データの転送。所有権不要。バイナリの kv エンコードを使用してレコード列で分割して送る。  
- 主用途：ioctl パラメータ、複雑なメタデータ、ログの補足情報。

例：ioctl のパラメータを kv エンコードして複数レコードに分割して送信する（エンコード関数は `kv_encode_*` 系を推奨）。

### Mode 2 — 参照ベース（shared heap / co_value）
- 特性：大きなバッファのやり取り。所有権移譲が必要。shared_heap/co_value を利用。  
- 主用途：read/write、バイナリデータ転送。

実装上の注意：value フィールドに共有ヒープ上のハンドル（またはインデックス）を入れ、受け取り側が所有権を取得する。バッファは必ず受け取り側で解放または返却すること。

---

## 6. ビルド／自動生成（明確化）

- ログ辞書は CSV から自動生成する（`log_keys.csv` → `inc/log_keys_offsets.hxx` と `src/logger_dictionary.cxx`）。  
- 自動生成スクリプトはビルド時に実行し、辞書とオフセットの整合性を担保する。  
- 生成物はバージョン管理（テンプレート／バージョンスタンプ）を付与すると差分追跡が容易。

---

## 7. パフォーマンス注記（整形）

- Router のルーティングはテーブル参照と CSP enqueue のみで完結し、<50 サイクルを見込む。  
- Logger の Dict 処理は O(1)（constexpr オフセットによるポインタ演算）で高速。  
- Mode 2 の大容量転送は共有ヒープと HAL の遅延に依存するため、<500–2000 サイクルを想定。

---

## 8. 実装チェックリスト（簡潔化）

- [ ] `log_keys.csv` を整備する。  
- [ ] `generate_log_dict.py`（CSV → offsets / dictionary）を実装／統合する。  
- [ ] Router のルーティング実装（Functional / Dict の振り分け）を実装する。  
- [ ] Logger の ROM 辞書ロードと Dict 処理を実装する。  
- [ ] Mode 2 の shared_heap / co_value API の実装・テストを行う。  
- [ ] 例示コードとテストケースを追加する（Logger、GPIO、ioctl）。

---

## 9. 表記・用語の一貫性（本変更の要点）

- ドキュメント内で「型付きKey-Value」という表記に統一しました（旧表記：型付きKey-Value形式、MessagePack など）。  
- サンプル関数名はドキュメント内で `kv_encode_*` を想定する表記に統一しています（実装名はプロジェクト規約に合わせてください）。  
- Router / Logger / shared_heap の責務を明確に記述し、重複した表現は整理しました。

---

## 10. 今後（改善候補: 参考）

（任意）将来的に行うと良い改善点一覧（短く）：
- フレームヘッダにバージョン・エンディアンビットを追加して互換性を保証する。  
- Dict メッセージのチェックサムや簡易署名を追加して改ざん検知を行う。  
- kv エンコードの仕様書（バイト列フォーマット・分割ルール）を別ドキュメントで厳密化する。

--- 

## 付録：サンプル（簡潔版）

```cpp
// Logger 送信（例）
ipc::ipc_record_t rec{};
rec.type_scope = ipc::ipc_record::type_scope::make_dict_log(ipc::ipc_record::data_type::UINT32);
rec.key_id = log_keys::HAL_DEVICE_READ_SUCCESS;
rec.value = bytes_read;
router_->send(loggger_id, rec);

// Functional ルーティング（例）
ipc::ipc_record_t cmd{};
cmd.type_scope = ipc::ipc_record::type_scope::make_functional(ipc::ipc_record::data_type::UINT32);
cmd.key_id = SERVICE_GPIO_WRITE;
cmd.value = 1;
router_->send(hal_id, cmd);
```
