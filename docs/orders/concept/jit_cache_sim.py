import random
from collections import deque, Counter

class JITCacheSim:
    """
    Simulation of Fireball JIT Cache mechanism.
    - Active/Old double buffer
    - 2-hit threshold for JIT compilation
    - Copy-GC eviction (Swap Active/Old)
    """

    def __init__(self, partition_size_bytes=2048, history_size=16, jit_threshold=2, card_size=64):
        self.partition_size = partition_size_bytes
        self.history_size = history_size
        self.jit_threshold = jit_threshold
        self.card_size = card_size
        
        self.active_cache = {}  # trace_id -> size
        self.old_cache = {}     # trace_id -> size
        self.active_used = 0
        
        self.history = deque(maxlen=history_size)
        self.compiled_cards = set() # Set of card_ids (pc // card_size)
        
        # Statistics
        self.stats = {
            'interp_exec': 0,
            'jit_hit_active': 0,
            'jit_hit_old': 0,
            'jit_compile': 0,
            'evictions': 0,
            'total_exec': 0
        }

    def execute(self, trace_id, trace_size):
        self.stats['total_exec'] += 1
        card_id = trace_id // self.card_size # Simplified trace_id as PC

        # 1. Check Active Cache
        if trace_id in self.active_cache:
            self.stats['jit_hit_active'] += 1
            return "JIT_ACTIVE"
        
        # 2. Check Old Cache (Promotion)
        if trace_id in self.old_cache:
            self.stats['jit_hit_old'] += 1
            self._promote_to_active(trace_id, trace_size)
            return "JIT_OLD"
        
        # 3. Check Card Status (On-demand Compile)
        if card_id in self.compiled_cards:
            self._compile_to_active(trace_id, trace_size)
            return "JIT_ON_DEMAND"

        # 4. Interpreter Execution
        self.stats['interp_exec'] += 1
        self.history.append(trace_id)
        
        # 5. Check Hotspot Detection
        counts = Counter(self.history)
        if counts[trace_id] >= self.jit_threshold:
            self.compiled_cards.add(card_id)
            self._compile_to_active(trace_id, trace_size)
            return "INTERP_JIT_TRIGGERED"
            
        return "INTERP"

    def _promote_to_active(self, trace_id, trace_size):
        # Remove from old, add to active
        # Note: In real Copy-GC, this might happen during eviction, 
        # but here we simulate promotion on hit.
        if trace_id in self.old_cache:
            del self.old_cache[trace_id]
        self._add_to_active(trace_id, trace_size)

    def _compile_to_active(self, trace_id, trace_size):
        self.stats['jit_compile'] += 1
        self._add_to_active(trace_id, trace_size)

    def _add_to_active(self, trace_id, trace_size):
        if self.active_used + trace_size > self.partition_size:
            self._evict()
        
        if trace_id not in self.active_cache:
            self.active_cache[trace_id] = trace_size
            self.active_used += trace_size

    def _evict(self):
        self.stats['evictions'] += 1
        # Copy-GC: Old is cleared, Active becomes Old, new Active is empty
        self.old_cache = self.active_cache
        self.active_cache = {}
        self.active_used = 0

    def get_report(self, jit_speedup=6.0):
        total = self.stats['total_exec']
        if total == 0: return {}
        
        hit_count = self.stats['jit_hit_active'] + self.stats['jit_hit_old']
        hit_rate = hit_count / total
        
        # Performance Estimation using Amdahl's Law
        # Speedup = 1 / ((1 - P) + P/S)
        # P = hit_rate, S = jit_speedup
        estimated_speedup = 1.0 / ((1.0 - hit_rate) + (hit_rate / jit_speedup))
        
        return {
            **self.stats,
            'hit_rate_percent': hit_rate * 100,
            'estimated_speedup': estimated_speedup,
            'active_count': len(self.active_cache),
            'old_count': len(self.old_cache)
        }

