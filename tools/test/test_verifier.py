import unittest
import tempfile
from pathlib import Path
from tools.verifier.core import State, Rule, Invariant, ResultStatus
from tools.verifier.checker import Model, ModelChecker
from tools.verifier.state_machine import StateMachine
from tools.verifier.dsl import rule, invariant
from tools.verifier.tla_generator import TLAGenerator

class TestVerifier(unittest.TestCase):
    def test_state_equality_and_hash(self):
        s1 = State.from_dict({"counter": 1, "status": "idle"})
        s2 = State.from_dict({"status": "idle", "counter": 1})
        s3 = State.from_dict({"counter": 2, "status": "idle"})

        self.assertEqual(s1, s2)
        self.assertEqual(hash(s1), hash(s2))
        self.assertNotEqual(s1, s3)

    def test_tla_generator_output(self):
        model = Model("SimpleCounter")
        model.add_initial_state(State.from_dict({"val": 0}))

        def cond(s):
            return s.get("val", 0) < 3

        def act(s):
            return State.from_dict({"val": s.get("val", 0) + 1})

        model.add_rule(Rule("increment", cond, act))
        model.add_invariant(Invariant("val_bounded", lambda s: s.get("val", 0) <= 3))

        gen = TLAGenerator(model)
        tla_code = gen.generate_tla()
        cfg_code = gen.generate_cfg()

        self.assertIn("MODULE SimpleCounter", tla_code)
        self.assertIn("val_bounded", tla_code)
        self.assertIn("SPECIFICATION Spec", cfg_code)
        self.assertIn("INVARIANT val_bounded", cfg_code)

    def test_state_machine_to_tla(self):
        sm = (StateMachine("TrafficLight")
              .variable("state", "Red")
              .transition("timer_expired", {"state": "Red"}, {"state": "Green"})
              .transition("timer_expired", {"state": "Green"}, {"state": "Yellow"})
              .transition("timer_expired", {"state": "Yellow"}, {"state": "Red"}))

        model = sm.to_model()
        gen = TLAGenerator(model)
        tla_code = gen.generate_tla()

        self.assertIn("MODULE TrafficLight", tla_code)
        self.assertIn("timer_expired", tla_code)

    def test_model_checker_verify_tla(self):
        model = Model("DSLTest")
        model.add_initial_state(State.from_dict({"status": "INIT"}))

        @rule(name="start", when=lambda s: s.get("status") == "INIT")
        def start_rule(s: State) -> State:
            return State.from_dict({"status": "RUNNING"})

        @invariant(name="valid_status")
        def check_status(s: State) -> bool:
            return s.get("status") in ["INIT", "RUNNING"]

        model.add_rule(start_rule._rule_obj)
        model.add_invariant(check_status._invariant_obj)

        with tempfile.TemporaryDirectory() as tmpdir:
            checker = ModelChecker(model)
            res = checker.verify(work_dir=Path(tmpdir))
            # VerificationResult should be returned
            self.assertIsNotNone(res)
            self.assertIn(res.status, [ResultStatus.PASSED, ResultStatus.INVARIANT_VIOLATED, ResultStatus.DEADLOCK_DETECTED])

if __name__ == "__main__":
    unittest.main()
