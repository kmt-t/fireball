# BUG-0005 `main.py` が起動できない

| 項目 | 内容 |
| :--- | :--- |
| 重大度 | 中 |
| 状態 | 修正済み |
| 対象 | [`main.py`](experiments/pysim/main.py) |
| 発見日 | 2026-09-19 |

## 1. 現象

pysim のエントリポイント `main.py` が、起動時の import で失敗する。

`system` モジュールが `ShmSlice` を提供していない。

## 2. 再現手順

```bash
.venv/Scripts/python.exe experiments/pysim/main.py
```

期待結果は、ゲストタスクの実行報告が出力されることである。

実際の結果は、次のエラーである（2026-09-19 実測）。

```text
ImportError: cannot import name 'ShmSlice' from 'system'
```

## 3. 関連する不整合

`main.py` は `sysv.console.write(...)` を呼ぶ。`System` は `console` 属性を持たない（別のテスト作成中に `AttributeError` で確認した）。

`main.py` を最後に更新した後、`System` の公開面が変わったと考えられる。この点は履歴で確認していない。

## 4. 影響

- [`README.md`](experiments/pysim/README.md) は、`main.py` を「エントリポイント CLI」として記載している。この記載と実態が異なる。
- `main.py` は、単体テストと統合シナリオのどちらからも実行されなかった。そのため、テストでは検出されなかった。

## 5. 修正と検証

- `main.py` を現行の `System` の公開面へ移植した。stdout 転送は `sysv.transport`、HALバッファは `sysv.pool` を使う。
- 所有者タスクの戻り値でバッファハンドルを受け渡し、敵対タスクの越境参照が拒否されることを確認する。
- `main.py` を実行する単体テスト `TEST-ENTRY-01`（`test_entrypoint.py`）を追加し、全スイートへ登録した。
- 同じ種類の劣化を、以後のテスト実行で検出できる。
