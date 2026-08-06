"""
tools/verifier/jit_performance_simulator.py
Fireball JIT コンパイラ設計書 (jit_compiler.md, jit_runtime_hotspot.md, jit_engine_copy_patch.md)
に 100% 準拠した精緻化パフォーマンスシミュレータ＆総合コスト評価モジュール

仕様書の厳格なダイナミックモデル:
1. 2-bit ホットスポット・ビットマップ (00b:UNEXECUTED -> 01b:EXECUTED -> 10b:HOT -> 11b:COMPILED)
2. Eviction 発生時の EXECUTED(01b) 降格ルール (COMPILED -> EXECUTED: Cache evicted)
3. Copy-and-Patch 単一パス超高速コンパイル (W_compile = 5.0)
4. I-Cache フラッシュ ＋ バンク無効化オーバーヘッド (W_gc_swap = 30.0)
5. N-Buffer (デフォルト 3: 2KB x 3 = 6KB) ＆ 最古バッファ限定 Promote ポリシー
"""

import random
import math
from typing import Dict, List, Tuple


class RefinedFireballJITSimulator:
    """
    Fireball 設計書に 100% 準拠した精緻化 JIT シミュレータ
    """

    def __init__(
        self,
        num_buffers: int = 3,
        buffer_capacity_bytes: int = 2048,
        promote_threshold: int = 2,
        oldest_only_promote: bool = True
    ):
        self.num_buffers = num_buffers
        self.buffer_capacity = buffer_capacity_bytes
        self.promote_threshold = promote_threshold
        self.oldest_only_promote = oldest_only_promote

        # 2-bit ホットスポット・ビットマップ: pc_id -> state (0:UNEXECUTED, 1:EXECUTED, 2:HOT, 3:COMPILED)
        self.hotspot_bitmap: Dict[int, int] = {}

        # N-Buffer キャッシュ管理: index 0: Active, 1..N-1: Old 世代
        self.buffers: List[Dict[int, dict]] = [{} for _ in range(num_buffers)]
        self.used_bytes: List[int] = [0 for _ in range(num_buffers)]

        # 詳細メトリクス
        self.total_requests = 0
        self.jit_hits = 0
        self.lazy_chained_hits = 0
        self.total_search_lookups = 0  # カードグループ絞り込み+二分探索が走ったバッファ面数
        self.interpreter_fallbacks = 0
        self.jit_compilations = 0
        self.gc_swaps = 0
        self.promotions = 0
        self.evictions = 0

    def access_code(self, pc_id: int, code_size_bytes: int):
        self.total_requests += 1
        curr_state = self.hotspot_bitmap.get(pc_id, 0)  # デフォルト 0: UNEXECUTED

        # 1. Active バッファ (index 0) での JIT ヒット
        if pc_id in self.buffers[0]:
            self.jit_hits += 1
            # チェイニング済み想定: 80% は Lazy Chaining (直結0cyc)、20% は二分探索
            if random.random() > 0.8:
                self.total_search_lookups += 1
            else:
                self.lazy_chained_hits += 1

            self.buffers[0][pc_id]["exec_count"] += 1
            self.hotspot_bitmap[pc_id] = 3  # COMPILED
            return

        # 2. Old 世代バッファ (index 1 ~ num_buffers-1) での JIT ヒット
        # Active でミスして探索したため Active の二分探索 +1 面
        self.total_search_lookups += 1

        for b_idx in range(1, self.num_buffers):
            self.total_search_lookups += 1  # 各 Old バッファの探索
            if pc_id in self.buffers[b_idx]:
                self.jit_hits += 1
                item = self.buffers[b_idx][pc_id]
                item["exec_count"] += 1
                self.hotspot_bitmap[pc_id] = 3  # COMPILED

                # Promote 判定: 最古バッファ (index N-1) からのみ Promote
                should_promote = (
                    (not self.oldest_only_promote) or (b_idx == self.num_buffers - 1)
                )

                if should_promote and item["exec_count"] >= self.promote_threshold:
                    if self.used_bytes[0] + code_size_bytes <= self.buffer_capacity:
                        del self.buffers[b_idx][pc_id]
                        self.used_bytes[b_idx] -= code_size_bytes
                        self.buffers[0][pc_id] = item
                        self.used_bytes[0] += code_size_bytes
                        self.promotions += 1
                return

        # 3. JIT キャッシュミス
        self.interpreter_fallbacks += 1

        if curr_state == 0:
            # 0: UNEXECUTED -> 1: EXECUTED (初回はインタープリタ実行)
            self.hotspot_bitmap[pc_id] = 1
            return

        if curr_state in (1, 3):
            # 1: EXECUTED (またはエビクト降格済み) -> 2回目で 2: HOT へ移行しコンパイル要求
            self.hotspot_bitmap[pc_id] = 2

        # 4. 2: HOT 状態 -> Copy-and-Patch JIT コンパイル実行
        self.jit_compilations += 1

        # Active バッファが溢れる場合は Ring Swap (世代ローテーション) 発生
        if self.used_bytes[0] + code_size_bytes > self.buffer_capacity:
            self._trigger_ring_swap()

        # Active バッファへ格納
        if self.used_bytes[0] + code_size_bytes <= self.buffer_capacity:
            self.buffers[0][pc_id] = {
                "size": code_size_bytes,
                "exec_count": 1,
            }
            self.used_bytes[0] += code_size_bytes
            self.hotspot_bitmap[pc_id] = 3  # COMPILED

    def _trigger_ring_swap(self):
        self.gc_swaps += 1
        oldest_buf = self.buffers.pop()
        self.evictions += len(oldest_buf)
        self.used_bytes.pop()

        # 破棄されたコードの Bitmap 状態を EXECUTED(1) に降格 (仕様書 5.1 節ケース6)
        for pc_id in oldest_buf:
            self.hotspot_bitmap[pc_id] = 1

        self.buffers.insert(0, {})
        self.used_bytes.insert(0, 0)


