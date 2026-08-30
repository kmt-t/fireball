"""
Fireball Experimental Python Simulation (pysim)
Modular multi-tier simulation of Fireball Hypervisor.
"""

import sys
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent

for p in [_PKG_ROOT, _PKG_ROOT / 'core', _PKG_ROOT / 'runtime', _PKG_ROOT / 'jit', _PKG_ROOT / 'platforms']:
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)
