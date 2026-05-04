# 仮想メモリ・アドレス空間の再設計 (32bit)

現状の vMMIO 直接探索から、階層的なアドレスデコード構造へ変更する。

## 1. アドレス空間のビット分割
WASMページサイズ(64KB = $2^{16}$)およびご提案の構成に基づき、32bitアドレスを以下のように定義する。

- `0xF0000000` (4 bits): **Function Code**
- `0x0F000000` (4 bits): **L1 Page Table Index**
- `0x00FF0000` (8 bits): **L2 Page Index** (flat_mapインデックス)
- `0x0000FFFF` (16 bits): **Offset** (ページ内オフセット)

### デコードロジック
- **L1 (4bit)**: ハンドラまたはL2テーブル（`std::flat_map<uint8_t, vmmio_handler>`）へのデスパッチに使用。
- **L2 (8bit)**: `Function Code` によっては、L2インデックスを直接オフセットの下位として扱う、あるいはフラットマップのキーとして使用することで柔軟にページを管理する。

### 特徴
- 柔軟性: `Function Code` に依存して、L2の解釈を変更可能。
- 高速性: 4bitのL1により、最初の分岐を最小化。8bitのL2により、フラットマップのルックアップパフォーマンスを最大化。

## 2. アドレス構造体定義案 (C++イメージ)
```cpp
struct vmmio_address {
    uint32_t func_code : 4;
    uint32_t page_index : 12; // 2^12 = 4096 ページ
    uint32_t offset : 16;     // 2^16 = 64KB (ページサイズ)
};
```

## 3. 実装上の変更点
- `VmmioController`: アドレス受領時に上記構造体へキャストし、`func_code` ごとに処理をディスパッチ。
- `flat_map`: `func_code` をキーとするのではなく、各 `func_code` に紐づく `page_index` を管理する。
  - `std::flat_map<uint32_t /* page_index */, vmmio_handler>` で高速なルックアップを行う。

## 4. 移行戦略
1. 現行の `vMMIO_STATIC_MAP` 二分探索ロジックを、新設するアドレスデコーダへ疎結合化する。
2. デコーダの出力として抽象的な `vmmio_handler_id` を生成し、`flat_map` で解決する。