def run_scenario(name, traces, partition_size=2048, history_size=16):
    sim = JITCacheSim(partition_size_bytes=partition_size, history_size=history_size)
    for tid, tsize in traces:
        sim.execute(tid, tsize)
    
    report = sim.get_report(jit_speedup=6.0)
    print(f"--- Scenario: {name} ---")
    print(f"Total Exec: {report['total_exec']}")
    print(f"Hit Rate:   {report['hit_rate_percent']:.2f}%")
    print(f"Est. Speedup (JIT=6x): {report['estimated_speedup']:.2f}x")
    print(f"Interp:     {report['interp_exec']}")
    print(f"Compiles:   {report['jit_compile']}")
    print(f"Evictions:  {report['evictions']}")
    print(f"Active/Old: {report['active_count']}/{report['old_count']}")
    print()

if __name__ == "__main__":
    # Average trace size 64 bytes
    AVG_SIZE = 64
    
    # 1. Tight Loop: 5 traces, 1000 iterations
    tight_loop = [(i % 5, AVG_SIZE) for i in range(5000)]
    run_scenario("Tight Loop (5 traces)", tight_loop)
    
    # 2. Large Loop: 60 traces (exceeds 2KB if 64 bytes each), 100 iterations
    # 60 * 64 = 3840 bytes > 2048 bytes
    large_loop = [(i % 60, AVG_SIZE) for i in range(6000)]
    run_scenario("Large Loop (60 traces, 16-entry history)", large_loop)

    # 2b. Large Loop with larger history
    print("Testing with 128-entry history for Large Loop...")
    sim_large_hist = JITCacheSim(partition_size_bytes=2048, history_size=128)
    for tid, tsize in large_loop:
        sim_large_hist.execute(tid, tsize)
    report = sim_large_hist.get_report()
    print(f"--- Scenario: Large Loop (60 traces, 128-entry history) ---")
    print(f"Hit Rate:   {report['hit_rate_percent']:.2f}%")
    print(f"Evictions:  {report['evictions']}")
    print(f"Active/Old: {report['active_count']}/{report['old_count']}")
    print()
    
    # 3. Branching: Path A (10 traces) vs Path B (10 traces)
    branching = []
    for _ in range(100):
        path = "A" if random.random() > 0.5 else "B"
        offset = 0 if path == "A" else 100
        branching.extend([(offset + i, AVG_SIZE) for i in range(10)])
    run_scenario("Branching (2 paths of 10 traces)", branching)
    
    # 4. Linear: 1000 unique traces
    linear = [(i, AVG_SIZE) for i in range(1000)]
    run_scenario("Linear (1000 unique traces)", linear)

    # 5. Thrashing: 40 traces (exceeds 2KB partition)
    # 40 * 64 = 2560 bytes > 2048 bytes.
    # We use a larger history (64) to ensure JIT triggers, so we can observe cache behavior.
    print("Testing Thrashing with 64-entry history...")
    thrashing = [(i % 40, AVG_SIZE) for i in range(4000)]
    sim_thrash = JITCacheSim(partition_size_bytes=2048, history_size=64)
    for tid, tsize in thrashing:
        sim_thrash.execute(tid, tsize)
    report = sim_thrash.get_report()
    print(f"--- Scenario: Thrashing (40 traces, 64-entry history) ---")
    print(f"Hit Rate:   {report['hit_rate_percent']:.2f}%")
    print(f"Evictions:  {report['evictions']}")
    print(f"Active/Old: {report['active_count']}/{report['old_count']}")
    print()

    # 6. Hot Path in Large Loop: 60 traces total, but 5 traces are "hot"
    hot_path_loop = []
    for _ in range(100):
        # 90% chance to run hot path (5 traces)
        if random.random() < 0.9:
            hot_path_loop.extend([(i, AVG_SIZE) for i in range(5)])
        else:
            # 10% chance to run the rest of the loop (55 traces)
            hot_path_loop.extend([(i, AVG_SIZE) for i in range(5, 60)])
    run_scenario("Hot Path in Large Loop (5 hot traces in 60 total)", hot_path_loop)
