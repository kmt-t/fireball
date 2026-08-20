---
name: formal-verifier
description: pyModelChecking (CTL / Kripke 構造) を用いた Fireball コンポーネントの形式検証モデル作成・実行スキル。docs/components/<tier>/formal/ 配下のモデル検査スクリプトを作成・更新・検証する際に使用する。
---

# Formal Verifier (pyModelChecking 形式検証スキル)

## 概要

Fireball では、各コンポーネントの仕様書（`os_scheduler.md`, `ipc_router.md`, `jit_compiler.md` 等）に `{VERIFY_FORMAL}` を宣言し、対応するコンポーネントディレクトリ内の `formal/*.py` に配置された形式検証スクリプトを `spec-integrator` パイプライン経由で自動検証します。

---

## ディレクトリ構成とモデル配置

```
docs/components/
├── tier1_core/
│   ├── os_scheduler.md          # {VERIFY_FORMAL}
│   └── formal/
│       └── mutex_model.py       # pyModelChecking による排他制御・進捗安全性モデル
├── tier1_interface/
│   ├── ipc_router.md            # {VERIFY_FORMAL}
│   └── formal/
│       └── csp_handoff_model.py # CSP 所有権移譲の安全性モデル
└── tier2_jit/
    ├── jit_compiler.md          # {VERIFY_FORMAL}
    └── formal/
        └── jit_cache_model.py   # JIT 命令キャッシュの整合性モデル
```

---

## モデル記述パターン (pyModelChecking)

```python
from pyModelChecking import Kripke
from pyModelChecking.CTL import modelcheck, AG, EF, AF, And, Not, Imply, AtomicProposition

def verify():
    # 1. 状態集合、初期状態、遷移関係、ラベルの定義
    S = ["s_idle", "s_busy"]
    S0 = {"s_idle"}
    R = [("s_idle", "s_busy"), ("s_busy", "s_idle")]
    L = {"s_idle": {"idle"}, "s_busy": {"busy"}}
    km = Kripke(S=S, S0=S0, R=R, L=L)

    # 2. CTL 論理式の記述
    # 例: 常に (idle と busy は排他)
    phi_mutex = AG(Not(And(AtomicProposition("idle"), AtomicProposition("busy"))))
    sat_mutex = modelcheck(km, phi_mutex)
    is_safe = km.S0.issubset(sat_mutex)

    if is_safe:
        print("Model verification: PASS")
        return 0
    else:
        print("Model verification: FAIL")
        return 1

if __name__ == "__main__":
    import sys
    sys.exit(verify())
```

---

## 検証の実行

```powershell
# 全ドキュメント検証パイプライン（Formal Gate を含む）
powershell tools/run_all_tests.ps1 -clean
```
