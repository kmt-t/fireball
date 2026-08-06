"""
examples/bank_transfer.py: 銀行口座間の資金移動と不変式 (残高保存法則) 検証
"""

from tools.verifier import (
    State, ModelChecker, state_model, rule, invariant,
    generate_markdown_report
)

INITIAL_TOTAL = 1000

@state_model(name="BankTransfer", allow_deadlock=True)
class BankTransferModel:
    """
    口座 A, B, C 間での資金移動モデル
    意図的なバグを含むルールを用意し、モデルチェッカーが不変式違反と反例トレースを発見することを示すデモ。
    """
    def init_state(self) -> dict:
        return {
            'account_a': 500,
            'account_b': 300,
            'account_c': 200,
        }

    # 正しい送金: A -> B (100円)
    @rule(name="Transfer_A_to_B", when=lambda s: s['account_a'] >= 100)
    def transfer_a_to_b(self, s: State) -> State:
        return s.update(
            account_a=s['account_a'] - 100,
            account_b=s['account_b'] + 100
        )

    # 正しい送金: B -> C (50円)
    @rule(name="Transfer_B_to_C", when=lambda s: s['account_b'] >= 50)
    def transfer_b_to_c(self, s: State) -> State:
        return s.update(
            account_b=s['account_b'] - 50,
            account_c=s['account_c'] + 50
        )

    # 意図的な不具合付き送金: C -> A で手数料二重引き去りバグ
    @rule(name="BuggyTransfer_C_to_A", when=lambda s: s['account_c'] >= 100)
    def buggy_transfer_c_to_a(self, s: State) -> State:
        # C から 100円引くが A には 90円しか入らない（10円が消失する不変式違反）
        return s.update(
            account_c=s['account_c'] - 100,
            account_a=s['account_a'] + 90
        )

    # 不変式: 3口座の合計金額は常に初期値 1000円 と一致しなければならない (Conservation of Money)
    @invariant(name="TotalMoneyConservation", description="全口座の合計残高は常に 1000 円で一定")
    def inv_total_conservation(self, s: State) -> bool:
        total = s['account_a'] + s['account_b'] + s['account_c']
        return total == INITIAL_TOTAL

    @invariant(name="NoNegativeBalance", description="どの口座も残高が負になってはならない")
    def inv_no_negative(self, s: State) -> bool:
        return s['account_a'] >= 0 and s['account_b'] >= 0 and s['account_c'] >= 0

def run():
    checker = ModelChecker(BankTransferModel.model)
    result = checker.verify()
    report = generate_markdown_report(result, "BankTransfer")
    print(report)
    return result

if __name__ == "__main__":
    run()
