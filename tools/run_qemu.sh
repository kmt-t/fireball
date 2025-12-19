#!/bin/bash

TARGET="${1:-cortex-m33}"
BINARY="${2:-./builddir/fireball}"

case "$TARGET" in
  cortex-m33)
    echo "Starting QEMU for Cortex-M33..."
    qemu-system-arm \
      -machine lm3s6965evb \
      -cpu cortex-m33 \
      -kernel "$BINARY" \
      -nographic \
      -serial stdio
    ;;
  riscv32)
    echo "Starting QEMU for RISC-V/32..."
    qemu-system-riscv32 \
      -machine virt \
      -cpu rv32 \
      -kernel "$BINARY" \
      -nographic \
      -serial stdio
    ;;
  *)
    echo "ERROR: Unknown target: $TARGET"
    echo "Usage: $0 {cortex-m33|riscv32} [binary]"
    exit 1
    ;;
esac