class StandardJITWorkloadGenerator:
    """
    JIT コンパイラ研究 (SPEC CPU, DaCapo, V8/Octane 等) で用いられる標準的なワークロード発生器
    1. Zipf-Mandelbrot べき乗則分布 (Power-law Distribution for function popularity)
    2. 時間的局所性 (Temporal Locality): ループブロックの連続 N 回反復実行
    """

    @staticmethod
    def generate_trace(
        total_code_size_kb: float,
        num_instructions: int = 100000,
        s_parameter: float = 1.2,  # Zipf スキューパラメータ (JIT論文での標準値 1.1~1.3)
        mean_loop_iterations: int = 50,  # ループ反復の平均回数
        seed: int = 42
    ) -> Tuple[List[int], int]:
        random.seed(seed)

        num_blocks = max(2, int((total_code_size_kb * 1024) // 200))
        block_size = 200

        # Zipf 確率分布の重み計算: w_i = 1 / (i ^ s)
        weights = [1.0 / (math.pow(i, s_parameter)) for i in range(1, num_blocks + 1)]
        cum_weights = []
        total_w = sum(weights)
        acc = 0.0
        for w in weights:
            acc += w / total_w
            cum_weights.append(acc)

        def sample_zipf() -> int:
            r = random.random()
            for idx, cw in enumerate(cum_weights):
                if r <= cw:
                    return idx
            return num_blocks - 1

        trace = []
        curr_step = 0

        while curr_step < num_instructions:
            chosen_block = sample_zipf()

            # 幾何分布によるループ反復回数 (mean_loop_iterations)
            p_geom = 1.0 / mean_loop_iterations
            u = random.random()
            iterations = max(1, int(math.ceil(math.log(1.0 - u) / math.log(1.0 - p_geom)))) if u < 1.0 else mean_loop_iterations
            iterations = min(iterations, num_instructions - curr_step)

            # 時間的局所性: 同じループブロックを連続 iterations 回実行
            trace.extend([chosen_block] * iterations)
            curr_step += iterations

        return trace, block_size


def run_powers_of_two_benchmark(num_instructions: int = 100000):
    print("=======================================================================================")
    print(f"   Fireball: ARMv8 (2.5倍コード展開) 精密 JIT 再シミュレーション")
    print(f"   【設定】 1 WASM 命令 -> ARMv8 2.5 命令 (展開倍率 2.5倍, JIT容量占有2.5倍)")
    print(f"   【物理サイクル】 JIT直接=2.5cyc/WASM inst (1.0/ARM inst), Interp=9.5cyc/WASM inst")
    print(f"                   Copy-Patch=80.0cyc/block (250B), GC Swap=250.0cyc")
    print("=======================================================================================")

    ARMV8_EXPANSION_RATIO = 2.5   # WASM -> ARMv8 展開倍率 (2.5倍)
    CYC_JIT_PER_WASM_INST = 2.5   # JITネイティブ実行 (ARMv8 2.5命令 = 2.5 cyc/WASM inst)
    CYC_INTERP_PER_WASM_INST = 9.5 # Interp ディスパッチ実行 (9.5 cyc/WASM inst)
    CYC_COMPILE_BLOCK = 80.0      # Copy-and-Patch コンパイル (250B テンプレートコピー+パッチ)
    CYC_GC_SWAP_FLUSH = 250.0     # I-Cache Flush + カード一括降格 + 環状シフト
    CYC_PROMOTE_COPY = 50.0       # 250B 昇格 memcpy

    INSTS_PER_BLOCK = 25  # 1 WASM トレースブロックあたりの命令数 (25 WASM 命令 = ARMv8 250 バイト)
    ARMV8_BLOCK_SIZE_BYTES = int(INSTS_PER_BLOCK * 4 * ARMV8_EXPANSION_RATIO)  # 250 バイト

    buffer_sizes_kb = [2, 4, 8, 16, 32, 64, 128]

    workloads = [
        ("10 KB WASM アプリ", 10 / 1024),
        ("1.0 MB Linux OS", 1.0),
    ]

    for wl_name, size_mb in workloads:
        print(f"\n==================== 【 ワークロード: {wl_name} (ARMv8 2.5倍展開) 】 ====================")
        num_block_accesses = num_instructions // INSTS_PER_BLOCK
        access_sequence, _ = StandardJITWorkloadGenerator.generate_trace(
            total_code_size_kb=size_mb * 1024, num_instructions=num_block_accesses, s_parameter=1.2, mean_loop_iterations=50
        )

        # 純粋インタープリタ実行のトータル CPU サイクル数 (全 WASM 命令数 x 9.5cyc)
        baseline_cycles = num_instructions * CYC_INTERP_PER_WASM_INST

        print(
            f"{'1面サイズ':<10} | {'総容量':<8} | {'JITヒット率':<10} | {'スピードアップ':<14} | {'GC Swap(キャッシュアウト)':<22} | {'Evict破棄件数':<12} | {'Hot昇格(Promote)':<14}"
        )
        print("-" * 110)

        # Pure Interpreter 基準
        print(
            f"{'JITなし':<10} | {'0 KB':<8} | {0.0:8.2f} % | {1.0:12.2f} 倍    | {'0 回':<22} | {'0 件':<12} | {'0 回':<14}"
        )
        print("-" * 110)

        for buf_kb in buffer_sizes_kb:
            cap_bytes = buf_kb * 1024

            sim3 = RefinedFireballJITSimulator(
                num_buffers=3, buffer_capacity_bytes=cap_bytes, promote_threshold=2, oldest_only_promote=True
            )
            for pc in access_sequence:
                sim3.access_code(pc_id=pc, code_size_bytes=ARMV8_BLOCK_SIZE_BYTES)

            hit3 = (sim3.jit_hits / sim3.total_requests) * 100

            jit_cycles = sim3.jit_hits * INSTS_PER_BLOCK * CYC_JIT_PER_WASM_INST
            interp_cycles = sim3.interpreter_fallbacks * INSTS_PER_BLOCK * CYC_INTERP_PER_WASM_INST
            compile_cycles = sim3.jit_compilations * CYC_COMPILE_BLOCK
            gc_swap_cycles = sim3.gc_swaps * CYC_GC_SWAP_FLUSH
            promote_cycles = sim3.promotions * CYC_PROMOTE_COPY
            fast_exit_cycles = sim3.interpreter_fallbacks * 1.0
            search_cycles = sim3.total_search_lookups * 10.0

            total_cycles = (
                jit_cycles + interp_cycles + compile_cycles + gc_swap_cycles + promote_cycles + fast_exit_cycles + search_cycles
            )
            spd3 = baseline_cycles / total_cycles

            print(
                f"{buf_kb:<2} KB / 面  | {buf_kb * 3:<4} KB  | {hit3:8.2f} % | {spd3:12.2f} 倍速   | {sim3.gc_swaps:12d} 回              | {sim3.evictions:10d} 件 | {sim3.promotions:12d} 回"
            )


def run_standard_academic_benchmark(num_instructions: int = 100000):
    print("=======================================================================================")
    print(f"   JIT 研究標準モデル (Zipf-Mandelbrot べき乗則 ＋ ループ時間的局所性モデル)")
    print(f"   【設定】 Zipf パラメータ s=1.2 (標準) / ループ平均反復回数 = 50 回")
    print(f"   【物理サイクル】 JIT直接=1.0cyc / Interp=9.5cyc / Copy-Patch=60.0cyc / I-Cache Flush=250.0cyc")
    print("=======================================================================================")

    CYC_JIT_EXEC = 1.0           # JITネイティブ命令実行 (1.0 サイクル/命令)
    CYC_INTERP_EXEC = 9.5        # Interpディスパッチ+ポインタ操作 (9.5 サイクル/命令)
    CYC_COMPILE_BLOCK = 60.0     # Copy-and-Patch テンプレートコピー+パッチ (1ブロック20命令あたり60cyc)
    CYC_GC_SWAP_FLUSH = 250.0    # I-Cache flush (fence.i) + ビットマップ降格 + 環状シフト (250cyc/Swap)
    CYC_PROMOTE_COPY = 35.0      # 最古バッファから Active への昇格 memcpy (35cyc/Promote)

    workloads = [
        ("10 KB WASM アプリ", 10 / 1024),
        ("1.0 MB Linux OS", 1.0),
    ]

    configs = [
        ("Interpのみ (JITなし)", 0, 0, False),
        ("旧標準 ダブル 4KB (2KB x 2)", 2, 2048, True),
        ("新標準 トリプル 6KB (2KB x 3)", 3, 2048, True),
        ("中型 トリプル 32KB (10.6KB x 3)", 3, 10922, True),
        ("標準 トリプル 64KB (21.3KB x 3)", 3, 21845, True),
        ("大型 トリプル 128KB (42.6KB x 3)", 3, 43690, True),
        ("超大型 トリプル 256KB (85.3KB x 3)", 3, 87381, True),
    ]

    for wl_name, size_mb in workloads:
        print(f"\n==================== 【 ワークロード: {wl_name} (Zipf 学術標準) 】 ====================")
        access_sequence, block_size = StandardJITWorkloadGenerator.generate_trace(
            total_code_size_kb=size_mb * 1024, num_instructions=num_instructions, s_parameter=1.2, mean_loop_iterations=50
        )

        baseline_cycles = num_instructions * CYC_INTERP_EXEC

        print(f"{'JIT構成':<32} | {'JITヒット率':<10} | {'総CPUサイクル数':<14} | {'実質スピードアップ':<20}")
        print("-" * 88)

        for cfg_name, n_buf, cap, is_jit in configs:
            if not is_jit:
                total_cycles = baseline_cycles
                hit_rate = 0.0
                speedup = 1.0
            else:
                sim = RefinedFireballJITSimulator(
                    num_buffers=n_buf, buffer_capacity_bytes=cap, promote_threshold=2, oldest_only_promote=True
                )
                for pc in access_sequence:
                    sim.access_code(pc_id=pc, code_size_bytes=block_size)

                hit_rate = (sim.jit_hits / sim.total_requests) * 100
                
                # サイクル精度での厳格物理コスト算定 (仕様書 3.1 & 4.1 節 カードグループ二分探索)
                jit_cycles = sim.jit_hits * CYC_JIT_EXEC
                interp_cycles = sim.interpreter_fallbacks * CYC_INTERP_EXEC
                compile_cycles = sim.jit_compilations * CYC_COMPILE_BLOCK
                gc_swap_cycles = sim.gc_swaps * CYC_GC_SWAP_FLUSH
                promote_cycles = sim.promotions * CYC_PROMOTE_COPY
                
                # JIT 検索コスト (仕様書 4.1 節 Step 1~3 準拠)
                # 1. Card-Marking Fast-Exit: COMPILED でないカードは 1.0cyc で即時 Fast-Exit
                fast_exit_cycles = sim.interpreter_fallbacks * 1.0

                # 2. Active / Old バッファ二分探索コスト (Card Group 絞り込み 2.0cyc + 二分探索 log2(K)*2.0cyc)
                #    平均エントリ数 K=16 とすると log2(16)*2 = 8cyc -> バッファ 1 面あたり約 10.0cyc
                #    Lazy Chaining (チェイニング済みヒット) は 0.0cyc (検索完全スキップ)
                search_lookup_cycles = sim.total_search_lookups * 10.0
                
                total_cycles = (
                    jit_cycles + interp_cycles + compile_cycles + gc_swap_cycles + promote_cycles + fast_exit_cycles + search_lookup_cycles
                )
                speedup = baseline_cycles / total_cycles

            print(
                f"{cfg_name:<30} | {hit_rate:8.2f} % | {total_cycles:14.0f} cyc | {speedup:14.2f} 倍速"
            )


if __name__ == "__main__":
    run_powers_of_two_benchmark(num_instructions=100000)





