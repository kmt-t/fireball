# Rebuild the git-ignored native libraries when their sources change.
# Run from any directory: make -C experiments/pysim
SHELL := bash
NATIVE := native
ABI := tier2_runtime/abi
QA_PRIVATE := qa/private

COMMON_DEPS := tier1_core/config.py $(wildcard $(ABI)/*.hxx $(ABI)/*.py)
PRINTK_DEPS := $(wildcard $(NATIVE)/tier1_core/printk/*)
INTERP_DEPS := $(COMMON_DEPS) $(wildcard $(NATIVE)/tier2_runtime/interpreter/*)
JIT_DEPS := $(COMMON_DEPS) $(INTERP_DEPS) \
  $(shell find $(NATIVE)/tier3_plugins/jit ../../tools/jit_stencil_gen -type f -not -name '*.pyc')

LIB_PRINTK := tier1_core/libprintk.so
LIB_INTERP := tier2_runtime/interpreter/libnative_interpreter.so
LIB_INTERP_QA := $(QA_PRIVATE)/libinterpreter_probe.so
LIB_JIT := tier3_plugins/jit/libtrace_compiler.so
LIB_JIT_QA := $(QA_PRIVATE)/libjit_probe.so

QA_DEPS := $(wildcard $(QA_PRIVATE)/*.cxx $(QA_PRIVATE)/*.hxx $(QA_PRIVATE)/*.exports)

.PHONY: all native clean-native
all native: $(LIB_PRINTK) $(LIB_INTERP) $(LIB_INTERP_QA) $(LIB_JIT) $(LIB_JIT_QA)

$(LIB_PRINTK): $(PRINTK_DEPS)
	bash $(NATIVE)/tier1_core/printk/build_native.sh

$(LIB_INTERP): $(INTERP_DEPS)
	bash $(NATIVE)/tier2_runtime/interpreter/build_native.sh

$(LIB_INTERP_QA): $(INTERP_DEPS) $(QA_DEPS)
	bash $(NATIVE)/tier2_runtime/interpreter/build_native.sh --qa

$(LIB_JIT): $(JIT_DEPS)
	bash $(NATIVE)/tier3_plugins/jit/build_native.sh

$(LIB_JIT_QA): $(JIT_DEPS) $(QA_DEPS)
	bash $(NATIVE)/tier3_plugins/jit/build_native.sh --qa

# Force a full rebuild after switching revisions with unusual timestamps.
clean-native:
	rm -f $(LIB_PRINTK) $(LIB_INTERP) $(LIB_INTERP_QA) $(LIB_JIT) $(LIB_JIT_QA)
