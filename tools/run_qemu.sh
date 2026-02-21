#!/bin/bash

TARGET="${1:-cortex-m33}"
BINARY="${2:-./builddir/fireball}"

# Meson sanity check bypass
case "$BINARY" in
  *sanity*)
    # Meson sanity check binary. Return 0 to allow build to proceed.
    exit 0
    ;;
esac

case "$TARGET" in
  cortex-m33)
    echo "Starting QEMU for Cortex-M33 (mps2-an505)..."
    qemu-system-arm \
      -machine mps2-an505 \
      -cpu cortex-m33 \
      -m 16M \
      -kernel "$BINARY" \
      -nographic \
      -semihosting-config target=native
    ;;
  riscv32)
    echo "Starting QEMU for RISC-V/32..."
    qemu-system-riscv32 \
      -machine virt \
      -cpu rv32 \
      -kernel "$BINARY" \
      -nographic \
      -semihosting \
      -semihosting-config target=native
    ;;
  *)
    echo "ERROR: Unknown target: $TARGET"
    echo "Usage: $0 {cortex-m33|riscv32} [binary]"
    exit 1
    ;;
esac
