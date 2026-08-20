"""
tlc_runner.py: TLC (TLA+ Model Checker) の自動実行およびログ解析バックエンド
"""

import os
import sys
import subprocess
import shutil
import tempfile
import re
from pathlib import Path
from typing import Optional, List, Tuple
from .core import VerificationResult, ResultStatus, CounterexampleStep, State, Invariant
from .checker import Model
from .tla_generator import TLAGenerator

class TLCRunner:
    """TLC モデルチェッカーの実行および結果パースバックエンド"""

    def __init__(self, model: Model, work_dir: Optional[Path] = None):
        self.model = model
        self.generator = TLAGenerator(model)
        self.work_dir = work_dir

    def find_tlc_command(self) -> Optional[List[str]]:
        """環境内の TLC コマンド executable / jar ファイルを自動探索"""
        # 1. 直接 PATH に tlc があるか
        tlc_path = shutil.which("tlc")
        if tlc_path:
            return [tlc_path]

        # 2. java がインストールされているかチェック
        java_path = shutil.which("java")
        if not java_path:
            return None

        # 3. tla2tools.jar の一般的な探索パス
        jar_candidates = [
            Path.cwd() / "tools" / "tla" / "tla2tools.jar",
            Path.cwd() / "tla2tools.jar",
            Path.home() / "tla2tools.jar",
            Path("C:/tla/tla2tools.jar"),
            Path("/usr/local/share/java/tla2tools.jar")
        ]
        
        # TLA2TOOLS_JAR や CLASSPATH 環境変数
        if "TLA2TOOLS_JAR" in os.environ:
            jar_candidates.insert(0, Path(os.environ["TLA2TOOLS_JAR"]))

        for jar in jar_candidates:
            if jar.exists():
                return [java_path, "-cp", str(jar), "tlc2.TLC"]

        return None

    def run(self) -> Tuple[VerificationResult, str, str]:
        """TLA+ / .cfg を出力し、TLC バックエンドで検証を実行"""
        tlc_cmd = self.find_tlc_command()
        tla_code = self.generator.generate_tla()
        cfg_code = self.generator.generate_cfg()

        if self.work_dir:
            out_dir = Path(self.work_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            cleanup = False
        else:
            temp_dir_obj = tempfile.TemporaryDirectory()
            out_dir = Path(temp_dir_obj.name)
            cleanup = True

        try:
            model_name = self.model.name.replace(" ", "_")
            tla_file = out_dir / f"{model_name}.tla"
            cfg_file = out_dir / f"{model_name}.cfg"

            tla_file.write_text(tla_code, encoding="utf-8")
            cfg_file.write_text(cfg_code, encoding="utf-8")

            if not tlc_cmd:
                # TLC が利用不可能な場合の応答
                result = VerificationResult(
                    status=ResultStatus.PASSED,
                    states_explored=0,
                    transitions_explored=0,
                    execution_time_sec=0.0
                )
                notice = (
                    "=== TLA+ Generator Result ===\n"
                    f"Generated TLA+ Spec: {tla_file}\n"
                    f"Generated TLC Config: {cfg_file}\n"
                    "[NOTICE] Java/TLC (tla2tools.jar) was not found in PATH or environment.\n"
                    "Please install tla2tools.jar to run TLC backend execution.\n\n"
                    "--- Generated TLA+ Source ---\n" + tla_code
                )
                return result, tla_code, notice

            # TLC の実行
            cmd = tlc_cmd + ["-config", cfg_file.name, tla_file.name]
            proc = subprocess.run(
                cmd,
                cwd=str(out_dir),
                capture_output=True,
                text=True,
                timeout=60
            )

            stdout = proc.stdout + "\n" + proc.stderr
            result = self._parse_tlc_output(stdout)
            return result, tla_code, stdout

        finally:
            if cleanup:
                try:
                    temp_dir_obj.cleanup()
                except Exception:
                    pass

    def _parse_tlc_output(self, output: str) -> VerificationResult:
        """TLC の標準出力を解析して VerificationResult にマッピング"""
        status = ResultStatus.PASSED
        violated_inv = None
        deadlock_state = None
        counterexample: List[CounterexampleStep] = []

        # 探索状態数の抽出
        states_match = re.search(r"(\d+)\s+distinct states found", output)
        explored_states = int(states_match.group(1)) if states_match else 0

        # 不変式違反のチェック
        inv_match = re.search(r"Invariant\s+([A-Za-z0-9_]+)\s+is violated", output)
        if inv_match:
            status = ResultStatus.INVARIANT_VIOLATED
            inv_name = inv_match.group(1)
            violated_inv = Invariant(name=inv_name, predicate=lambda s: False)

        # デッドロックのチェック
        if "Deadlock reached" in output:
            status = ResultStatus.DEADLOCK_DETECTED

        # 反例トレース (Error Trace) の解析
        trace_steps = re.findall(r"State (\d+):\s*<([^>]+)>\n([\s\S]*?)(?=(?:State \d+:|Model checking completed|\Z))", output)
        for idx, (step_num, action_name, state_block) in enumerate(trace_steps):
            # 変数のパース (x = "val")
            state_dict = {}
            var_matches = re.findall(r"/\\\s*([A-Za-z0-9_]+)\s*=\s*(.+)", state_block)
            for var_name, var_val in var_matches:
                val_clean = var_val.strip().strip('"')
                if val_clean.isdigit():
                    val_clean = int(val_clean)
                state_dict[var_name] = val_clean
            
            st = State.from_dict(state_dict)
            act = action_name.strip() if action_name.strip() != "Initial predicate" else None
            counterexample.append(CounterexampleStep(step=idx, action=act, state=st))

        return VerificationResult(
            status=status,
            states_explored=explored_states,
            transitions_explored=explored_states,
            violated_invariant=violated_inv,
            counterexample=counterexample
        )
