"""
examples/producer_consumer.py: 有界バッファにおける生産者・消費者モデルの検証
"""

from tools.verifier import (
    State, ModelChecker, state_model, rule, invariant,
    generate_markdown_report
)

MAX_CAPACITY = 3

@state_model(name="ProducerConsumer", allow_deadlock=True)
class ProducerConsumerModel:
    """
    有界バッファ (Capacity=3) に対する生産者と消費者の並行処理モデル
    状態:
      - buffer_count: 0..MAX_CAPACITY
      - produced_total: 通算生産数
      - consumed_total: 通算消費数
    """
    def init_state(self) -> dict:
        return {
            'buffer_count': 0,
            'produced_total': 0,
            'consumed_total': 0
        }

    @rule(name="Produce", when=lambda s: s['buffer_count'] < MAX_CAPACITY and s['produced_total'] < 5)
    def produce(self, s: State) -> State:
        return s.update(
            buffer_count=s['buffer_count'] + 1,
            produced_total=s['produced_total'] + 1
        )

    @rule(name="Consume", when=lambda s: s['buffer_count'] > 0)
    def consume(self, s: State) -> State:
        return s.update(
            buffer_count=s['buffer_count'] - 1,
            consumed_total=s['consumed_total'] + 1
        )

    # 不変式
    @invariant(name="BufferBounds", description="バッファ件数は 0 以上 MAX_CAPACITY 以下でなければならない")
    def inv_buffer_bounds(self, s: State) -> bool:
        return 0 <= s['buffer_count'] <= MAX_CAPACITY

    @invariant(name="ConservationOfItems", description="生産数 = 消費数 + バッファ残数")
    def inv_item_conservation(self, s: State) -> bool:
        return s['produced_total'] == s['consumed_total'] + s['buffer_count']

def run():
    checker = ModelChecker(ProducerConsumerModel.model)
    result = checker.verify()
    report = generate_markdown_report(result, "ProducerConsumer")
    print(report)
    return result

if __name__ == "__main__":
    run()
