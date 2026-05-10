#!/usr/bin/env python3
"""
EventDrivenCOOS_ThreeState.tla の TLC 検証スクリプト

3状態モデル（READY, RUNNING, BLOCKED）の仕様検証を実行し、
以下を確認：
  - 状態遷移の不変条件
  - キュー有限性とイベント型安全性
  - 単一実行（RUNNING は最大1個）
  - イベント消費（デッドロック回避）
  - Call-Reply ペアリング
  - アイドル時割り込み復帰
"""

import subprocess
import sys
import os

def run_tlc_verification():
    """TLC検証を実行"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    module_path = os.path.join(script_dir, "EventDrivenCOOS_ThreeState")
    config_path = os.path.join(script_dir, "EventDrivenCOOS_ThreeState.cfg")

    print("=" * 80)
    print("EventDrivenCOOS_ThreeState.tla - TLC Verification")
    print("=" * 80)
    print(f"\nModule: {module_path}")
    print(f"Config: {config_path}\n")

    # TLC コマンド実行
    cmd = [
        "tlc",
        "-config", config_path,
        module_path
    ]

    print(f"Executing: {' '.join(cmd)}\n")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)

        print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)

        return result.returncode == 0

    except FileNotFoundError:
        print("ERROR: 'tlc' コマンドが見つかりません。")
        print("TLC (TLA+ toolbox) をインストールしてください。")
        print("  https://lamport.azurewebsites.net/tla/toolbox.html")
        return False

def print_verification_summary():
    """検証項目のサマリー出力"""
    print("\n" + "=" * 80)
    print("検証項目一覧")
    print("=" * 80)

    items = [
        ("INV-1: StateConsistency",
         "すべてのタスク状態が {READY, RUNNING, BLOCKED} に属する"),

        ("INV-2: QueueBounded",
         "イベントキューサイズが QUEUE_MAX_SIZE を超えない"),

        ("INV-3: EventValidity",
         "キュー内のすべてのイベントが有効な構造"),

        ("INV-4: SingleRunning",
         "RUNNING 状態のタスクは最大1個（単一スレッド）"),

        ("INV-5: OwnershipInvariant",
         "メッセージの所有権が常に一意"),

        ("LIVENESS-1: EventualDispatch",
         "キューが空でない限り、最終的に Dispatch が実行される（デッドロック回避）"),

        ("LIVENESS-2: CallReplyPairing",
         "Call の後、最終的に Reply が完結し、caller が READY に戻る"),

        ("LIVENESS-3: IdleRecovery",
         "すべてのタスクが BLOCKED の場合、最終的に割り込みで復帰するか、\n"
         "                 アイドル時に割り込みが再検出される"),
    ]

    for idx, (name, desc) in enumerate(items, 1):
        print(f"\n{idx}. {name}")
        print(f"   {desc}")

def main():
    print_verification_summary()

    success = run_tlc_verification()

    print("\n" + "=" * 80)
    if success:
        print("✓ 検証成功：3状態モデルの仕様が正式に検証されました")
    else:
        print("✗ 検証失敗：上記を参照してください")
    print("=" * 80)

    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())
